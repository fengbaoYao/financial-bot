"""HTTP routes for Financial Bot.

Mounted by ``api.main`` under ``/api``. Every route resolves the owner on the
server and passes it to owner-scoped repository methods. No route accepts an
owner identifier from the client.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import uuid
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from api.financial_bot import runtime
from api.financial_bot.auth import OwnerContext, resolve_owner
from api.financial_bot.memory_extract import extract_explicit_memories
from api.financial_bot.models import (
    BotCreateRequest,
    BotOnboardingRequest,
    BotPatchRequest,
    BotPromptPreviewRequest,
    CandidateConfirmRequest,
    CandidateDismissRequest,
    DeleteAllRequest,
    MemoryPatchRequest,
    MarketClosureCreateRequest,
    MonitorCreateRequest,
    MonitorMuteRequest,
    MonitorPatchRequest,
    NotificationAnalyzeRequest,
    OpenMuseSharedResearchRequest,
    TaskCreateRequest,
    ThreadCreateRequest,
    WelcomeCreateRequest,
)
from api.financial_bot.prompt import (
    DEFAULT_PROMPT_CONFIG,
    DEFAULT_PROMPT_TEMPLATE,
    KNOWN_VARIABLES,
    PROMPT_TEMPLATE_VERSION,
    VARIABLE_LABELS,
    prompt_config_from_bot,
    render_bot_prompt,
    validate_prompt_template,
)
from api.financial_bot.store import ConflictError, LimitError, NotFoundError
from ashare.config import Config
from api.datasources.ifind_provider import history_quota_exhausted, history_quota_user_note
from api.datasources.flags import (
    fuyao_enabled,
    fuyao_financials,
    fuyao_primary_a_share,
    ifind_enabled,
    ifind_history_enabled,
    ifind_primary,
    yfinance_a_share_fallback,
    yfinance_primary_us_hk,
    yfinance_us_hk_fundamentals,
)
from api.datasources.shadow import shadow_dir, shadow_enabled
from api.datasources import health as provider_health
from api.datasources import last_good as last_good_cache
from api.datasources import quality as source_quality
from api.financial_bot.executor import STOP_STATUSES
from api.financial_bot.openmuse_client import OpenMuseClient

router = APIRouter(prefix="/api", tags=["financial-bot"])

TERMINAL = {"completed", "partial", "failed", "cancelled", "unknown"}
_FINANCIAL_RESEARCH_TERMS = re.compile(
    r"(?:股价|股票|证券|行情|财报|财务|估值|市值|分红|股息|基金|持仓|投资|债券|利率|外汇|期权|期货|A股|港股|美股|上市公司|公司财务|公司|企业|盈利|收入|营收|利润|业绩|增长|现金流|回报率|"
    r"stock|shares?|equities|ticker|quote|earnings|valuation|market\s*cap|investment|investing|invest|portfolio|ETF|dividend|financial\s+statement|SEC\s+filing|IPO|bond|forex|options?|company|revenue|profit|profitability|performance|growth|cash\s*flow|returns?|"
    r"long[ -]term|buy\s+and\s+hold|hold\s+(?:it|this|for)|should\s+i\s+(?:buy|sell|invest)|长期持有|长期投资|买入|卖出|"
    r"(?<!\d)(?:[036]\d{5}|\d{5})(?!\d)|(?<![A-Za-z])(?-i:[A-Z]{2,5}(?:\.[A-Z])?)(?![A-Za-z]))",
    re.IGNORECASE,
)


def _is_financial_research_question(query: str) -> bool:
    if _FINANCIAL_RESEARCH_TERMS.search(query):
        return True
    # Company-specific outlook, moat, and competitive-position questions often
    # omit explicit words such as "stock" or "financial". Use the same local
    # alias and security-code resolver as source-only research so short names
    # like "微软" and "小米" are not missed. This performs no network request.
    try:
        from api.financial_bot.fallback_research import find_company

        if find_company(query):
            return True
    except Exception:
        pass
    try:
        from ashare.search import stock_searcher

        return any(
            len(str(name)) >= 3 and str(name).casefold() in query.casefold()
            for name in stock_searcher.base_stock_db
        )
    except Exception:
        return False


def _owner(request: Request) -> OwnerContext:
    from api.main import supabase_config

    return resolve_owner(request, supabase_config)


def _store():
    return runtime.get_store()


def _executor():
    return runtime.get_executor()


def _task_public(task: dict[str, Any]) -> dict[str, Any]:
    data = dict(task)
    data.pop("owner_id", None)
    data.pop("idempotency_key", None)
    data.pop("status_reason", None)
    return data


def _event_public(event: dict[str, Any]) -> dict[str, Any]:
    data = dict(event)
    data.pop("owner_id", None)
    return data


def _error(status: int, detail: Any, **extra: Any) -> JSONResponse:
    payload = {"detail": detail, **extra}
    return JSONResponse(payload, status_code=status)


def _handle_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, NotFoundError):
        return _error(404, "资源不存在或无权访问")
    if isinstance(exc, ConflictError):
        return _error(409, str(exc), current_version=exc.current_version)
    if isinstance(exc, LimitError):
        return _error(429, str(exc), code=exc.code)
    if isinstance(exc, ValueError):
        return _error(400, str(exc))
    raise exc


@router.get("/bot/status")
def bot_status(request: Request) -> dict[str, Any]:
    owner = _owner(request)
    store = _store()
    config = runtime.get_config()
    import api.main as main

    runtime_config = main.settings_store.build_runtime_config(main.stock_service.config)
    model_configured = bool(runtime_config.llm_api_key)
    from datetime import datetime, timezone

    from api.local_research import local_research_scope

    # OpenMuse currently maps every request to its shared `local-user`. Never
    # contact that single-user service for a hosted Financial Bot identity.
    if not owner.is_local:
        openmuse = {
            "status": "unavailable",
            "reason": "local_single_user_only",
            "mode": None,
            "agent_configured": None,
            "browser_configured": None,
        }
    else:
        health = OpenMuseClient.from_env().check_health()
        if health.available:
            openmuse = {
                "status": "connected",
                "reason": None,
                "mode": health.mode,
                "agent_configured": health.agent_configured,
                "browser_configured": health.browser_configured,
                "shared_research_enabled": health.shared_research_enabled,
            }
        else:
            reason = health.error or "unavailable"
            status = {
                "disabled": "disabled",
                "invalid_configuration": "configuration_error",
            }.get(reason, "unreachable")
            openmuse = {
                "status": status,
                "reason": reason,
                "mode": None,
                "agent_configured": None,
                "browser_configured": None,
            }
    openmuse["shared_research_enabled"] = bool(
        owner.is_local
        and openmuse.get("status") == "connected"
        and openmuse.get("shared_research_enabled") is True
        and os.environ.get("OPENMUSE_SHARED_RESEARCH_ENABLED", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    usage = store.usage_since(owner.owner_id, today.isoformat(timespec="seconds"))
    return {
        "owner": owner.public(),
        "openmuse": openmuse,
        "storage": {
            "dialect": store.db.dialect,
            "durable": True,
            "database": "server-side",
        },
        "model": {
            "configured": model_configured,
            "model": runtime_config.llm_model if model_configured else None,
            "note": "模型只由服务端调用；浏览器与日志不接触密钥。"
            if model_configured
            else "未配置模型密钥，将使用确定性的规则引擎降级回答。",
        },
        "limits": {
            "daily_tasks": config.daily_task_limit,
            "daily_tokens": config.daily_token_limit,
        },
        "usage": {
            "tasks_today": int(usage.get("task_count") or 0),
            "tokens_today": int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0),
            "cost_usd_today": round(float(usage.get("cost_usd") or 0), 6),
        },
        "deletion": {
            "third_party_retention_verified": config.third_party_retention_verified,
            "backup_dir_configured": bool(config.backup_dir),
        },
        "local_data": local_research_scope(),
        "trial_gate": {
            "multi_user_open": False,
            "reason": "多人试用需要真实 Supabase/PostgreSQL RLS 集成验收与供应商保留范围核实；本构建保持关闭。",
        },
        "recovery": {"interrupted_tasks_recovered": runtime.recovered_task_count()},
        "data_source_policy": source_quality.summary_for([
            "a_share_history",
            "a_share_financials",
            "a_share_valuations",
            "a_share_announcements",
            "a_share_consensus",
            "us_hk_history",
            "us_hk_fundamentals",
            "web_search",
        ]),
        "provider_health": provider_health.snapshot(),
        "last_good_cache": last_good_cache.snapshot(),
        "data_sources": {
            "a_share_quotes": "iFinD（需 token）→ Fuyao → 新浪 → 腾讯(qfq) → akshare；历史/延迟；快照优先 iFinD",
            "a_share_news": "iFinD 公告（需 token）→ 巨潮/东财/财联社/新浪/同花顺（akshare）",
            "a_share_local_research": "巨潮公告/同花顺一致预期/中证国证成分；估值/公告优先 iFinD；财报 Fuyao → 新浪",
            "hong_kong_quotes": "iFinD（需 token）→ yfinance (Yahoo)",
            "hong_kong_news": "iFinD 公告（需 token）→ 同花顺/akshare；公司关联可能是 topic",
            "us_quotes": "yfinance (Yahoo) → Finnhub（需 key）",
            "us_news": "Finnhub（需 key）；未配置时跳过",
            "sec": "SEC EDGAR（需 SEC_USER_AGENT；未配置时跳过）",
            "web_search": "Brave（需 key）→ Google News RSS → Bing RSS → DuckDuckGo",
            "yfinance": "yfinance 1.7.0（Yahoo，无 key）；美/港财报与目标价已接入，行情仍按现有链路",
            "fuyao": "同花顺 Fuyao（A 股，需服务端凭据）；日 K/财报/估值/日历/指数成分；iFinD 之后的备源",
            "ifind": "同花顺 iFinD quantapi（需 IFIND_ACCESS_TOKEN）；/bot 与 /work 共用；优先实时/基础数据/公告/日期序列；历史日 K 额度耗尽时自动改用 Fuyao/备用",
        },
        "provider_config": {
            "finnhub": bool(Config().finnhub_api_key),
            "ifind": ifind_enabled() and bool(
                (os.environ.get("IFIND_ACCESS_TOKEN") or os.environ.get("ifind_api_access_token") or "").strip()
            ),
            "ifind_primary": ifind_primary(),
            "ifind_history_enabled": ifind_history_enabled(),
            "ifind_history_quota_exhausted": history_quota_exhausted(),
            "ifind_history_note": history_quota_user_note(language="zh"),
            "fuyao": fuyao_enabled() and bool(os.environ.get("FUYAO_API_KEY")),
            "fuyao_primary_a_share": fuyao_primary_a_share(),
            "fuyao_financials": fuyao_financials(),
            "brave_search": bool(Config().brave_search_api_key),
            "sec_contact": "@" in os.environ.get("SEC_USER_AGENT", ""),
            "web_search_enabled": Config().web_search_enabled,
            "web_search_timeout_seconds": Config().web_search_timeout,
            "yfinance_shadow": shadow_enabled(),
            "yfinance_shadow_dir": str(shadow_dir()),
            "yfinance_us_hk_fundamentals": yfinance_us_hk_fundamentals(),
            "yfinance_primary_us_hk": yfinance_primary_us_hk(),
            "yfinance_a_share_fallback": yfinance_a_share_fallback(),
        },
    }


# -------------------------------------------------------------------- bots
@router.get("/bots")
def list_bots(request: Request):
    owner = _owner(request)
    return {"bots": _store().list_bots(owner.owner_id)}


@router.get("/bots/prompt-defaults")
def bot_prompt_defaults(request: Request):
    """The canonical starter template and variable catalogue for Bot Studio."""
    _owner(request)
    return {
        "template": DEFAULT_PROMPT_TEMPLATE,
        "config": DEFAULT_PROMPT_CONFIG,
        "variables": VARIABLE_LABELS,
        "known_variables": list(KNOWN_VARIABLES),
        "template_version": PROMPT_TEMPLATE_VERSION,
    }


@router.post("/bots/prompt-preview")
def preview_bot_prompt(request: Request, payload: BotPromptPreviewRequest):
    """Render the exact Bot system prompt for the Studio preview.

    The preview and the executor call the same renderer.  A client cannot make
    the preview differ from what the server will run by editing browser state.
    """
    owner = _owner(request)
    store = _store()
    existing: dict[str, Any] | None = None
    if payload.bot_id:
        existing = store.get_bot(owner.owner_id, payload.bot_id)
        if existing is None:
            return JSONResponse(status_code=404, content={"detail": "Bot not found"})

    config = payload.prompt_config.model_dump() if payload.prompt_config else prompt_config_from_bot(existing)
    fields_set = payload.model_fields_set
    if "instructions" in fields_set:
        config["role"] = payload.instructions
    elif existing:
        config["role"] = str(existing.get("instructions") or config.get("role") or "")
    template = payload.prompt_template
    if template is None:
        template = (existing or {}).get("prompt_template") or DEFAULT_PROMPT_TEMPLATE
    try:
        template = validate_prompt_template(template)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    bot = {
        "id": (existing or {}).get("id") or "new-bot",
        "name": (payload.name if "name" in fields_set else (existing or {}).get("name")) or "新 Bot",
        "instructions": (payload.instructions if "instructions" in fields_set else (existing or {}).get("instructions")) or "",
        "prompt_template": template,
        "prompt_config": config,
    }

    memory_text = ""
    history_count = 0
    routine_count = 0
    if existing:
        memory_text = "\n".join(
            store.build_memory_context(
                owner.owner_id,
                bot_id=existing["id"],
                include_legacy=bool(existing.get("is_default")),
            )
            if config.get("memory_enabled", True)
            else []
        )
        sessions = store.list_sessions(owner.owner_id, bot_id=existing["id"], limit=1)
        if sessions:
            history_count = min(12, int(sessions[0].get("task_count") or 0))
        routine_count = len(store.list_monitors(owner.owner_id, bot_id=existing["id"]))

    platform_prompt = ""
    try:
        from api import agent_pydantic

        platform_prompt = str(getattr(agent_pydantic, "SYSTEM_PROMPT", "") or "")
    except Exception:
        platform_prompt = ""

    preview = render_bot_prompt(
        bot,
        query=payload.query,
        memory_text=memory_text,
        history_count=history_count,
        routine_count=routine_count,
        platform_prompt=platform_prompt,
    )
    preview["bot_id"] = (existing or {}).get("id") or None
    return preview


@router.post("/bots")
def create_bot(request: Request, payload: BotCreateRequest):
    owner = _owner(request)
    try:
        if payload.prompt_template and payload.prompt_template.strip():
            validate_prompt_template(payload.prompt_template)
        bot = _store().create_bot(
            owner.owner_id,
            name=payload.name,
            description=payload.description,
            instructions=payload.instructions,
            approval_threshold=payload.approval_threshold,
            prompt_template=payload.prompt_template,
            prompt_config=payload.prompt_config.model_dump() if payload.prompt_config else None,
        )
    except Exception as exc:
        return _handle_error(exc)
    return JSONResponse(content={"bot": bot}, status_code=201)


@router.post("/bots/{bot_id}/onboarding")
async def bot_onboarding(request: Request, bot_id: str, payload: BotOnboardingRequest):
    """The first conversation this particular Bot should have.

    Returns `null` when the model is unavailable, slow, or returns something
    unusable; the client then keeps its capability-based list.
    """
    owner = _owner(request)
    store = _store()
    try:
        bot = store.get_bot(owner.owner_id, bot_id)
    except Exception as exc:
        return _handle_error(exc)
    if bot is None:
        return JSONResponse(status_code=404, content={"detail": "Bot not found"})
    import api.main as main
    from api.financial_bot.onboarding import BotOnboarding

    runtime_config = main.settings_store.build_runtime_config(main.stock_service.config)
    generator = BotOnboarding(
        api_key=getattr(runtime_config, "llm_api_key", None),
        base_url=getattr(runtime_config, "llm_base_url", None),
        model=getattr(runtime_config, "llm_model", None),
    )
    generated = await generator.generate(bot, language=payload.language)
    return {"onboarding": generated}


@router.get("/bots/{bot_id}/preflight")
def preflight_bot(request: Request, bot_id: str, query: str = Query(min_length=2, max_length=4000)):
    owner = _owner(request)
    bot = _store().get_bot(owner.owner_id, bot_id)
    if bot is None:
        return JSONResponse(status_code=404, content={"detail": "Bot not found"})
    from api.credits import estimate_request_cost
    import api.main as main

    runtime_config = main.settings_store.build_runtime_config(main.stock_service.config)
    estimated = int(estimate_request_cost(query))
    threshold = int(bot.get("approval_threshold") or 0)
    requires = bool(runtime_config.llm_api_key and estimated > threshold)
    return {
        "requires_approval": requires,
        "estimated_credits": estimated,
        "threshold": threshold,
        "model": runtime_config.llm_model if runtime_config.llm_api_key else None,
        "model_configured": bool(runtime_config.llm_api_key),
        "reason": "estimated_credits_above_bot_budget" if requires else None,
    }


@router.patch("/bots/{bot_id}")
def patch_bot(request: Request, bot_id: str, payload: BotPatchRequest):
    owner = _owner(request)
    if payload.prompt_template and payload.prompt_template.strip():
        try:
            validate_prompt_template(payload.prompt_template)
        except ValueError as exc:
            return JSONResponse(status_code=422, content={"detail": str(exc)})
    instructions = payload.instructions
    if instructions is None and payload.prompt_config is not None and payload.prompt_config.role:
        instructions = payload.prompt_config.role
    bot = _store().update_bot(
        owner.owner_id,
        bot_id,
        name=payload.name,
        description=payload.description,
        instructions=instructions,
        approval_threshold=payload.approval_threshold,
        prompt_template=payload.prompt_template,
        prompt_config=payload.prompt_config.model_dump() if payload.prompt_config else None,
        market_hours_only=payload.market_hours_only,
        max_alerts_per_day=payload.max_alerts_per_day,
        quiet_hours_start=payload.quiet_hours_start,
        quiet_hours_end=payload.quiet_hours_end,
        webhook_url=payload.webhook_url,
        notification_email=payload.notification_email,
        report_style=payload.report_style,
        digest_mode=payload.digest_mode,
        digest_hour=payload.digest_hour,
    )
    if bot is None:
        return JSONResponse(status_code=404, content={"detail": "Bot not found"})
    return {"bot": bot}


def _digest_payload(store, owner_id: str, bot_id: str) -> dict[str, Any]:
    bot = store.get_bot(owner_id, bot_id)
    notifications = store.list_notifications(owner_id, bot_id=bot_id, limit=50)
    unread = [item for item in notifications if item.get("status") != "read"]
    lines = [f"- {item.get('title')}: {str(item.get('body') or '')[:180]}" for item in unread[:20]]
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).date().isoformat()
    return {
        "title": f"Financial Bot 摘要预览 · {day}",
        "body": "\n".join(lines) if lines else "暂无未读提醒。",
        "bot_id": bot_id,
        "bot_name": (bot or {}).get("name"),
        "unread_count": len(unread),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/bots/{bot_id}/digest-preview")
def digest_preview(request: Request, bot_id: str):
    owner = _owner(request)
    store = _store()
    if store.get_bot(owner.owner_id, bot_id) is None:
        return JSONResponse(status_code=404, content={"detail": "Bot not found"})
    return {"digest": _digest_payload(store, owner.owner_id, bot_id)}


@router.post("/bots/{bot_id}/digest/send")
def send_digest_now(request: Request, bot_id: str):
    owner = _owner(request)
    store = _store()
    if store.get_bot(owner.owner_id, bot_id) is None:
        return JSONResponse(status_code=404, content={"detail": "Bot not found"})
    digest = _digest_payload(store, owner.owner_id, bot_id)
    channels = store.get_bot_channels(owner.owner_id, bot_id)
    if not (channels.get("webhook_url") or channels.get("notification_email")):
        return {"digest": digest, "configured": False, "sent": {"webhook": False, "email": False}}
    from api.financial_bot.dispatch import configured_results, dispatch_notification

    result = dispatch_notification(
        digest,
        webhook_url=channels.get("webhook_url"),
        email_to=channels.get("notification_email"),
    )
    for channel, ok in configured_results(result, webhook_url=channels.get("webhook_url"), email_to=channels.get("notification_email")).items():
        store.record_delivery(
            owner.owner_id,
            bot_id=bot_id,
            notification_id=None,
            channel=f"manual_digest_{channel}",
            status="sent" if ok else "failed",
            payload=digest,
        )
    return {"digest": digest, "configured": True, "sent": result}


@router.post("/bots/{bot_id}/test-notification")
def test_bot_notification(request: Request, bot_id: str):
    owner = _owner(request)
    store = _store()
    bot = store.get_bot(owner.owner_id, bot_id)
    if bot is None:
        return JSONResponse(status_code=404, content={"detail": "Bot not found"})
    channels = store.get_bot_channels(owner.owner_id, bot_id)
    from api.financial_bot.dispatch import configured_results, dispatch_notification
    from datetime import datetime, timezone

    payload = {
        "title": "Financial Bot test notification",
        "body": "This is a test from the Bot notification settings. The in-app inbox is unchanged.",
        "bot_id": bot_id,
        "severity": "info",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = dispatch_notification(
        payload,
        webhook_url=channels.get("webhook_url"),
        email_to=channels.get("notification_email"),
    )
    return {
        "webhook": bool(result.get("webhook")),
        "email": bool(result.get("email")),
        "configured": bool(channels.get("webhook_url") or channels.get("notification_email")),
    }


@router.delete("/bots/{bot_id}")
def delete_bot(request: Request, bot_id: str):
    owner = _owner(request)
    try:
        result = _store().delete_bot(owner.owner_id, bot_id)
    except ValueError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    except Exception as exc:
        return _handle_error(exc)
    return result


# --------------------------------------------------------- market closures
@router.get("/market-closures")
def list_market_closures(request: Request):
    owner = _owner(request)
    return {"closures": _store().list_market_closures(owner.owner_id)}


@router.post("/market-closures")
def add_market_closure(request: Request, payload: MarketClosureCreateRequest):
    owner = _owner(request)
    try:
        closure = _store().add_market_closure(
            owner.owner_id,
            market=payload.market,
            closure_date=payload.date,
            reason=payload.reason,
        )
    except Exception as exc:
        return _handle_error(exc)
    return JSONResponse(content={"closure": closure}, status_code=201)


@router.delete("/market-closures/{closure_id}")
def delete_market_closure(request: Request, closure_id: str):
    owner = _owner(request)
    if not _store().delete_market_closure(owner.owner_id, closure_id):
        return JSONResponse(status_code=404, content={"detail": "Closure not found"})
    return {"deleted": closure_id}


# ----------------------------------------------------------------- monitors
@router.get("/monitors")
def list_monitors(request: Request, bot_id: Optional[str] = Query(default=None, max_length=200)):
    owner = _owner(request)
    return {"monitors": _store().list_monitors(owner.owner_id, bot_id=bot_id)}


@router.post("/monitors")
def create_monitor(request: Request, payload: MonitorCreateRequest):
    owner = _owner(request)
    store = _store()
    bot = store.get_bot(owner.owner_id, payload.bot_id) if payload.bot_id else store.ensure_default_bot(owner.owner_id)
    if bot is None:
        return JSONResponse(status_code=404, content={"detail": "Bot not found"})
    if (bot.get("prompt_config") or {}).get("routines_enabled") is False:
        return JSONResponse(status_code=409, content={"detail": "routines_disabled"})
    try:
        from ashare.stock_pool import normalize_stock_code

        symbol = normalize_stock_code(payload.symbol.strip())
        monitor = store.create_monitor(
            owner.owner_id,
            bot_id=bot["id"],
            name=payload.name.strip(),
            symbol=symbol,
            condition=payload.condition,
            threshold=payload.threshold,
            direction=payload.direction,
            interval_minutes=payload.interval_minutes,
            cooldown_minutes=payload.cooldown_minutes,
            session_id=payload.session_id,
        )
    except Exception as exc:
        return _handle_error(exc)
    return JSONResponse(content={"monitor": monitor}, status_code=201)


@router.patch("/monitors/{monitor_id}")
def patch_monitor(request: Request, monitor_id: str, payload: MonitorPatchRequest):
    owner = _owner(request)
    monitor = _store().update_monitor(
        owner.owner_id,
        monitor_id,
        status=payload.status,
        name=payload.name,
        threshold=payload.threshold,
    )
    if monitor is None:
        return JSONResponse(status_code=404, content={"detail": "Monitor not found"})
    return {"monitor": monitor}


@router.post("/monitors/{monitor_id}/mute")
def mute_monitor(request: Request, monitor_id: str, payload: MonitorMuteRequest):
    owner = _owner(request)
    from datetime import datetime, timedelta, timezone

    if payload.mute_minutes <= 0:
        monitor = _store().update_monitor(owner.owner_id, monitor_id, clear_mute=True)
    else:
        until = (datetime.now(timezone.utc) + timedelta(minutes=payload.mute_minutes)).isoformat(timespec="seconds")
        monitor = _store().update_monitor(owner.owner_id, monitor_id, muted_until=until)
    if monitor is None:
        return JSONResponse(status_code=404, content={"detail": "Monitor not found"})
    return {"monitor": monitor}


@router.delete("/monitors/{monitor_id}")
def delete_monitor(request: Request, monitor_id: str):
    owner = _owner(request)
    deleted = _store().delete_monitor(owner.owner_id, monitor_id)
    if not deleted:
        return JSONResponse(status_code=404, content={"detail": "Monitor not found"})
    return {"deleted": monitor_id}


@router.get("/notifications")
def list_notifications(
    request: Request,
    bot_id: Optional[str] = Query(default=None, max_length=200),
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
):
    owner = _owner(request)
    return {"notifications": _store().list_notifications(owner.owner_id, bot_id=bot_id, unread_only=unread_only, limit=limit)}


@router.post("/notifications/{notification_id}/analyze")
def analyze_notification(request: Request, notification_id: str, payload: NotificationAnalyzeRequest):
    owner = _owner(request)
    store = _store()
    notification = store.get_notification(owner.owner_id, notification_id)
    if notification is None:
        return JSONResponse(status_code=404, content={"detail": "Notification not found"})
    if notification.get("task_id"):
        task = store.get_task(owner.owner_id, notification["task_id"])
        return {"task": _task_public(task), "created": False, "bot_id": notification["bot_id"]}
    bot = store.get_bot(owner.owner_id, notification["bot_id"])
    if bot is None:
        bot = store.ensure_default_bot(owner.owner_id)
    query = (
        f"请核查这条主动监控异动并给出研究结论：{notification['title']}。"
        f"{notification['body']} "
        "请区分已观察事实、可能原因和证据缺口，并列出后续需要跟踪的指标。"
    )
    from api.credits import estimate_request_cost
    import api.main as main

    runtime_config = main.settings_store.build_runtime_config(main.stock_service.config)
    estimated = int(estimate_request_cost(query))
    threshold = int(bot.get("approval_threshold") or 0)
    if runtime_config.llm_api_key and estimated > threshold and not payload.model_approved:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "model_approval_required",
                "estimated_credits": estimated,
                "threshold": threshold,
                "bot_id": bot["id"],
                "model": runtime_config.llm_model,
            },
        )
    session_id = f"monitor-{notification.get('monitor_id') or notification_id}"
    store.ensure_bot_session(owner.owner_id, bot["id"], session_id)
    idempotency_key = f"notification:{notification_id}"
    try:
        task, created = store.create_task(
            owner_id=owner.owner_id,
            query=query,
            session_id=session_id,
            language="auto",
            research_mode="quick",
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        return _handle_error(exc)
    if created:
        store.link_notification_task(owner.owner_id, notification_id, task["id"])
        _executor().start(owner, task["id"])
    return JSONResponse(
        content={"task": _task_public(task), "created": created, "bot_id": bot["id"], "session_id": session_id},
        status_code=201 if created else 200,
    )


@router.post("/notifications/{notification_id}/read")
def read_notification(request: Request, notification_id: str):
    owner = _owner(request)
    notification = _store().mark_notification_read(owner.owner_id, notification_id)
    if notification is None:
        return JSONResponse(status_code=404, content={"detail": "Notification not found"})
    return {"notification": notification}


@router.get("/deliveries/export")
def export_deliveries(
    request: Request,
    bot_id: Optional[str] = Query(default=None, max_length=200),
    status: Optional[str] = Query(default=None, max_length=20),
    channel: Optional[str] = Query(default=None, max_length=40),
):
    owner = _owner(request)
    import csv
    import io

    rows = _store().list_deliveries(owner.owner_id, bot_id=bot_id, status=status, channel=channel, limit=1000)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "channel", "status", "attempts", "created_at", "sent_at", "error"])
    for row in rows:
        writer.writerow([row.get("id"), row.get("channel"), row.get("status"), row.get("attempts"), row.get("created_at"), row.get("sent_at"), row.get("error")])
    from fastapi.responses import Response

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="delivery-log.csv"'},
    )


@router.get("/deliveries")
def list_deliveries(
    request: Request,
    bot_id: Optional[str] = Query(default=None, max_length=200),
    status: Optional[str] = Query(default=None, max_length=20),
    channel: Optional[str] = Query(default=None, max_length=40),
    limit: int = Query(default=30, ge=1, le=100),
):
    owner = _owner(request)
    return {"deliveries": _store().list_deliveries(owner.owner_id, bot_id=bot_id, status=status, channel=channel, limit=limit)}


@router.post("/deliveries/{delivery_id}/retry")
def retry_delivery(request: Request, delivery_id: str):
    owner = _owner(request)
    store = _store()
    delivery = store.get_delivery(owner.owner_id, delivery_id)
    if delivery is None:
        return JSONResponse(status_code=404, content={"detail": "Delivery not found"})
    if delivery.get("status") == "sent":
        return {"delivery": {k: v for k, v in delivery.items() if k != "payload_json"}, "retried": False}
    try:
        payload = json.loads(delivery.get("payload_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict) or not payload:
        return JSONResponse(status_code=409, content={"detail": "delivery_payload_missing"})
    channels = store.get_bot_channels(owner.owner_id, delivery["bot_id"])
    from api.financial_bot.dispatch import dispatch_channel

    ok = dispatch_channel(
        payload,
        delivery.get("channel") or "",
        webhook_url=channels.get("webhook_url"),
        email_to=channels.get("notification_email"),
    )
    store.update_delivery(owner.owner_id, delivery_id, status="sent" if ok else "failed")
    return {
        "delivery": {k: v for k, v in (store.get_delivery(owner.owner_id, delivery_id) or {}).items() if k != "payload_json"},
        "retried": True,
        "sent": ok,
    }


@router.get("/artifacts")
def list_artifacts(
    request: Request,
    bot_id: Optional[str] = Query(default=None, max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
):
    owner = _owner(request)
    return {"artifacts": _store().list_artifacts(owner.owner_id, bot_id=bot_id, limit=limit)}


@router.get("/model-providers")
def model_providers(request: Request):
    owner = _owner(request)
    connected = False
    if owner.is_local:
        from api.financial_bot.chatgpt_session import is_signed_in
        connected = is_signed_in()
    return {"deepseek": {"available": True},
            "chatgpt": {"available": owner.is_local, "connected": connected}}


# ------------------------------------------------------------------- tasks
def _task_authorization(store, owner, payload, bot, runtime_config, threshold):
    """Freeze what this task may do: scope, budget, model and prompt version.

    A follow-up on the same thread inherits the finished task's snapshot; a new
    topic starts from the Bot's current defaults. Widening scope or budget needs
    a new task, never an edit of the frozen snapshot.
    """
    parent = None
    if payload.continued_from_task_id:
        parent = store.get_task(owner.owner_id, payload.continued_from_task_id)
        if payload.session_id and parent.get("session_id") != payload.session_id:
            raise HTTPException(status_code=409, detail="continued_task_belongs_to_another_thread")
        if parent.get("status") not in {"completed", "partial", "dropped", "failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="continued_task_not_finished")

    inherited = parent.get("authorization") if parent else None
    if inherited:
        authorization = json.loads(json.dumps(inherited))
        authorization["inherited_from"] = parent["id"]
    else:
        authorization = {
            "owner_id": owner.owner_id,
            "data_authorization": owner.mode,
            # Which providers a research actually used is cited by the answer's
            # evidence; the snapshot must not guess them at creation time.
            "data_sources": None,
            "budget": {"credits_limit": threshold},
            "model": ("ChatGPT plan (account model)" if payload.model_provider == "chatgpt" else runtime_config.llm_model or None),
            "prompt_version": bot.get("prompt_version"),
        }
    authorization["scope"] = {
        "session_id": payload.session_id,
        "query": payload.query.strip()[:200],
        "research_mode": payload.research_mode,
        "model_provider": payload.model_provider,
    }
    return authorization, parent["id"] if parent else None


@router.post("/tasks")
async def create_task(request: Request, payload: TaskCreateRequest):
    owner = _owner(request)
    if payload.model_provider == "chatgpt" and not owner.is_local:
        raise HTTPException(status_code=403, detail="chatgpt_local_only")
    from api.research_rate_limit import enforce_research_rate_limit

    limited = enforce_research_rate_limit(
        request,
        owner_id=owner.owner_id,
        language=payload.language,
        bucket="research_task",
    )
    if limited is not None:
        return limited
    store = _store()
    config = runtime.get_config()
    platform = store.get_platform_state()
    if platform.get("paused"):
        return JSONResponse(
            status_code=409,
            content={
                "detail": "platform_paused",
                "reason": platform.get("pause_reason"),
                "paused_by": platform.get("paused_by"),
            },
        )
    try:
        store.check_daily_limits(
            owner.owner_id, max_tasks=config.daily_task_limit, max_tokens=config.daily_token_limit
        )
    except Exception as exc:
        return _handle_error(exc)

    header_key = request.headers.get("Idempotency-Key", "").strip()
    idempotency_key = (payload.idempotency_key or header_key or uuid.uuid4().hex)[:200]
    zh = payload.language != "en-US"
    try:
        bot = store.get_bot(owner.owner_id, payload.bot_id) if payload.bot_id else store.bot_for_session(owner.owner_id, payload.session_id)
        if bot is None:
            return JSONResponse(status_code=404, content={"detail": "Bot not found"})
        from api.credits import estimate_request_cost
        import api.main as main

        runtime_config = main.settings_store.build_runtime_config(main.stock_service.config)
        estimated = int(estimate_request_cost(payload.query))
        threshold = int(bot.get("approval_threshold") or 0)
        if runtime_config.llm_api_key and estimated > threshold and not payload.model_approved:
            try:
                store.record_disposition(
                    owner.owner_id,
                    input_ref=idempotency_key,
                    disposition="clarify",
                    reason_code="model_approval_required",
                    reason_display="等待模型调用确认",
                    session_id=payload.session_id,
                )
            except Exception:
                pass
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "model_approval_required",
                    "estimated_credits": estimated,
                    "threshold": threshold,
                    "bot_id": bot["id"],
                    "model": runtime_config.llm_model,
                },
            )
        if payload.session_id:
            bound = store.ensure_bot_session(owner.owner_id, bot["id"], payload.session_id)
            if bound and bound.get("bot_id") != bot["id"]:
                return JSONResponse(
                    status_code=409,
                    content={"detail": "session_belongs_to_another_bot", "bot_id": bound["bot_id"]},
                )
            store.ensure_main_thread(owner.owner_id, bot["id"], payload.session_id, payload.query[:120])
        authorization, continued_from = _task_authorization(
            store, owner, payload, bot, runtime_config, threshold
        )
        task, created = store.create_task(
            owner_id=owner.owner_id,
            query=payload.query.strip(),
            session_id=payload.session_id,
            language=payload.language,
            research_mode=payload.research_mode,
            idempotency_key=idempotency_key,
            zh=zh,
            ui_context=payload.ui_context.model_dump() if payload.ui_context else None,
            authorization=authorization,
            continued_from_task_id=continued_from,
        )
    except Exception as exc:
        return _handle_error(exc)

    if created:
        try:
            store.record_disposition(
                owner.owner_id,
                input_ref=idempotency_key,
                disposition="accepted",
                reason_code="research_started",
                reason_display="已开始研究",
                task_id=task["id"],
                session_id=payload.session_id,
            )
        except Exception:
            # A disposition is evidence; losing it must not block the task.
            pass
        try:
            if task.get("session_id"):
                store.append_message(
                    owner.owner_id,
                    session_id=str(task["session_id"]),
                    role="user",
                    kind="message",
                    content=str(task.get("query") or ""),
                    task_id=task["id"],
                    bot_id=bot["id"],
                    metadata={"source": "task"},
                )
        except Exception:
            # Conversation persistence must never prevent the task itself.
            pass
        # Explicit user statements are recorded immediately, with the original
        # sentence as source. They are facts, not model-inferred views.
        for fact in extract_explicit_memories(task["query"]):
            try:
                store.add_direct_memory(
                    owner.owner_id,
                    kind=fact["kind"],
                    text=fact["text"],
                    source_text=fact["source_text"],
                    source_ref=f"task:{task['id']}",
                    bot_id=bot["id"],
                    zh=zh,
                )
            except Exception:
                # Memory extraction must never block the research task.
                pass
        _executor().start(owner, task["id"])
    return JSONResponse(
        content={"task": _task_public(task), "created": created},
        status_code=201 if created else 200,
    )


@router.post("/tasks/openmuse-shared")
async def create_openmuse_shared_research(request: Request, payload: OpenMuseSharedResearchRequest):
    owner = _owner(request)
    if not owner.is_local:
        raise HTTPException(status_code=403, detail="openmuse_local_single_user_only")
    # Loopback is defense in depth for direct local access only. It is not
    # proof of the original browser peer behind a reverse proxy. Deployments
    # must keep this feature on a protected local/private boundary.
    try:
        remote_address = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        remote_address = None
    if remote_address is None or not remote_address.is_loopback:
        raise HTTPException(status_code=403, detail="openmuse_shared_requires_local_request")
    if os.environ.get("OPENMUSE_SHARED_RESEARCH_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=503, detail="openmuse_shared_research_disabled")
    financial_bridge_required = _is_financial_research_question(payload.query)
    if financial_bridge_required:
        shared_token = os.environ.get("FINANCIAL_RESEARCH_SHARED_TOKEN", "").strip()
        if len(shared_token.encode("utf-8")) < 32:
            raise HTTPException(status_code=503, detail="financial_research_bridge_unavailable")
    from api.financial_bot.openmuse_task_coordinator import OpenMuseTaskCoordinator
    from api.financial_bot.openmuse_tasks import OpenMuseTasksClient, OpenMuseTasksConfig, OpenMuseTaskError

    config = OpenMuseTasksConfig.from_env()
    if not config.enabled:
        raise HTTPException(status_code=503, detail="openmuse_shared_research_disabled")
    if not config.base_url or not config.access_key:
        raise HTTPException(status_code=503, detail="openmuse_invalid_configuration")
    client = OpenMuseTasksClient(config, store=_store())
    try:
        if not await client.shared_research_enabled(owner):
            raise HTTPException(status_code=503, detail="openmuse_shared_research_upstream_disabled")
    except OpenMuseTaskError as exc:
        await client.close()
        raise HTTPException(status_code=503, detail=f"openmuse_{exc.code}") from None
    store = _store()
    bot = store.get_bot(owner.owner_id, payload.bot_id) if payload.bot_id else store.bot_for_session(owner.owner_id, payload.session_id)
    if bot is None:
        await client.close()
        raise HTTPException(status_code=404, detail="bot_not_found")
    if payload.session_id:
        bound = store.ensure_bot_session(owner.owner_id, bot["id"], payload.session_id)
        if bound and bound.get("bot_id") != bot["id"]:
            await client.close()
            raise HTTPException(status_code=409, detail="session_belongs_to_another_bot")
    key = (payload.idempotency_key or request.headers.get("Idempotency-Key", "").strip() or uuid.uuid4().hex)[:200]
    task, created = store.create_task(
        owner_id=owner.owner_id,
        query=payload.query.strip(),
        session_id=payload.session_id,
        language=payload.language,
        research_mode="openmuse_shared",
        idempotency_key=key,
        zh=payload.language != "en-US",
        authorization={
            "owner_id": owner.owner_id,
            "data_authorization": owner.mode,
            # This UI field is provenance, not the task's data authorization.
            # The create response cannot establish which upstream sources ran.
            "data_sources": None,
            "scope": {
                "query": payload.query.strip()[:200],
                "research_mode": "openmuse_shared",
                "financial_bridge_required": financial_bridge_required,
            },
        },
    )
    coordinator = OpenMuseTaskCoordinator(
        store,
        client,
        enabled=True,
        is_general_task=lambda item: item.get("research_mode") == "openmuse_shared",
    )
    try:
        result = await coordinator.submit(owner, task["id"])
    except OpenMuseTaskError as exc:
        raise HTTPException(status_code=503, detail=f"openmuse_{exc.code}") from None
    finally:
        await client.close()
    if result.state == "blocked":
        store.transition(
            owner.owner_id,
            task["id"],
            "failed",
            reason="openmuse_shared_research_unavailable",
            summary="OpenMuse could not verify or accept the shared research workspace.",
        )
        raise HTTPException(status_code=503, detail="openmuse_shared_research_unavailable")
    current = store.get_task(owner.owner_id, task["id"])
    return JSONResponse(
        status_code=201 if created else 200,
        content={"task": _task_public(current), "coordination": result.__dict__},
    )


@router.get("/tasks/{task_id}/openmuse-shared")
async def sync_openmuse_shared_research(request: Request, task_id: str):
    owner = _owner(request)
    if not owner.is_local:
        raise HTTPException(status_code=403, detail="openmuse_local_single_user_only")
    from api.financial_bot.openmuse_task_coordinator import OpenMuseTaskCoordinator
    from api.financial_bot.openmuse_tasks import OpenMuseTasksClient, OpenMuseTasksConfig, OpenMuseTaskError

    config = OpenMuseTasksConfig.from_env()
    store = _store()
    try:
        if store.get_task(owner.owner_id, task_id).get("research_mode") != "openmuse_shared":
            raise HTTPException(status_code=404, detail="openmuse_task_mapping_not_found")
    except NotFoundError:
        raise HTTPException(status_code=404, detail="openmuse_task_mapping_not_found") from None
    mapping = store.get_openmuse_task_mapping(owner.owner_id, task_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="openmuse_task_mapping_not_found")
    client = OpenMuseTasksClient(config, store=store)
    coordinator = OpenMuseTaskCoordinator(
        store,
        client,
        enabled=config.enabled,
        is_general_task=lambda item: item.get("research_mode") == "openmuse_shared",
    )
    try:
        result = await coordinator.sync(owner, task_id)
        # Sync can reconcile a timed-out create and bind its previously unknown
        # upstream id. Re-read the durable mapping after recovery before loading
        # the upstream projection for the response.
        mapping = store.get_openmuse_task_mapping(owner.owner_id, task_id)
        upstream = None
        upstream_id = mapping.get("upstream_task_id")
        if upstream_id:
            upstream = (await client.get_task(owner, upstream_id)).get("task")
    except OpenMuseTaskError as exc:
        raise HTTPException(status_code=503, detail=f"openmuse_{exc.code}") from None
    finally:
        await client.close()
    return {"coordination": result.__dict__, "upstream_task": upstream}


@router.post("/tasks/{task_id}/openmuse-shared/cancel")
async def cancel_openmuse_shared_research(request: Request, task_id: str):
    owner = _owner(request)
    if not owner.is_local:
        raise HTTPException(status_code=403, detail="openmuse_local_single_user_only")
    try:
        remote_address = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        remote_address = None
    if remote_address is None or not remote_address.is_loopback:
        raise HTTPException(status_code=403, detail="openmuse_shared_requires_local_request")
    store = _store()
    try:
        if store.get_task(owner.owner_id, task_id).get("research_mode") != "openmuse_shared":
            raise HTTPException(status_code=404, detail="openmuse_task_mapping_not_found")
    except NotFoundError:
        raise HTTPException(status_code=404, detail="openmuse_task_mapping_not_found") from None
    mapping = store.get_openmuse_task_mapping(owner.owner_id, task_id)
    if not mapping or not mapping.get("upstream_task_id"):
        raise HTTPException(status_code=404, detail="openmuse_task_mapping_not_found")
    from api.financial_bot.openmuse_tasks import OpenMuseTasksClient, OpenMuseTasksConfig, OpenMuseTaskError

    client = OpenMuseTasksClient(OpenMuseTasksConfig.from_env(), store=store)
    try:
        upstream = await client.cancel_task(owner, mapping["upstream_task_id"])
    except OpenMuseTaskError as exc:
        raise HTTPException(status_code=503, detail=f"openmuse_{exc.code}") from None
    finally:
        await client.close()
    return {"upstream_task": upstream}


@router.post("/tasks/{task_id}/openmuse-shared/answer")
async def answer_openmuse_shared_research(request: Request, task_id: str, payload: dict[str, Any]):
    owner = _owner(request)
    if not owner.is_local:
        raise HTTPException(status_code=403, detail="openmuse_local_single_user_only")
    try:
        remote_address = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        remote_address = None
    if remote_address is None or not remote_address.is_loopback:
        raise HTTPException(status_code=403, detail="openmuse_shared_requires_local_request")
    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 12000:
        raise HTTPException(status_code=422, detail="invalid_task_answer")
    store = _store()
    try:
        if store.get_task(owner.owner_id, task_id).get("research_mode") != "openmuse_shared":
            raise HTTPException(status_code=404, detail="openmuse_task_mapping_not_found")
    except NotFoundError:
        raise HTTPException(status_code=404, detail="openmuse_task_mapping_not_found") from None
    mapping = store.get_openmuse_task_mapping(owner.owner_id, task_id)
    if not mapping or not mapping.get("upstream_task_id"):
        raise HTTPException(status_code=404, detail="openmuse_task_mapping_not_found")
    from api.financial_bot.openmuse_tasks import OpenMuseTasksClient, OpenMuseTasksConfig, OpenMuseTaskError

    client = OpenMuseTasksClient(OpenMuseTasksConfig.from_env(), store=store)
    try:
        upstream = await client.get_task(owner, mapping["upstream_task_id"])
        task = upstream.get("task", {})
        if task.get("shared_research_workspace") is not True or task.get("status") != "waiting_input":
            raise HTTPException(status_code=409, detail="openmuse_task_not_waiting_for_input")
        await client.answer_task(owner, mapping["upstream_task_id"], answer.strip())
        resumed = await client.resume_research_task(owner, mapping["upstream_task_id"])
    except OpenMuseTaskError as exc:
        raise HTTPException(status_code=503, detail=f"openmuse_{exc.code}") from None
    finally:
        await client.close()
    return {"upstream_task": resumed}


@router.get("/dispositions")
def list_dispositions(request: Request, session_id: str | None = None, limit: int = 50):
    """Why each user input was researched, answered, clarified or dropped."""
    owner = _owner(request)
    return {"dispositions": _store().list_dispositions(owner.owner_id, session_id=session_id, limit=limit)}


@router.get("/platform/state")
def platform_state(request: Request):
    _owner(request)
    return {"state": _store().get_platform_state()}


@router.post("/platform/pause")
def set_platform_pause(request: Request, payload: dict):
    """Pause dispatch for every owner. Local admin only in this release."""
    owner = _owner(request)
    if not owner.is_local:
        return JSONResponse(status_code=403, content={"detail": "platform_admin_local_only"})
    reason = (payload.get("reason") or "").strip()[:300] or None
    by = (payload.get("by") or "local_admin")[:80]
    state = _store().set_platform_pause(paused=bool(payload.get("paused")), by=by, reason=reason)
    return {"state": state}


@router.patch("/threads/{session_id}")
def patch_thread(request: Request, session_id: str, payload: dict):
    owner = _owner(request)
    allowed = {"title", "pinned", "archived", "folder", "unread", "approval_mode", "research_mode"}
    if set(payload) - allowed:
        return JSONResponse(status_code=400, content={"detail": "unsupported thread field"})
    try:
        store = _store()
        store.ensure_thread_for_session(owner.owner_id, session_id)
        thread = store.update_thread(
            owner.owner_id,
            session_id,
            title=payload.get("title"),
            pinned=payload.get("pinned"),
            archived=payload.get("archived"),
            folder=payload.get("folder"),
            unread=payload.get("unread"),
            approval_mode=payload.get("approval_mode"),
            research_mode=payload.get("research_mode"),
        )
    except Exception as exc:
        return _handle_error(exc)
    if thread is None:
        return JSONResponse(status_code=404, content={"detail": "Thread not found"})
    return {"thread": thread}


@router.post("/threads/{session_id}/read")
def read_thread(request: Request, session_id: str):
    owner = _owner(request)
    store = _store()
    # A brand-new conversation has no thread row yet. Reading it is a no-op, not
    # an error, and must not create a row: opening the page should never add an
    # empty conversation to the roster.
    thread = store.mark_thread_read(owner.owner_id, session_id)
    if thread is None:
        return {"thread": None, "created": False}
    return {"thread": thread}


@router.delete("/sessions/{session_id}")
def delete_session(request: Request, session_id: str):
    owner = _owner(request)
    result = _store().delete_session(owner.owner_id, session_id)
    if result.get("status") == "pending":
        return JSONResponse(
            status_code=409,
            content={"detail": "openmuse_task_deletion_pending", **result},
        )
    return result


@router.delete("/bots/{bot_id}/sessions")
def clear_bot_sessions(request: Request, bot_id: str):
    owner = _owner(request)
    store = _store()
    bot = store.get_bot(owner.owner_id, bot_id)
    if bot is None:
        return JSONResponse(status_code=404, content={"detail": "Bot not found"})
    result = store.clear_bot_sessions(owner.owner_id, bot_id)
    if result.get("status") == "pending":
        return JSONResponse(
            status_code=409,
            content={"detail": "openmuse_task_deletion_pending", **result},
        )
    return result


@router.post("/threads")
def create_thread(request: Request, payload: ThreadCreateRequest):
    owner = _owner(request)
    store = _store()
    bot = store.get_bot(owner.owner_id, payload.bot_id)
    if bot is None:
        return JSONResponse(status_code=404, content={"detail": "Bot not found"})
    thread = store.create_thread(
        owner.owner_id,
        bot_id=bot["id"],
        parent_session_id=payload.parent_session_id,
        title=(payload.title or "独立话题")[:120],
    )
    return JSONResponse(content={"thread": thread, "session": thread}, status_code=201)


@router.get("/sessions")
def list_sessions(
    request: Request,
    bot_id: Optional[str] = Query(default=None, max_length=200),
    limit: int = Query(default=30, ge=1, le=100),
):
    owner = _owner(request)
    return {"sessions": _store().list_sessions(owner.owner_id, bot_id=bot_id, limit=limit)}


@router.post("/sessions/{session_id}/welcome")
def ensure_welcome_message(request: Request, session_id: str, payload: WelcomeCreateRequest):
    owner = _owner(request)
    store = _store()
    bot = store.get_bot(owner.owner_id, payload.bot_id)
    if bot is None:
        return JSONResponse(status_code=404, content={"detail": "Bot not found"})
    bound = store.ensure_bot_session(owner.owner_id, bot["id"], session_id)
    if bound and bound.get("bot_id") != bot["id"]:
        # The conversation already belongs to another Bot; keep the history there.
        # `bot_id` tells the client which Bot actually owns it, so it can show the
        # thread under the right assistant instead of leaving a stale header.
        return JSONResponse(
            status_code=409,
            content={"detail": "session_belongs_to_another_bot", "bot_id": bound["bot_id"]},
        )
    existing = store.list_messages(owner.owner_id, session_id, limit=500)
    if any(message.get("kind") == "welcome" for message in existing):
        return {"created": False}
    zh = payload.language != "en-US"
    introductions = {
        "zh-CN": "你好，我是 Financial Bot。不需要先想好完整的问题。告诉我你平时怎么做研究，我会把第一次对话调成适合你的方式。",
        "en-US": "Hi, I'm Financial Bot. You don't need a perfect prompt to start. Tell me how you usually research — I'll shape the first conversation.",
    }
    store.append_message(
        owner.owner_id,
        session_id=session_id,
        role="assistant",
        kind="welcome",
        content=introductions["zh-CN" if zh else "en-US"],
        bot_id=bot["id"],
        metadata={"persona": payload.persona, "language": "zh-CN" if zh else "en-US"},
    )
    return {"created": True}


@router.get("/sessions/{session_id}/state")
def get_thread_state(request: Request, session_id: str):
    owner = _owner(request)
    store = _store()
    # Read-only: state must not materialize a thread row for a session the user
    # has not started yet.
    state = store.get_thread_workflow(owner.owner_id, session_id)
    if state is None:
        return {"workflow": None, "task_id": None, "updated_at": None}
    return state


@router.post("/conversation/backfill")
def backfill_conversation(request: Request):
    owner = _owner(request)
    return _store().backfill_all_messages(owner.owner_id)


@router.get("/sessions/{session_id}/messages")
def list_session_messages(
    request: Request,
    session_id: str,
    limit: int = Query(default=200, ge=1, le=500),
):
    owner = _owner(request)
    store = _store()
    # Legacy sessions predate fb_messages; project their task cards once on read.
    store.backfill_session_messages(owner.owner_id, session_id)
    return {"messages": store.list_messages(owner.owner_id, session_id, limit=limit)}


@router.get("/tasks")
def list_tasks(
    request: Request,
    session_id: Optional[str] = Query(default=None, max_length=200),
    limit: int = Query(default=30, ge=1, le=100),
):
    owner = _owner(request)
    store = _store()
    tasks = store.list_tasks(owner.owner_id, session_id=session_id, limit=limit)
    return {"tasks": [_task_public(task) for task in tasks]}


@router.get("/tasks/{task_id}")
def get_task(request: Request, task_id: str):
    owner = _owner(request)
    try:
        task = _store().get_task(owner.owner_id, task_id)
    except Exception as exc:
        return _handle_error(exc)
    return {"task": _task_public(task)}


@router.post("/tasks/{task_id}/resume")
def resume_task(request: Request, task_id: str):
    owner = _owner(request)
    try:
        if _store().get_task(owner.owner_id, task_id).get("research_mode") == "openmuse_shared":
            raise HTTPException(status_code=409, detail="openmuse_shared_resume_required")
        task, plan = _store().resume_task(owner.owner_id, task_id)
    except NotFoundError:
        return JSONResponse(status_code=404, content={"detail": "Task not found"})
    except ValueError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    except Exception as exc:
        return _handle_error(exc)
    _executor().start(owner, task_id, resume=True)
    return JSONResponse(content={"task": _task_public(task), "plan": plan}, status_code=202)


@router.get("/tasks/{task_id}/plan")
def get_task_plan(request: Request, task_id: str):
    owner = _owner(request)
    try:
        task = _store().get_task(owner.owner_id, task_id)
        if task is None:
            return JSONResponse(status_code=404, content={"detail": "Task not found"})
        plan = _store().get_plan(owner.owner_id, task_id)
    except Exception as exc:
        return _handle_error(exc)
    return {"plan": plan}


@router.get("/tasks/{task_id}/events")
async def task_events(
    request: Request,
    task_id: str,
    after_sequence: int = Query(default=0, ge=0),
):
    owner = _owner(request)
    store = _store()
    try:
        store.get_task(owner.owner_id, task_id)
    except Exception as exc:
        return _handle_error(exc)

    accept = request.headers.get("accept", "")
    if "text/event-stream" not in accept:
        events = store.list_events(owner.owner_id, task_id, after_sequence=after_sequence)
        task = store.get_task(owner.owner_id, task_id)
        return {
            "task_id": task_id,
            "status": task["status"],
            "events": [_event_public(event) for event in events],
        }

    async def stream() -> AsyncIterator[str]:
        last = after_sequence
        while True:
            try:
                events = store.list_events(owner.owner_id, task_id, after_sequence=last)
                task = store.get_task(owner.owner_id, task_id)
            except NotFoundError:
                yield "event: error\ndata: " + json.dumps({"detail": "资源不存在或无权访问"}, ensure_ascii=False) + "\n\n"
                return
            except Exception:
                yield "event: error\ndata: " + json.dumps({"detail": "读取任务事件失败"}, ensure_ascii=False) + "\n\n"
                return
            for event in events:
                last = int(event["sequence"])
                yield "event: task_event\ndata: " + json.dumps(_event_public(event), ensure_ascii=False) + "\n\n"
            if task["status"] in STOP_STATUSES:
                yield "event: done\ndata: " + json.dumps(
                    {"task_id": task_id, "status": task["status"], "last_sequence": last},
                    ensure_ascii=False,
                ) + "\n\n"
                return
            if await request.is_disconnected():
                return
            # Keep the connection warm without fabricating progress.
            yield ": keep-alive\n\n"
            await asyncio.sleep(0.3)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/tasks/{task_id}/cancel")
def cancel_task(request: Request, task_id: str):
    owner = _owner(request)
    store = _store()
    try:
        before = store.get_task(owner.owner_id, task_id)
        if before.get("research_mode") == "openmuse_shared":
            raise HTTPException(status_code=409, detail="openmuse_shared_cancel_required")
        already_terminal = before["status"] in TERMINAL
        task = store.request_cancel(owner.owner_id, task_id)
    except Exception as exc:
        return _handle_error(exc)
    cancelled_execution = False if already_terminal else _executor().cancel(task_id)
    return {
        "task": _task_public(store.get_task(owner.owner_id, task_id)),
        "cancel": {
            "requested": not already_terminal,
            "already_terminal": already_terminal,
            "execution_signal_sent": cancelled_execution,
            "note": (
                "取消是请求而非撤销保证；已发出的外部请求可能已产生影响。"
                if not already_terminal
                else "任务已结束，无需取消。"
            ),
        },
    }


# ---------------------------------------------------------------- memories
@router.get("/memories")
def list_memories(
    request: Request,
    kind: Optional[str] = None,
    bot_id: Optional[str] = Query(default=None, max_length=200),
    include_legacy: bool = Query(default=False),
):
    owner = _owner(request)
    store = _store()
    try:
        memories = store.list_memories(owner.owner_id, kind=kind, bot_id=bot_id, include_legacy=include_legacy)
        candidates = store.list_candidates(owner.owner_id, status="pending", bot_id=bot_id, include_legacy=include_legacy)
        latest = store.latest_deletion_job(owner.owner_id)
    except Exception as exc:
        return _handle_error(exc)
    return {
        "memories": memories,
        "pending_candidates": candidates,
        "latest_deletion": latest,
        "cache": {"context_scope": "confirmed_only"},
    }


@router.get("/memories/{memory_id}/versions")
def memory_versions(request: Request, memory_id: str):
    owner = _owner(request)
    try:
        versions = _store().memory_versions(owner.owner_id, memory_id)
    except Exception as exc:
        return _handle_error(exc)
    return {"memory_id": memory_id, "versions": versions}


@router.patch("/memories/{memory_id}")
def patch_memory(request: Request, memory_id: str, payload: MemoryPatchRequest):
    owner = _owner(request)
    try:
        memory = _store().patch_memory(
            owner.owner_id,
            memory_id,
            expected_version=payload.expected_version,
            text=payload.text,
            source_text=payload.source_text,
        )
    except Exception as exc:
        return _handle_error(exc)
    return {"memory": memory}


@router.delete("/memories/{memory_id}")
def delete_memory(
    request: Request,
    memory_id: str,
    request_id: Optional[str] = Query(default=None, max_length=200),
):
    owner = _owner(request)
    store = _store()
    request_id = (request_id or request.headers.get("Idempotency-Key", "") or uuid.uuid4().hex)[:200]
    try:
        job = store.request_deletion(
            owner.owner_id,
            scope="single",
            target_ids=[memory_id],
            request_id=request_id,
        )
    except Exception as exc:
        return _handle_error(exc)
    return {"deletion": job}


@router.delete("/memories")
def delete_all_memories(request: Request, payload: DeleteAllRequest):
    owner = _owner(request)
    store = _store()
    request_id = (payload.request_id or request.headers.get("Idempotency-Key", "") or uuid.uuid4().hex)[:200]
    try:
        job = store.request_deletion(
            owner.owner_id, scope="all", target_ids=None, request_id=request_id
        )
    except Exception as exc:
        return _handle_error(exc)
    return {"deletion": job}


@router.get("/deletion-jobs/{job_id}")
def get_deletion_job(request: Request, job_id: str):
    owner = _owner(request)
    try:
        job = _store().get_deletion_job(owner.owner_id, job_id)
    except Exception as exc:
        return _handle_error(exc)
    return {"deletion": job}


@router.get("/deletion-jobs")
def latest_deletion_job(request: Request):
    owner = _owner(request)
    return {"deletion": _store().latest_deletion_job(owner.owner_id)}


# --------------------------------------------------------------- candidates
@router.get("/memory-candidates")
def list_candidates(request: Request, status: str = Query(default="pending")):
    owner = _owner(request)
    try:
        candidates = _store().list_candidates(owner.owner_id, status=status)
    except Exception as exc:
        return _handle_error(exc)
    return {"candidates": candidates}


@router.post("/memory-candidates/{candidate_id}/confirm")
def confirm_candidate(request: Request, candidate_id: str, payload: CandidateConfirmRequest):
    owner = _owner(request)
    key = (payload.idempotency_key or request.headers.get("Idempotency-Key", "") or uuid.uuid4().hex)[:200]
    try:
        candidate, memory = _store().confirm_candidate(
            owner.owner_id,
            candidate_id,
            expected_version=payload.expected_version,
            final_text=payload.final_text,
            idempotency_key=key,
        )
    except Exception as exc:
        return _handle_error(exc)
    return {"candidate": candidate, "memory": memory}


@router.post("/memory-candidates/{candidate_id}/dismiss")
def dismiss_candidate(request: Request, candidate_id: str, payload: CandidateDismissRequest):
    owner = _owner(request)
    key = (payload.idempotency_key or request.headers.get("Idempotency-Key", "") or uuid.uuid4().hex)[:200]
    try:
        candidate = _store().dismiss_candidate(
            owner.owner_id,
            candidate_id,
            expected_version=payload.expected_version,
            idempotency_key=key,
        )
    except Exception as exc:
        return _handle_error(exc)
    return {"candidate": candidate}
