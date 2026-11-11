"""Polling worker for proactive price monitors.

This is intentionally a poller, not a streaming quote feed. It uses the existing
market-data adapter (yfinance for US/HK, the existing Chinese sources for A
shares) and writes an in-app notification. No model is called by the worker;
the user decides whether to run a research task from the inbox.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
from typing import Any, Callable

from api.financial_bot.market_calendar import is_market_holiday, market_for_symbol, session_windows
from api.financial_bot.store import FinancialBotStore, utc_now

logger = logging.getLogger(__name__)


def _currency(symbol: str) -> str | None:
    upper = (symbol or "").upper()
    if upper.endswith(".SS") or upper.endswith(".SZ"):
        return "CNY"
    if upper.endswith(".HK"):
        return "HKD"
    return "USD"


def market_is_open(symbol: str, now: datetime | None = None) -> bool:
    market = market_for_symbol(symbol)
    zone = ZoneInfo({"cn": "Asia/Shanghai", "hk": "Asia/Hong_Kong"}.get(market, "America/New_York"))
    current = (now or datetime.now(timezone.utc)).astimezone(zone)
    if current.weekday() >= 5 or is_market_holiday(symbol, current.date()):
        return False
    return any(start <= current.time() <= end for start, end in session_windows(symbol, current.date()))


def in_quiet_hours(start: str | None, end: str | None, now: time | None = None) -> bool:
    try:
        start_time = time.fromisoformat((start or "")[:5])
        end_time = time.fromisoformat((end or "")[:5])
    except ValueError:
        return False
    current = now or datetime.now().time()
    return start_time <= current or current < end_time if start_time > end_time else start_time <= current < end_time


def fetch_announcement_snapshot(symbol: str) -> dict[str, Any] | None:
    from api.local_research import cninfo_announcements

    data = cninfo_announcements(symbol, limit=5)
    rows = data.get("announcements") or []
    if not rows:
        return None
    latest = rows[0]
    event_ref = str(latest.get("record_key") or latest.get("url") or f"{latest.get('date')}:{latest.get('title')}")
    return {
        "event_ref": event_ref,
        "title": f"{latest.get('date') or ''} · {latest.get('title') or '公告'}".strip(),
        "body": (
            f"{symbol} 出现新的巨潮公告：{latest.get('title') or '未命名公告'}。"
            f"类型 {latest.get('type') or '未知'}，日期 {latest.get('date') or '未知'}。"
            "该通知只包含公告标题和日期，正文尚未读取；是否需要研究由你确认。"
        ),
    }


def fetch_earnings_snapshot(symbol: str, days_before: int = 7) -> dict[str, Any] | None:
    from api.datasources.yfinance_provider import YFinanceProvider

    data = YFinanceProvider().calendar(symbol)
    raw_date = str(data.get("earnings_date") or "")[:10]
    try:
        earnings = date.fromisoformat(raw_date)
    except ValueError:
        return None
    days = (earnings - date.today()).days
    if days < 0 or days > max(0, min(60, int(days_before or 7))):
        return None
    return {
        "event_ref": f"earnings:{raw_date}",
        "title": f"{symbol} 财报日 · {raw_date}（还有 {days} 天）",
        "body": (
            f"{symbol} 的 Yahoo Finance 财报日历显示下一财报日为 {raw_date}，"
            f"距今天 {days} 天。财报日期可能调整，属于预估/延迟数据。"
            "该提醒不会自动调用模型，是否分析由你确认。"
        ),
    }


def fetch_watchlist_report(monitor: dict[str, Any]) -> dict[str, Any] | None:
    import api.main as main
    from ashare.data import DataFetcher

    snapshot = main.workspace_watchlist_store.get()
    items = snapshot.get("items") if isinstance(snapshot, dict) else []
    if not items:
        return None
    fetcher = DataFetcher(default_count=5)
    lines: list[str] = []
    limit = 6 if str(monitor.get("report_style") or "brief") == "brief" else 12
    for item in list(items)[:limit]:
        code = str(item.get("code") or "")
        name = str(item.get("name") or code)
        try:
            frame = fetcher.fetch_stock_data(code, count=5)
            closes = [float(value) for value in frame["close"].tolist() if value is not None] if frame is not None and not frame.empty else []
            if len(closes) >= 2 and closes[-2] != 0:
                change = (closes[-1] / closes[-2] - 1) * 100
                lines.append(f"{code} {name}：{closes[-1]:.2f} ({change:+.2f}%)")
            else:
                lines.append(f"{code} {name}：数据不足")
        except Exception:
            lines.append(f"{code} {name}：获取失败")
    if not lines:
        return None
    today = date.today().isoformat()
    period = "周报" if int(monitor.get("interval_minutes") or 0) >= 10080 else "日报"
    return {
        "event_ref": f"report:{period}:{today}",
        "title": f"自选股{period} · {today}",
        "body": "自选股摘要（轮询/延迟数据）：\n" + "\n".join(lines) + "\n该报告不构成投资建议。",
    }


def fetch_price_snapshot(symbol: str) -> dict[str, Any]:
    from ashare.data import DataFetcher

    frame = DataFetcher(default_count=10).fetch_stock_data(symbol, count=10)
    if frame is None or frame.empty or "close" not in frame:
        raise RuntimeError(f"no market data for {symbol}")
    closes = [float(value) for value in frame["close"].tolist() if value is not None]
    if len(closes) < 2:
        raise RuntimeError(f"not enough bars for {symbol}")
    price, previous = closes[-1], closes[-2]
    if previous == 0:
        raise RuntimeError(f"invalid previous close for {symbol}")
    return {
        "price": price,
        "change_pct": (price / previous - 1) * 100,
        "data_as_of": str(frame.index[-1])[:10],
        "currency": _currency(symbol),
    }


class MonitorRunner:
    def __init__(
        self,
        store: FinancialBotStore,
        *,
        interval_seconds: int = 60,
        fetcher: Callable[[str], dict[str, Any]] = fetch_price_snapshot,
    ):
        self.store = store
        self.interval_seconds = max(10, int(interval_seconds))
        self.fetcher = fetcher
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._running = False

    async def _dispatch_latest(self, monitor: dict[str, Any]) -> None:
        try:
            from api.financial_bot.dispatch import configured_results, dispatch_notification

            rows = await asyncio.to_thread(
                self.store.list_notifications,
                monitor["owner_id"],
                bot_id=monitor["bot_id"],
                limit=1,
            )
            if not rows:
                return
            bot = await asyncio.to_thread(self.store.get_bot, monitor["owner_id"], monitor["bot_id"])
            if str((bot or {}).get("digest_mode") or "immediate") == "daily":
                return
            channels = await asyncio.to_thread(self.store.get_bot_channels, monitor["owner_id"], monitor["bot_id"])
            if in_quiet_hours(channels.get("quiet_hours_start"), channels.get("quiet_hours_end")):
                return
            result = await asyncio.to_thread(
                dispatch_notification,
                rows[0],
                webhook_url=channels.get("webhook_url"),
                email_to=channels.get("notification_email"),
            )
            for channel, ok in configured_results(result, webhook_url=channels.get("webhook_url"), email_to=channels.get("notification_email")).items():
                await asyncio.to_thread(
                    self.store.record_delivery,
                    monitor["owner_id"],
                    bot_id=monitor["bot_id"],
                    notification_id=rows[0].get("id"),
                    channel=channel,
                    status="sent" if ok else "failed",
                    payload=rows[0],
                )
        except Exception:
            logger.exception("Outbound notification dispatch failed")

    async def run_once(self) -> dict[str, int]:
        if self._running:
            return {"checked": 0, "triggered": 0, "failed": 0}
        self._running = True
        checked = triggered = failed = 0
        try:
            monitors = await asyncio.to_thread(self.store.list_due_monitors)
            for monitor in monitors:
                if self._stopping:
                    break
                bot = await asyncio.to_thread(self.store.get_bot, monitor["owner_id"], monitor["bot_id"])
                bot_config = (bot or {}).get("prompt_config") or {}
                if bot_config.get("routines_enabled") is False or bot_config.get("proactive_enabled") is False:
                    continue
                muted_until = monitor.get("muted_until")
                if muted_until:
                    try:
                        if datetime.fromisoformat(str(muted_until).replace("Z", "+00:00")) > datetime.now(timezone.utc):
                            await asyncio.to_thread(self.store.mark_monitor_checked, monitor["owner_id"], monitor["id"], utc_now())
                            continue
                    except ValueError:
                        pass
                if monitor.get("condition") == "price" and bool((bot or {}).get("market_hours_only", True)):
                    market = market_for_symbol(monitor["symbol"])
                    today = datetime.now(timezone.utc).date().isoformat()
                    if await asyncio.to_thread(self.store.owner_market_closed, monitor["owner_id"], market, today):
                        continue
                    if not market_is_open(monitor["symbol"]):
                        continue
                max_alerts = int((bot or {}).get("max_alerts_per_day") or 20)
                sent_today = await asyncio.to_thread(self.store.count_notifications_today, monitor["owner_id"], monitor["bot_id"])
                if sent_today >= max_alerts:
                    continue
                checked += 1
                try:
                    if monitor.get("condition") == "report":
                        monitor = {**monitor, "report_style": (bot or {}).get("report_style") or "brief"}
                        event = await asyncio.to_thread(fetch_watchlist_report, monitor)
                        checked_at = utc_now()
                        if event is None:
                            await asyncio.to_thread(self.store.mark_monitor_checked, monitor["owner_id"], monitor["id"], checked_at)
                        else:
                            created = await asyncio.to_thread(
                                self.store.record_monitor_event,
                                monitor["owner_id"],
                                monitor["id"],
                                checked_at=checked_at,
                                event_ref=event["event_ref"],
                                title=event["title"],
                                body=event["body"],
                            )
                            if created:
                                triggered += 1
                                await self._dispatch_latest(monitor)
                        continue
                    if monitor.get("condition") == "earnings":
                        event = await asyncio.to_thread(fetch_earnings_snapshot, monitor["symbol"], int(monitor.get("threshold") or 7))
                        checked_at = utc_now()
                        if event is None:
                            await asyncio.to_thread(self.store.mark_monitor_checked, monitor["owner_id"], monitor["id"], checked_at)
                        else:
                            created = await asyncio.to_thread(
                                self.store.record_monitor_event,
                                monitor["owner_id"],
                                monitor["id"],
                                checked_at=checked_at,
                                event_ref=event["event_ref"],
                                title=event["title"],
                                body=event["body"],
                            )
                            if created:
                                triggered += 1
                                await self._dispatch_latest(monitor)
                        continue
                    if monitor.get("condition") == "announcement":
                        event = await asyncio.to_thread(fetch_announcement_snapshot, monitor["symbol"])
                        checked_at = utc_now()
                        if event is None:
                            await asyncio.to_thread(self.store.mark_monitor_checked, monitor["owner_id"], monitor["id"], checked_at)
                        else:
                            created = await asyncio.to_thread(
                                self.store.record_monitor_event,
                                monitor["owner_id"],
                                monitor["id"],
                                checked_at=checked_at,
                                event_ref=event["event_ref"],
                                title=event["title"],
                                body=event["body"],
                            )
                            if created:
                                triggered += 1
                                await self._dispatch_latest(monitor)
                        continue
                    quote = await asyncio.to_thread(self.fetcher, monitor["symbol"])
                    result = await asyncio.to_thread(
                        self.store.record_monitor_check,
                        monitor["owner_id"],
                        monitor["id"],
                        checked_at=utc_now(),
                        price=quote.get("price"),
                        change_pct=quote.get("change_pct"),
                        data_as_of=quote.get("data_as_of"),
                        currency=quote.get("currency"),
                    )
                    if result and result.get("triggered"):
                        triggered += 1
                        await self._dispatch_latest(monitor)
                except Exception as exc:  # noqa: BLE001 - one monitor must not stop the worker
                    failed += 1
                    logger.warning("Monitor check failed for %s: %s", monitor.get("symbol"), exc)
                    try:
                        await asyncio.to_thread(
                            self.store.record_monitor_check,
                            monitor["owner_id"],
                            monitor["id"],
                            checked_at=utc_now(),
                            error=str(exc),
                        )
                    except Exception:
                        logger.exception("Could not record monitor failure")
            return {"checked": checked, "triggered": triggered, "failed": failed}
        finally:
            self._running = False

    async def run_digests(self) -> int:
        """Send one external digest per due Bot; the inbox is unchanged."""
        sent = 0
        now = datetime.now().astimezone()
        today = now.date().isoformat()
        for bot in await asyncio.to_thread(self.store.list_digest_bots):
            hour = str(bot.get("digest_hour") or "18:00")
            try:
                hour_value = int(hour[:2])
            except ValueError:
                hour_value = 18
            if now.hour < hour_value or str(bot.get("last_digest_date") or "") == today:
                continue
            notifications = await asyncio.to_thread(
                self.store.list_notifications,
                bot["owner_id"],
                bot_id=bot["id"],
                unread_only=False,
                limit=50,
            )
            unread = [item for item in notifications if item.get("status") != "read"]
            if not unread:
                await asyncio.to_thread(self.store.mark_digest_sent, bot["owner_id"], bot["id"], today)
                continue
            channels = await asyncio.to_thread(self.store.get_bot_channels, bot["owner_id"], bot["id"])
            if in_quiet_hours(channels.get("quiet_hours_start"), channels.get("quiet_hours_end")):
                continue
            lines = [f"- {item.get('title')}: {str(item.get('body') or '')[:180]}" for item in unread[:20]]
            digest = {
                "title": f"Financial Bot 每日摘要 · {today}",
                "body": "\n".join(lines),
                "bot_id": bot["id"],
                "severity": "info",
                "created_at": utc_now(),
            }
            from api.financial_bot.dispatch import configured_results, dispatch_notification

            result = await asyncio.to_thread(
                dispatch_notification,
                digest,
                webhook_url=channels.get("webhook_url"),
                email_to=channels.get("notification_email"),
            )
            for channel, ok in configured_results(result, webhook_url=channels.get("webhook_url"), email_to=channels.get("notification_email")).items():
                await asyncio.to_thread(
                    self.store.record_delivery,
                    bot["owner_id"],
                    bot_id=bot["id"],
                    notification_id=None,
                    channel=f"digest_{channel}",
                    status="sent" if ok else "failed",
                    payload=digest,
                )
            if any(result.values()) or not (channels.get("webhook_url") or channels.get("notification_email")):
                await asyncio.to_thread(self.store.mark_digest_sent, bot["owner_id"], bot["id"], today)
                sent += 1
        return sent

    async def run_retries(self) -> int:
        """Retry failed external deliveries with the scheduled backoff."""
        from api.financial_bot.dispatch import dispatch_channel

        retried = 0
        for delivery in await asyncio.to_thread(self.store.list_retry_deliveries):
            try:
                payload = json.loads(delivery.get("payload_json") or "{}")
                if not isinstance(payload, dict) or not payload:
                    await asyncio.to_thread(self.store.update_delivery, delivery["owner_id"], delivery["id"], status="failed", error="payload missing")
                    continue
                channels = await asyncio.to_thread(self.store.get_bot_channels, delivery["owner_id"], delivery["bot_id"])
                ok = await asyncio.to_thread(
                    dispatch_channel,
                    payload,
                    delivery.get("channel") or "",
                    webhook_url=channels.get("webhook_url"),
                    email_to=channels.get("notification_email"),
                )
                await asyncio.to_thread(
                    self.store.update_delivery,
                    delivery["owner_id"],
                    delivery["id"],
                    status="sent" if ok else "failed",
                    error=None if ok else "retry failed",
                )
                retried += 1
            except Exception:
                logger.exception("Delivery retry failed")
        return retried

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.run_once()
                await self.run_digests()
                await self.run_retries()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Monitor loop failed")
            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                raise

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
