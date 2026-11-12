"""Four local-only data adapters: CNINFO announcements, THS consensus EPS,
CSI/CNI index constituents, and Sina A-share financial statements.

All are deliberately narrow:
* no arbitrary URL is accepted from the model or browser;
* only validated A-share/index codes are accepted;
* the raw third-party response is not persisted (only its SHA-256);
* failures are explicit and never converted into an empty successful result.

The endpoint shapes and field semantics were checked against Vibe-Research
(db3a7c6). See docs/vibe-research-data-sources.md for the source/licensing review.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from io import StringIO
from threading import Lock
from typing import Any

import requests

from ashare.stock_pool import extract_symbol, normalize_stock_code

TZ_SH = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)
CNINFO_ORG_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
THS_WORTH_URL = "https://basic.10jqka.com.cn/new/{digits}/worth.html"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
_ORG_CACHE: dict[str, str] = {}
_ORG_LOCK = Lock()


class LocalDataError(RuntimeError):
    """A local research source failed or has no usable data."""


def _normalized_a_share(stock_code: str) -> tuple[str, str]:
    text = str(stock_code or "").strip()
    if re.fullmatch(r"\d{6}", text):
        # Bare six-digit A-share code: 6/9 = Shanghai, 0/3 = Shenzhen.
        text = ("sh" if text[0] in "69" else "sz") + text
    normalized = normalize_stock_code(text)
    lower = normalized.lower()
    if not re.fullmatch(r"(sh|sz)\d{6}", lower):
        raise LocalDataError(f"仅支持沪深 A 股代码，收到 {stock_code!r}")
    return normalized, extract_symbol(normalized)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _now() -> str:
    return datetime.now(TZ_SH).isoformat(timespec="seconds")


def _cninfo_org_id(digits: str) -> str:
    global _ORG_CACHE
    with _ORG_LOCK:
        if not _ORG_CACHE:
            response = requests.get(
                CNINFO_ORG_URL,
                headers={"User-Agent": UA},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            _ORG_CACHE = {
                str(item.get("code")): str(item.get("orgId"))
                for item in (payload or {}).get("stockList", [])
                if item.get("code") and item.get("orgId")
            }
            if not _ORG_CACHE:
                raise LocalDataError("巨潮 orgId 映射为空，接口可能已变更")
        org_id = _ORG_CACHE.get(digits)
        if not org_id:
            raise LocalDataError(f"巨潮映射表未收录 {digits}，拒绝猜测 orgId")
        return org_id


def cninfo_announcements(stock_code: str, limit: int = 8) -> dict[str, Any]:
    """Recent official CNINFO announcements for a Shanghai/Shenzhen stock."""
    normalized, digits = _normalized_a_share(stock_code)
    limit = max(1, min(20, int(limit)))
    org_id = _cninfo_org_id(digits)
    form = {
        "stock": f"{digits},{org_id}",
        "tabName": "fulltext",
        "pageSize": str(limit),
        "pageNum": "1",
        "column": "",
        "category": "",
        "plate": "",
        "seDate": "",
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    response = requests.post(
        CNINFO_QUERY_URL,
        data=form,
        headers={
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.cninfo.com.cn/new/disclosure",
            "Origin": "https://www.cninfo.com.cn",
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    rows = []
    for item in (payload or {}).get("announcements", []) or []:
        timestamp = item.get("announcementTime")
        try:
            date = datetime.fromtimestamp(float(timestamp) / 1000, TZ_SH).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            date = str(timestamp or "")[:10]
        announcement_id = item.get("announcementId")
        rows.append(
            {
                "title": str(item.get("announcementTitle") or "").strip(),
                "type": str(item.get("announcementTypeName") or "").strip(),
                "date": date,
                "url": (
                    f"https://www.cninfo.com.cn/new/disclosure/detail?stockCode={digits}&announcementId={announcement_id}"
                    if announcement_id else None
                ),
                "record_key": str(announcement_id or ""),
            }
        )
    rows = [row for row in rows if row["title"]]
    return {
        "symbol": normalized,
        "market": "CN",
        "provider": "cninfo",
        "endpoint": "cninfo.com.cn/new/hisAnnouncement/query",
        "retrieved_at": _now(),
        "data_as_of": rows[0]["date"] if rows else None,
        "currency": None,
        "adjustment": "not_applicable",
        "announcements": rows,
        "raw_sha256": _sha256(response.content),
        "raw_ref": None,
        "raw_retained": False,
        "data_gaps": [] if rows else ["巨潮公告返回 0 条；这不等于没有公告。"],
        "note": "官方公告标题/链接；正文未读取。仅限本机内部研究。",
    }


def eastmoney_announcements(stock_code: str, limit: int = 8) -> dict[str, Any]:
    """Eastmoney A-share announcement fallback when CNINFO is unavailable.

    This is a secondary aggregator, not the official disclosure.  Callers must
    preserve the provider label and the explicit provenance gap.
    """
    normalized, digits = _normalized_a_share(stock_code)
    limit = max(1, min(20, int(limit)))
    try:
        import akshare as ak
    except ImportError as exc:
        raise LocalDataError("akshare is not installed; Eastmoney announcement fallback unavailable") from exc
    end = datetime.now(TZ_SH)
    start = end - timedelta(days=120)
    df = ak.stock_individual_notice_report(
        security=digits,
        symbol="全部",
        begin_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    if df is None or getattr(df, "empty", True):
        raise LocalDataError("东方财富公告接口返回空数据")
    rows = []
    for item in df.head(limit).to_dict("records"):
        title = str(item.get("公告标题") or "").strip()
        if not title:
            continue
        date = item.get("公告日期")
        if hasattr(date, "strftime"):
            date = date.strftime("%Y-%m-%d")
        else:
            date = str(date or "")[:10]
        url = str(item.get("网址") or "").strip() or None
        key = hashlib.sha256(f"{normalized}|{date}|{title}|{url}".encode("utf-8")).hexdigest()[:20]
        rows.append({
            "title": title,
            "type": str(item.get("公告类型") or "").strip(),
            "date": date,
            "url": url,
            "record_key": key,
        })
    if not rows:
        raise LocalDataError("东方财富公告没有可用标题")
    return {
        "symbol": normalized,
        "market": "CN",
        "provider": "eastmoney_announcements",
        "endpoint": "data.eastmoney.com/notices/stock/{digits}",
        "retrieved_at": _now(),
        "data_as_of": rows[0]["date"] or None,
        "currency": None,
        "adjustment": "not_applicable",
        "announcements": rows,
        "raw_sha256": hashlib.sha256("".join(row["record_key"] for row in rows).encode("utf-8")).hexdigest(),
        "raw_ref": None,
        "raw_retained": False,
        "data_gaps": ["东方财富公告是二级聚合源，不能替代巨潮官方披露；请以公告原文为准。"],
        "note": "CNINFO unavailable; Eastmoney announcement fallback. Titles/links only.",
    }


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(str(value).replace(",", "").strip())
        return number
    except (TypeError, ValueError):
        return None


_EM_FORECAST_CACHE: dict[str, Any] = {}
_EM_FORECAST_LOCK = Lock()
_EM_FORECAST_TTL = 3600.0


def _eastmoney_consensus_estimates(digits: str) -> dict[str, Any]:
    """Eastmoney consensus EPS fallback (secondary aggregator).

    The public forecast table gives a consensus EPS and report count, but not
    analyst-level min/max.  Those missing fields stay explicit gaps.
    """
    import time
    import akshare as ak

    with _EM_FORECAST_LOCK:
        cached = _EM_FORECAST_CACHE.get("all")
        if cached and time.time() - cached[0] < _EM_FORECAST_TTL:
            frame = cached[1]
        else:
            frame = ak.stock_profit_forecast_em(symbol="")
            _EM_FORECAST_CACHE["all"] = (time.time(), frame)
    if frame is None or getattr(frame, "empty", True):
        raise LocalDataError("东方财富盈利预测返回空表")
    code_column = "代码" if "代码" in frame.columns else None
    if code_column is None:
        raise LocalDataError("东方财富盈利预测缺少代码列")
    matched = frame[frame[code_column].astype(str).str.zfill(6) == digits]
    if matched.empty:
        raise LocalDataError("东方财富没有该股票的一致预期")
    row = matched.iloc[0]
    years = []
    for column in frame.columns:
        text = str(column)
        match = re.fullmatch(r"(\d{4})预测每股收益", text)
        if not match:
            continue
        value = _to_float(row.get(column))
        if value is None:
            continue
        years.append({
            "period": f"FY{match.group(1)}",
            "mean": value,
            "min": None,
            "max": None,
            "count": int(_to_float(row.get("研报数"))) if _to_float(row.get("研报数")) is not None else None,
        })
    if not any(item["mean"] is not None for item in years):
        raise LocalDataError("东方财富一致预期没有 EPS 数值")
    gaps = [
        "东方财富盈利预测是二级聚合源，不能替代同花顺原始机构明细。",
        "东方财富快照只给一致 EPS 与研报数，缺少机构级最小/最大值。",
    ]
    for item in years:
        if item["count"] is not None and item["count"] < 3:
            gaps.append(f"{item['period']} 仅 {item['count']} 家机构，覆盖不足")
    return {
        "symbol": f"sh{digits}" if digits.startswith("6") else f"sz{digits}",
        "market": "CN",
        "provider": "eastmoney_forecast",
        "endpoint": "data.eastmoney.com/report/profitforecast.jshtml",
        "retrieved_at": _now(),
        "data_as_of": datetime.now(TZ_SH).strftime("%Y-%m-%d"),
        "currency": "CNY",
        "adjustment": "not_applicable",
        "years": years,
        "ratings": {
            "buy": _to_float(row.get("机构投资评级(近六个月)-买入")),
            "overweight": _to_float(row.get("机构投资评级(近六个月)-增持")),
            "neutral": _to_float(row.get("机构投资评级(近六个月)-中性")),
            "underweight": _to_float(row.get("机构投资评级(近六个月)-减持")),
            "sell": _to_float(row.get("机构投资评级(近六个月)-卖出")),
        },
        "raw_sha256": hashlib.sha256("|".join(f"{item['period']}={item['mean']}" for item in years).encode("utf-8")).hexdigest(),
        "raw_ref": None,
        "raw_retained": False,
        "data_gaps": gaps,
        "note": "Eastmoney consensus EPS fallback; secondary aggregator, no min/max. Local research only.",
    }


def consensus_estimates(stock_code: str) -> dict[str, Any]:
    """THS consensus EPS, with Eastmoney fallback when THS is unavailable."""
    normalized, digits = _normalized_a_share(stock_code)
    try:
        return _consensus_estimates_ths(normalized, digits)
    except Exception as exc:
        logger.warning("THS consensus failed for %s: %s; trying Eastmoney", normalized, type(exc).__name__)
        try:
            return _eastmoney_consensus_estimates(digits)
        except Exception as fallback_exc:
            raise LocalDataError(
                f"同花顺和东方财富一致预期均不可用：{type(fallback_exc).__name__}"
            ) from fallback_exc


def _consensus_estimates_ths(normalized: str, digits: str) -> dict[str, Any]:
    """THS FY consensus EPS with institution count and min/max."""
    response = requests.get(
        THS_WORTH_URL.format(digits=digits),
        headers={"User-Agent": UA, "Referer": "https://basic.10jqka.com.cn/"},
        timeout=20,
    )
    response.raise_for_status()
    response.encoding = "gbk"
    import pandas as pd

    try:
        tables = pd.read_html(StringIO(response.text))
    except ValueError as exc:
        raise LocalDataError("同花顺页面没有可解析的表格") from exc
    table = None
    for frame in tables:
        columns = {str(column) for column in frame.columns}
        if {"年度", "预测机构数", "均值"}.issubset(columns):
            table = frame
            break
    if table is None or table.empty:
        raise LocalDataError("同花顺页面没有一致预期表（可能无机构覆盖）")
    years = []
    for _, row in table.iterrows():
        year = str(row.get("年度", "")).strip()[:4]
        if not year.isdigit():
            continue
        count = _to_float(row.get("预测机构数"))
        years.append(
            {
                "period": f"FY{year}",
                "mean": _to_float(row.get("均值")),
                "min": _to_float(row.get("最小值")),
                "max": _to_float(row.get("最大值")),
                "count": int(count) if count is not None else None,
            }
        )
    if not any(row["mean"] is not None for row in years):
        raise LocalDataError("同花顺表格解析后没有均值")
    gaps = []
    current = datetime.now(TZ_SH).year
    present = {row["period"] for row in years}
    for year in (current, current + 1, current + 2):
        if f"FY{year}" not in present:
            gaps.append(f"缺少 FY{year} 一致预期")
    for row in years:
        if row["count"] is not None and row["count"] < 3:
            gaps.append(f"{row['period']} 仅 {row['count']} 家机构，覆盖不足")
        if row["min"] is None or row["max"] is None:
            gaps.append(f"{row['period']} 缺少最小值/最大值")
    return {
        "symbol": normalized,
        "market": "CN",
        "provider": "ths",
        "endpoint": "basic.10jqka.com.cn/new/{digits}/worth.html",
        "retrieved_at": _now(),
        "data_as_of": datetime.now(TZ_SH).strftime("%Y-%m-%d"),
        "currency": "CNY",
        "adjustment": "not_applicable",
        "years": years,
        "raw_sha256": _sha256(response.content),
        "raw_ref": None,
        "raw_retained": False,
        "data_gaps": gaps,
        "note": "一致预期 EPS（元/股），机构覆盖不足时不得当作可靠预期。仅限本机内部研究。",
    }


def index_constituents(index_code: str, provider: str = "csi") -> dict[str, Any]:
    """Official CSI/CNI index constituents and weights (single published snapshot)."""
    code = str(index_code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise LocalDataError(f"指数代码必须是 6 位数字，收到 {index_code!r}")
    provider = (provider or "csi").strip().lower()
    if provider not in {"csi", "cni"}:
        raise LocalDataError("provider 只能是 csi（中证）或 cni（国证）")
    if provider == "csi":
        url = (
            "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/"
            f"autofile/closeweight/{code}closeweight.xls"
        )
        response = requests.get(url, headers={"User-Agent": UA, "Referer": url}, timeout=(10, 40))
        response.raise_for_status()
        source_name = "csindex"
        source_label = "中证指数"
    else:
        url = "https://www.cnindex.com.cn/sample-detail/download-history"
        response = requests.get(
            url,
            params={"indexcode": code},
            headers={"User-Agent": UA, "Referer": url},
            timeout=(10, 40),
        )
        response.raise_for_status()
        source_name = "cnindex"
        source_label = "国证指数"
    import pandas as pd
    from io import BytesIO

    try:
        frame = pd.read_excel(BytesIO(response.content), dtype=str)
    except (ValueError, OSError) as exc:
        raise LocalDataError(f"{source_label}成分文件不是可解析的 Excel；可能未发布或结构已变") from exc
    frame.columns = [re.sub(r"\s+", "", str(column)) for column in frame.columns]
    if provider == "csi":
        required = ["日期Date", "指数代码IndexCode", "成份券代码ConstituentCode", "成份券名称ConstituentName", "交易所Exchange", "权重(%)weight"]
        missing = [column for column in required if column not in frame.columns]
        if missing:
            raise LocalDataError("中证成分文件缺列: " + ", ".join(missing))
        records = []
        dates = set()
        for _, row in frame.iterrows():
            if str(row["指数代码IndexCode"]).zfill(6) != code:
                raise LocalDataError("中证返回了不同指数的成分")
            exchange = {"上海证券交易所": "SH", "深圳证券交易所": "SZ", "北京证券交易所": "BJ"}.get(str(row["交易所Exchange"]))
            if exchange is None:
                continue
            date_raw = str(row["日期Date"]).strip()
            date = date_raw if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_raw) else f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
            dates.add(date)
            records.append({
                "code": f"{exchange}{str(row['成份券代码ConstituentCode']).zfill(6)}",
                "name": str(row["成份券名称ConstituentName"]).strip(),
                "exchange": exchange,
                "weight_percent": _to_float(row["权重(%)weight"]),
            })
    else:
        required = ["日期", "样本代码", "样本简称", "权重（%）"]
        missing = [column for column in required if column not in frame.columns]
        if missing:
            raise LocalDataError("国证成分文件缺列: " + ", ".join(missing))
        records = []
        dates = set()
        for _, row in frame.iterrows():
            date = str(row["日期"]).strip()
            dates.add(date)
            raw_code = str(row["样本代码"]).strip()
            if not re.fullmatch(r"\d{6}", raw_code):
                continue
            exchange = "SH" if raw_code.startswith(("6", "9")) else "SZ" if raw_code.startswith(("0", "3")) else "BJ" if raw_code.startswith(("4", "8")) else None
            if exchange is None:
                continue
            records.append({
                "code": f"{exchange}{raw_code}",
                "name": str(row["样本简称"]).strip(),
                "exchange": exchange,
                "weight_percent": _to_float(row["权重（%）"]),
            })
    if not records or len(dates) != 1:
        raise LocalDataError(f"{source_label}成分文件为空或混有多个日期")
    total = sum(row["weight_percent"] or 0 for row in records)
    if not 99 <= total <= 101:
        raise LocalDataError(f"{source_label}权重合计 {total:.2f}% 异常")
    records.sort(key=lambda row: row["weight_percent"] or 0, reverse=True)
    return {
        "index_code": code,
        "provider": source_name,
        "provider_label": source_label,
        "endpoint": getattr(response, "url", url),
        "data_as_of": next(iter(dates)),
        "retrieved_at": _now(),
        "currency": None,
        "adjustment": "not_applicable",
        "member_count": len(records),
        "weight_total_percent": round(total, 4),
        "members": records,
        "raw_sha256": _sha256(response.content),
        "raw_ref": None,
        "raw_retained": False,
        "data_gaps": [] if records else [f"{source_label}成分文件为空"],
        "note": f"{source_label}官网最近公布的单日成分权重快照；不是实时指数篮子。仅限本机内部研究。",
    }


_FINANCIAL_REPORTS = {
    "income": ("lrb", "利润表", "净利润", {
        "营业总收入": "revenue_total",
        "营业收入": "revenue",
        "营业利润": "operating_profit",
        "利润总额": "total_profit",
        "净利润": "net_profit",
        "归属于母公司所有者的净利润": "net_profit_parent",
        "基本每股收益": "eps_basic",
    }),
    "balance": ("fzb", "资产负债表", "资产总计", {
        "资产总计": "total_assets",
        "负债合计": "total_liabilities",
        "所有者权益(或股东权益)合计": "total_equity",
        "归属于母公司股东权益合计": "equity_parent",
        "货币资金": "cash",
        "存货": "inventory",
        "应收账款": "accounts_receivable",
        "流动资产合计": "current_assets",
        "流动负债合计": "current_liabilities",
    }),
    "cashflow": ("llb", "现金流量表", "经营活动产生的现金流量净额", {
        "经营活动产生的现金流量净额": "cfo",
        "投资活动产生的现金流量净额": "cfi",
        "筹资活动产生的现金流量净额": "cff",
        "现金及现金等价物净增加额": "net_cash_change",
    }),
}


def financial_statements(stock_code: str, statement: str = "income", periods: int = 8) -> dict[str, Any]:
    """A-share financials with a persistent last-good fallback.

    Live Fuyao/Sina remains primary.  If both fail, a previously successful
    normalized statement is returned with ``stale=true`` and an explicit gap;
    callers must not present it as a live/reported-now value.
    """
    normalized, _digits = _normalized_a_share(stock_code)
    try:
        data = _financial_statements_live(stock_code, statement, periods)
        try:
            from api.datasources.last_good import put

            put(
                "financial_statements",
                f"{normalized}:{statement}",
                data,
                provider=str(data.get("provider") or "unknown"),
                data_as_of=data.get("data_as_of"),
                adjustment=str(data.get("adjustment") or "not_applicable"),
            )
        except Exception:
            logger.debug("Financial last-good write skipped for %s", normalized, exc_info=True)
        return data
    except Exception:
        try:
            from api.datasources.last_good import get

            max_age = float(os.getenv("LAST_GOOD_FINANCIALS_MAX_AGE_HOURS", "4320"))
            cached = get("financial_statements", f"{normalized}:{statement}", max_age_hours=max_age)
            if cached and isinstance(cached.get("value"), dict):
                stale = dict(cached["value"])
                rows = list(stale.get("periods") or [])
                if rows:
                    stale["periods"] = rows[: max(1, min(8, int(periods)))]
                    from api.datasources.validation import validate_financial_periods

                    if validate_financial_periods(stale["periods"]):
                        raise LocalDataError("last-good 财报缓存结构不完整")
                    stale["stale"] = True
                    stale["cache_provider"] = cached.get("provider")
                    stale["cache_stored_at"] = cached.get("stored_at")
                    stale["retrieved_at"] = cached.get("stored_at") or stale.get("retrieved_at")
                    stale["data_gaps"] = [
                        *(stale.get("data_gaps") or []),
                        f"实时财报源不可用；返回 last-good 缓存（原来源 {cached.get('provider')}，截至 {cached.get('data_as_of') or '未知'}）；缓存可能早于最新财报重述。",
                    ]
                    stale["note"] = "LAST-GOOD CACHE — not a live financial statement refresh. " + str(stale.get("note") or "")
                    return stale
        except Exception:
            logger.debug("Financial last-good read skipped for %s", normalized, exc_info=True)
        raise


def _financial_statements_live(stock_code: str, statement: str = "income", periods: int = 8) -> dict[str, Any]:
    """Live A-share financial statement key items, preserving reported periods.

    Fuyao is the primary source when ``FUYAO_API_KEY`` and the financials flag
    are configured; the existing Sina endpoint remains the fallback. Both paths
    return the same period/items envelope, so downstream cards do not branch.
    """
    normalized, digits = _normalized_a_share(stock_code)
    try:
        from api.datasources.flags import fuyao_financials

        if fuyao_financials():
            from api.datasources.fuyao_provider import FuyaoNotConfigured, get_fuyao_provider

            provider = get_fuyao_provider()
            if provider.configured:
                try:
                    return provider.financial_statements(stock_code, statement, periods)
                except FuyaoNotConfigured:
                    pass
                except Exception as exc:
                    logger.warning("Fuyao financials failed for %s: %s; falling back to Sina", normalized, type(exc).__name__)
    except Exception:
        logger.debug("Fuyao financials feature check failed", exc_info=True)
    if statement not in _FINANCIAL_REPORTS:
        raise LocalDataError("statement 只能是 income / balance / cashflow")
    report_type, label, _focus, mapping = _FINANCIAL_REPORTS[statement]
    periods = max(1, min(8, int(periods)))
    params = {
        "paperCode": f"{normalized[:2]}{digits}",
        "source": report_type,
        "type": "0",
        "page": "1",
        "num": str(periods),
    }
    response = requests.get(
        "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022",
        params=params,
        headers={"User-Agent": UA},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    report_list = (((payload or {}).get("result") or {}).get("data") or {}).get("report_list") or {}
    rows = []
    for period in sorted(report_list.keys(), reverse=True)[:periods]:
        record = report_list[period] or {}
        items: dict[str, float | None] = {}
        raw_items = {}
        for item in record.get("data", []) or []:
            title = str(item.get("item_title") or "").strip()
            if title in mapping:
                items[mapping[title]] = _to_float(item.get("item_value"))
                raw_items[title] = item.get("item_value")
        if not items:
            continue
        rows.append({
            "period": f"{period[:4]}-{period[4:6]}-{period[6:8]}",
            "items": items,
        })
    if not rows:
        raise LocalDataError(f"新浪{label}没有该股票的可用报告期")
    gaps = []
    if len(rows) < periods:
        gaps.append(f"仅返回 {len(rows)} 个报告期，少于请求的 {periods} 个")
    if all(value is None for row in rows for value in row["items"].values()):
        raise LocalDataError(f"新浪{label}返回了报告期但关键科目全为空")
    return {
        "symbol": normalized,
        "market": "CN",
        "provider": "sina",
        "endpoint": "quotes.sina.cn CompanyFinanceService.getFinanceReport2022",
        "statement": statement,
        "statement_label": label,
        "report_type": report_type,
        "data_as_of": rows[0]["period"],
        "retrieved_at": _now(),
        "currency": "CNY",
        "adjustment": "not_applicable",
        "periods": rows,
        "raw_sha256": _sha256(response.content),
        "raw_ref": None,
        "raw_retained": False,
        "data_gaps": gaps,
        "note": f"新浪财报三表（{label}）关键科目原值；累计值口径，不做单季拆分。仅限本机内部研究。",
    }
