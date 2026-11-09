#!/usr/bin/env python3
"""Independent MausBot proxy v0.5.2 — thin-forward + mid-turn message/interrupt.

Does not modify financial-bot app services.
"""
from __future__ import annotations

import json
import os
import re
time = __import__("time")
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

UPSTREAM = os.environ.get("MAUSBOT_UPSTREAM", "http://127.0.0.1:18799").rstrip("/")
TOKEN_FILE = os.environ.get("MAUSBOT_TOKEN_FILE", "/opt/financial-bot/secrets/mausbot_token")
BOT_ID = os.environ.get("MAUSBOT_BOT_ID", "0638843a-1c82-45dc-9902-137925618c12")
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "MAUSBOT_ALLOWED_ORIGINS",
        ",".join(
            [
                "https://fengbaoyao.github.io",
                "https://vm-0-4-ubuntu.taild33970.ts.net",
                "https://vinyl-month-carefully-hawk.trycloudflare.com",
                "https://situated-bug-dvd-ranks.trycloudflare.com",
                "http://127.0.0.1:18800",
                "http://localhost:18800",
                "http://127.0.0.1:3000",
                "http://localhost:3000",
                "http://127.0.0.1:3001",
                "http://localhost:3001",
            ]
        ),
    ).split(",")
    if o.strip()
]
# Quick-tunnel hostnames drift; also accept trycloudflare + this tailnet Funnel host.
ALLOWED_ORIGIN_REGEX = os.environ.get(
    "MAUSBOT_ALLOWED_ORIGIN_REGEX",
    r"^https://([a-z0-9-]+\.trycloudflare\.com|vm-0-4-ubuntu\.taild33970\.ts\.net)$",
)
RATE_LIMIT = int(os.environ.get("MAUSBOT_RATE_LIMIT_PER_MIN", "30"))
ASK_TIMEOUT = float(os.environ.get("MAUSBOT_ASK_TIMEOUT_SEC", "180"))
MAX_TEXT = int(os.environ.get("MAUSBOT_MAX_TEXT", "2000"))
QUIET_AFTER_TURN_SEC = float(os.environ.get("MAUSBOT_QUIET_AFTER_TURN_SEC", "0.6"))
STREAM_QUIET_AFTER_TURN_SEC = float(os.environ.get("MAUSBOT_STREAM_QUIET_AFTER_TURN_SEC", "2.0"))
SAFE_ATTACH_RE = re.compile(r"^[\w.-]+$")
SAFE_UPSTREAM_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,160}$")

app = FastAPI(title="MausBot Proxy", version="0.5.2")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

_hits: dict[str, list[float]] = {}


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    if origin in ALLOWED_ORIGINS:
        return True
    try:
        return bool(re.match(ALLOWED_ORIGIN_REGEX, origin))
    except re.error:
        return False


def _client_ip(req: Request) -> str:
    xff = req.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return req.client.host if req.client else "unknown"


def _rate_limit(ip: str) -> None:
    now = time.time()
    window = _hits.setdefault(ip, [])
    _hits[ip] = [t for t in window if now - t < 60]
    if len(_hits[ip]) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests, try again later.")
    _hits[ip].append(now)


def _token() -> str:
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            tok = f.read().strip()
        if not tok:
            raise ValueError("empty")
        return tok
    except Exception as e:
        raise HTTPException(status_code=503, detail="MausBot credentials unavailable") from e


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _sanitize(text: str) -> str:
    t = text.strip()
    if not t:
        raise HTTPException(status_code=400, detail="text required")
    if len(t) > MAX_TEXT:
        raise HTTPException(status_code=400, detail="text too long")
    return t


def _path_id(value: str, label: str) -> str:
    if not SAFE_UPSTREAM_ID_RE.fullmatch(value or ""):
        raise HTTPException(status_code=400, detail=f"invalid {label}")
    return value


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _slim_attachments(raw: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list):
        return None
    out: list[dict[str, Any]] = []
    for item in raw[:24]:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        path = item.get("path")
        if not kind or not path:
            continue
        slim: dict[str, Any] = {
            "kind": str(kind),
            "path": str(path)[:500],
        }
        if item.get("mime"):
            slim["mime"] = str(item.get("mime"))[:120]
        if item.get("name"):
            slim["name"] = str(item.get("name"))[:200]
        out.append(slim)
    return out or None


def _trunc(val: Any, n: int) -> str | None:
    if val is None:
        return None
    s = str(val)
    if len(s) > n:
        return s[:n] + "…"
    return s


def _tool_preview(val: Any, n: int) -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        text = val
    else:
        try:
            text = json.dumps(val, ensure_ascii=False, default=str)
        except Exception:
            text = str(val)
    # Keep tool details useful while avoiding obvious credentials in the browser UI.
    text = re.sub(
        r"(?i)([\"']?\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|authorization)\b[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;]+)([\"']?)",
        r"\1[redacted]\3",
        text,
    )
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [redacted]", text)
    text = re.sub(r"\b(?:sk|rk)-[A-Za-z0-9_-]{16,}\b", "[redacted key]", text)
    text = re.sub(r"-----BEGIN [^-]+PRIVATE KEY-----.*?-----END [^-]+PRIVATE KEY-----", "[redacted private key]", text, flags=re.DOTALL)
    return _trunc(text, n)


def _slim_tool(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("name", "label"):
        if raw.get(key) is not None:
            out[key] = str(raw.get(key))[:200]
    for key, limit in (("summary", 400), ("input", 1200), ("output", 2400), ("error", 800)):
        preview = _tool_preview(raw.get(key), limit)
        if preview is not None:
            out[key] = preview
    if "ok" in raw:
        out["ok"] = bool(raw.get("ok")) if raw.get("ok") is not None else None
    if raw.get("partial") is not None:
        out["partial"] = bool(raw.get("partial"))
    if raw.get("clipped") is not None:
        out["clipped"] = bool(raw.get("clipped"))
    return out or None


def _slim_card(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    title = raw.get("title") or raw.get("question") or raw.get("prompt")
    if title:
        out["title"] = _trunc(title, 500)
    for key, limit in (
        ("subtitle", 1200),
        ("requestId", 160),
        ("requestType", 40),
        ("tool", 160),
        ("answered", 80),
        ("answeredText", 800),
        ("held", 500),
    ):
        if raw.get(key) is not None:
            out[key] = _trunc(raw.get(key), limit)
    for key in ("dismissed", "expired", "stopped"):
        if raw.get(key) is not None:
            out[key] = bool(raw.get(key))
    options = raw.get("options")
    if isinstance(options, list):
        labels: list[str] = []
        for opt in options[:24]:
            if isinstance(opt, str):
                labels.append(opt[:200])
            elif isinstance(opt, dict):
                labels.append(str(opt.get("label") or opt.get("text") or opt.get("id") or "")[:200])
        if labels:
            out["options"] = [x for x in labels if x]
    return out or None


def _slim_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Thin-forward wire message: keep UI-useful fields, drop huge payloads."""
    slim: dict[str, Any] = {
        "id": msg.get("id"),
        "role": msg.get("role"),
        "kind": msg.get("kind"),
        "text": (msg.get("text") or "")[:20000],
        "at": msg.get("at"),
        "turnId": msg.get("turnId"),
        "helperIntro": bool(msg.get("helperIntro")),
        "turnTerminal": msg.get("turnTerminal"),
    }
    atts = _slim_attachments(msg.get("attachments"))
    if atts:
        slim["attachments"] = atts
    digest = msg.get("digest")
    if isinstance(digest, dict):
        slim["digest"] = {
            "turnId": digest.get("turnId"),
            "threadId": digest.get("threadId"),
        }
    tool = _slim_tool(msg.get("tool"))
    if tool:
        slim["tool"] = tool
    if msg.get("steered") is not None:
        slim["steered"] = bool(msg.get("steered"))
    wait_line = msg.get("waitLine")
    if isinstance(wait_line, (str, dict)):
        slim["waitLine"] = wait_line if isinstance(wait_line, dict) else _trunc(wait_line, 500)
    elif wait_line is not None:
        slim["waitLine"] = _trunc(wait_line, 500)
    card = _slim_card(msg.get("card"))
    if card:
        slim["card"] = card
    return slim


_RUNTIME_DROP = {
    "raw",
    "command",
    "commandLine",
    "reads",
    "data",  # base64 image bytes
    "payload",
}


def _slim_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    """Forward runtime events for UI status; strip huge/binary fields."""
    out: dict[str, Any] = {}
    keep_keys = (
        "type",
        "eventId",
        "threadId",
        "turnId",
        "itemId",
        "requestId",
        "provider",
        "providerInstanceId",
        "createdAt",
        "title",
        "summary",
        "resource",
        "position",
        "waitedMs",
        "outcome",
        "streamKind",
        "delta",
        "itemType",
        "ok",
        "error",
        "stopReason",
        "activity",
        "tool",
        "file",
        "bytes",
        "target",
        "detail",
        "whole",
        "attempt",
        "delayMs",
        "reason",
        "requestType",
        "summaryCode",
        "text",
        "tokens",
        "synthetic",
    )
    for key in keep_keys:
        if key not in runtime:
            continue
        val = runtime.get(key)
        if key == "delta" and isinstance(val, str):
            out[key] = val[:8000]
        elif key in ("summary", "title", "text", "detail", "target", "file", "tool", "error", "reason", "resource"):
            out[key] = _trunc(val, 800) if val is not None else None
        elif key == "output" or key == "input":
            continue
        else:
            out[key] = val
    # input / output previews truncated
    if runtime.get("input") is not None:
        out["input"] = _trunc(runtime.get("input"), 400)
    if runtime.get("output") is not None:
        out["output"] = _trunc(runtime.get("output"), 800)
    holder = runtime.get("holder")
    if isinstance(holder, dict):
        out["holder"] = {
            "name": _trunc(holder.get("name"), 120),
            "task": _trunc(holder.get("task"), 200),
        }
    quiet = runtime.get("quiet")
    if isinstance(quiet, dict):
        out["quiet"] = {"kind": quiet.get("kind"), "at": quiet.get("at")}
    step = runtime.get("step")
    if isinstance(step, dict):
        out["step"] = {
            "activity": step.get("activity"),
            "target": _trunc(step.get("target"), 80),
            "detail": _trunc(step.get("detail"), 200),
        }
    # pass type always
    if "type" not in out and runtime.get("type"):
        out["type"] = runtime.get("type")
    return out


def _slim_bot_event(ev: dict[str, Any]) -> dict[str, Any]:
    bot = ev.get("bot") if isinstance(ev.get("bot"), dict) else ev
    if not isinstance(bot, dict):
        return {"id": ev.get("botId")}
    out: dict[str, Any] = {
        "id": bot.get("id") or ev.get("botId"),
        "busy": bot.get("busy"),
        "activity": bot.get("activity"),
        "waitingForTeammates": bot.get("waitingForTeammates"),
    }
    if bot.get("name"):
        out["name"] = str(bot.get("name"))[:120]
    if bot.get("title"):
        out["title"] = str(bot.get("title"))[:200]
    return out


async def _upstream_json(
    method: str,
    path: str,
    *,
    json_body: Any | None = None,
    timeout: float = 30.0,
    pass_client_errors: bool = False,
):
    headers = _headers()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.request(method, f"{UPSTREAM}{path}", headers=headers, json=json_body)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail="MausBot upstream unreachable") from e
    body: Any
    try:
        body = r.json()
    except Exception:
        body = {"raw": (r.text or "")[:500]}
    if r.status_code >= 400:
        detail = None
        if isinstance(body, dict):
            detail = body.get("error") or body.get("detail") or body.get("message")
        # Preserve 4xx for respond/queue cancel so the shell can show actionable errors.
        status = r.status_code if (pass_client_errors and 400 <= r.status_code < 500) else 502
        raise HTTPException(status_code=status, detail=str(detail or f"upstream {r.status_code}"))
    return body


class AskIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT)
    botId: str | None = None
    threadId: str | None = None


class TaskIn(BaseModel):
    botId: str | None = None
    title: str | None = None


class MessageIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT)
    botId: str | None = None
    threadId: str | None = None


class InterruptIn(BaseModel):
    botId: str | None = None
    threadId: str = Field(..., min_length=1)
    turnId: str | None = None


class RespondIn(BaseModel):
    requestId: str = Field(..., min_length=1, max_length=160)
    behavior: str = Field(..., pattern="^(allow|deny|answer)$")
    message: str | None = Field(default=None, max_length=MAX_TEXT)


class QueueCancelIn(BaseModel):
    threadId: str = Field(..., min_length=1, max_length=160)


def _collect_text_message(store: dict[str, dict[str, Any]], msg: dict[str, Any], user_msg_id: str | None) -> None:
    if not isinstance(msg, dict):
        return
    mid = msg.get("id")
    if not mid or mid == user_msg_id:
        return
    role = msg.get("role")
    kind = msg.get("kind")
    text = (msg.get("text") or "").strip()
    if role not in ("bot", "assistant"):
        return
    if kind not in (None, "text", "markdown", "options"):
        return
    if not text or text.startswith("[digest]"):
        return
    prev = store.get(mid)
    if prev and len(prev.get("text") or "") > len(text):
        text = prev["text"]
    entry: dict[str, Any] = {
        "id": mid,
        "role": "assistant",
        "kind": kind or "text",
        "text": text,
        "turnId": msg.get("turnId"),
        "at": msg.get("at"),
    }
    atts = _slim_attachments(msg.get("attachments"))
    if atts:
        entry["attachments"] = atts
    elif prev and prev.get("attachments"):
        entry["attachments"] = prev["attachments"]
    store[mid] = entry


async def _resolve_thread_and_post(
    client: httpx.AsyncClient,
    *,
    headers: dict[str, str],
    bot_id: str,
    thread_id: str | None,
    text: str,
) -> tuple[str | None, str | None, dict[str, Any]]:
    resolved = thread_id
    if not resolved:
        br = await client.get(f"{UPSTREAM}/api/bots", headers=headers)
        if br.status_code == 200:
            bots = (br.json() or {}).get("bots") or []
            me = next((b for b in bots if b.get("id") == bot_id), bots[0] if bots else None)
            if me:
                resolved = me.get("mainThreadId") or me.get("threadId")

    tr = await client.post(f"{UPSTREAM}/api/auth/stream-ticket", headers=headers, json={})
    if tr.status_code >= 400:
        raise HTTPException(status_code=502, detail="MausBot auth failed")
    ticket = (tr.json() or {}).get("ticket")
    if not ticket:
        raise HTTPException(status_code=502, detail="MausBot ticket missing")

    body: dict[str, Any] = {"text": text}
    if resolved:
        body["threadId"] = resolved
    mr = await client.post(f"{UPSTREAM}/api/bots/{bot_id}/messages", headers=headers, json=body)
    if mr.status_code == 409 and not resolved:
        raise HTTPException(status_code=409, detail="threadId required when bot has multiple threads")
    if mr.status_code not in (200, 202):
        detail = None
        try:
            detail = (mr.json() or {}).get("error")
        except Exception:
            detail = None
        raise HTTPException(status_code=502, detail=str(detail or f"MausBot rejected message ({mr.status_code})"))
    mr_json: dict[str, Any] = {}
    try:
        mr_json = mr.json() or {}
    except Exception:
        mr_json = {}
    user_msg_id = ((mr_json.get("message") or {}) if isinstance(mr_json, dict) else {}).get("id")
    resolved = mr_json.get("threadId") or resolved
    return resolved, user_msg_id, {"ticket": ticket, "message": mr_json.get("message")}


@app.get("/api/mausbot/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{UPSTREAM}/api/health")
            ok = r.status_code == 200
            try:
                body = r.json()
            except Exception:
                body = {"raw": r.text[:200]}
            return {"ok": ok, "upstream_status": r.status_code, "upstream_body": body if ok else None}
    except httpx.HTTPError:
        return JSONResponse(status_code=503, content={"ok": False, "error": "MausBot upstream unreachable"})


@app.get("/api/mausbot/bots")
async def bots(request: Request):
    _rate_limit(_client_ip(request))
    return await _upstream_json("GET", "/api/bots")


@app.get("/api/mausbot/routines")
async def routines(request: Request):
    _rate_limit(_client_ip(request))
    return await _upstream_json("GET", "/api/routines")


@app.get("/api/mausbot/config")
async def config(request: Request):
    _rate_limit(_client_ip(request))
    return await _upstream_json("GET", "/api/config")


@app.get("/api/mausbot/bots/{bot_id}/memory")
async def bot_memory(bot_id: str, request: Request):
    _rate_limit(_client_ip(request))
    return await _upstream_json("GET", f"/api/bots/{bot_id}/memory")


@app.get("/api/mausbot/threads/{thread_id}/messages")
async def thread_messages(thread_id: str, request: Request):
    _rate_limit(_client_ip(request))
    return await _upstream_json("GET", f"/api/threads/{thread_id}/messages")


@app.post("/api/mausbot/threads/{thread_id}/respond")
async def respond_to_request(thread_id: str, payload: RespondIn, request: Request):
    """Forward a live approval or question response to the owning thread."""
    origin = request.headers.get("origin")
    if origin and not _origin_allowed(origin):
        raise HTTPException(status_code=403, detail="Origin not allowed")
    _rate_limit(_client_ip(request))
    thread_id = _path_id(thread_id, "threadId")
    if payload.behavior == "answer" and not (payload.message or "").strip():
        raise HTTPException(status_code=400, detail="message required for answer")
    body: dict[str, Any] = {
        "requestId": payload.requestId,
        "behavior": payload.behavior,
    }
    if payload.message is not None:
        body["message"] = _sanitize(payload.message)
    return await _upstream_json(
        "POST",
        f"/api/threads/{thread_id}/respond",
        json_body=body,
        pass_client_errors=True,
    )


@app.delete("/api/mausbot/bots/{bot_id}/queue/{queue_id}")
async def cancel_queued_message(bot_id: str, queue_id: str, payload: QueueCancelIn, request: Request):
    """Remove a queued message before it drains so it can be edited safely."""
    origin = request.headers.get("origin")
    if origin and not _origin_allowed(origin):
        raise HTTPException(status_code=403, detail="Origin not allowed")
    _rate_limit(_client_ip(request))
    bot_id = _path_id(bot_id, "botId")
    queue_id = _path_id(queue_id, "queueId")
    thread_id = _path_id(payload.threadId, "threadId")
    return await _upstream_json(
        "DELETE",
        f"/api/bots/{bot_id}/queue/{queue_id}",
        json_body={"threadId": thread_id},
        pass_client_errors=True,
    )


@app.post("/api/mausbot/tasks")
async def create_task(payload: TaskIn, request: Request):
    _rate_limit(_client_ip(request))
    bot_id = (payload.botId or BOT_ID).strip() or BOT_ID
    body: dict[str, Any] = {}
    if payload.title and payload.title.strip():
        body["title"] = payload.title.strip()[:120]
    return await _upstream_json("POST", f"/api/bots/{bot_id}/tasks", json_body=body or {"title": "New thread"})


@app.get("/api/mausbot/attachments/{name}")
async def attachment(name: str, request: Request):
    origin = request.headers.get("origin")
    if origin and not _origin_allowed(origin):
        raise HTTPException(status_code=403, detail="Origin not allowed")
    _rate_limit(_client_ip(request))
    if not SAFE_ATTACH_RE.match(name or ""):
        raise HTTPException(status_code=400, detail="invalid attachment name")
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Accept": "*/*",
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(f"{UPSTREAM}/api/attachments/{name}", headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail="MausBot upstream unreachable") from e
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"attachment upstream {r.status_code}")
    content_type = r.headers.get("content-type") or "application/octet-stream"
    return Response(
        content=r.content,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": r.headers.get("content-disposition") or f'inline; filename="{name}"',
        },
    )


@app.post("/api/mausbot/message")
async def post_message(payload: MessageIn, request: Request):
    """Mid-turn send: post to upstream messages only (server queue / steer). No new SSE."""
    origin = request.headers.get("origin")
    if origin and not _origin_allowed(origin):
        raise HTTPException(status_code=403, detail="Origin not allowed")
    _rate_limit(_client_ip(request))
    text = _sanitize(payload.text)
    bot_id = (payload.botId or BOT_ID).strip() or BOT_ID
    thread_id = (payload.threadId or "").strip() or None
    headers = _headers()
    body: dict[str, Any] = {"text": text}
    if thread_id:
        body["threadId"] = thread_id
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            mr = await client.post(f"{UPSTREAM}/api/bots/{bot_id}/messages", headers=headers, json=body)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail="MausBot upstream unreachable") from e
    try:
        mr_json = mr.json() if mr.content else {}
    except Exception:
        mr_json = {"raw": (mr.text or "")[:500]}
    if mr.status_code not in (200, 202):
        detail = None
        if isinstance(mr_json, dict):
            detail = mr_json.get("error") or mr_json.get("detail") or mr_json.get("message")
        raise HTTPException(status_code=502, detail=str(detail or f"MausBot rejected message ({mr.status_code})"))
    if not isinstance(mr_json, dict):
        mr_json = {}
    msg = mr_json.get("message") if isinstance(mr_json.get("message"), dict) else None
    out: dict[str, Any] = {
        "ok": True,
        "threadId": mr_json.get("threadId") or thread_id,
        "message": _slim_message(msg) if msg else msg,
        "queued": bool(mr_json.get("queued")),
    }
    if mr_json.get("queueId") is not None:
        out["queueId"] = mr_json.get("queueId")
    return out


@app.post("/api/mausbot/interrupt")
async def interrupt_turn(payload: InterruptIn, request: Request):
    """Stop current turn: proxy POST /api/bots/{id}/interrupt."""
    origin = request.headers.get("origin")
    if origin and not _origin_allowed(origin):
        raise HTTPException(status_code=403, detail="Origin not allowed")
    _rate_limit(_client_ip(request))
    bot_id = (payload.botId or BOT_ID).strip() or BOT_ID
    thread_id = payload.threadId.strip()
    body: dict[str, Any] = {"threadId": thread_id}
    if payload.turnId and str(payload.turnId).strip():
        body["turnId"] = str(payload.turnId).strip()
    headers = _headers()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{UPSTREAM}/api/bots/{bot_id}/interrupt", headers=headers, json=body)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail="MausBot upstream unreachable") from e
    try:
        body_json = r.json() if r.content else {}
    except Exception:
        body_json = {"raw": (r.text or "")[:500]}
    return JSONResponse(status_code=r.status_code if r.status_code >= 400 else 200, content=body_json if isinstance(body_json, dict) else {"body": body_json})


@app.post("/api/mausbot/ask")
async def ask(payload: AskIn, request: Request):
    origin = request.headers.get("origin")
    if origin and not _origin_allowed(origin):
        raise HTTPException(status_code=403, detail="Origin not allowed")
    _rate_limit(_client_ip(request))
    text = _sanitize(payload.text)
    bot_id = (payload.botId or BOT_ID).strip() or BOT_ID
    thread_id = (payload.threadId or "").strip() or None
    headers = _headers()
    deadline = time.time() + ASK_TIMEOUT
    answers: dict[str, dict[str, Any]] = {}
    error = None
    turn_id = None
    turn_done = False
    turn_done_at = None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            thread_id, user_msg_id, _meta = await _resolve_thread_and_post(
                client, headers=headers, bot_id=bot_id, thread_id=thread_id, text=text
            )
            ticket = _meta["ticket"]

            async with client.stream(
                "GET",
                f"{UPSTREAM}/api/events?ticket={ticket}",
                headers={"Accept": "text/event-stream", "Authorization": headers["Authorization"]},
                timeout=ASK_TIMEOUT,
            ) as stream:
                buf = ""
                async for chunk in stream.aiter_text():
                    now = time.time()
                    if now > deadline:
                        error = "timeout waiting for MausBot reply"
                        break
                    if turn_done and turn_done_at and now - turn_done_at >= QUIET_AFTER_TURN_SEC:
                        break
                    buf += chunk
                    while "\n\n" in buf:
                        block, buf = buf.split("\n\n", 1)
                        data_lines = [ln[6:] for ln in block.splitlines() if ln.startswith("data: ")]
                        if not data_lines:
                            continue
                        raw = "\n".join(data_lines).strip()
                        if not raw or raw == "ping" or raw.startswith(":"):
                            continue
                        try:
                            ev = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        kind = ev.get("kind")
                        ev_thread = ev.get("threadId")
                        if ev_thread and thread_id and ev_thread != thread_id and kind in ("message", "message.patch", "runtime"):
                            runtime = ev.get("event") if kind == "runtime" else None
                            if not (isinstance(runtime, dict) and runtime.get("threadId") == thread_id):
                                if kind != "runtime":
                                    continue

                        if kind in ("message", "message.patch"):
                            msg = ev.get("message") or {}
                            if isinstance(msg, dict):
                                if not turn_id and msg.get("turnId"):
                                    turn_id = msg.get("turnId")
                                _collect_text_message(answers, msg, user_msg_id)
                                if msg.get("kind") == "digest":
                                    digest = msg.get("digest") or {}
                                    if not turn_id or digest.get("turnId") == turn_id or digest.get("threadId") == thread_id:
                                        turn_done = True
                                        turn_done_at = time.time()
                                if msg.get("turnTerminal") is True:
                                    if not turn_id or msg.get("turnId") == turn_id:
                                        turn_done = True
                                        turn_done_at = time.time()

                        if kind == "runtime":
                            runtime = ev.get("event") or {}
                            if isinstance(runtime, dict):
                                if runtime.get("threadId") and thread_id and runtime.get("threadId") != thread_id:
                                    continue
                                if not turn_id and runtime.get("turnId"):
                                    turn_id = runtime.get("turnId")
                                if runtime.get("type") == "turn.completed":
                                    if not turn_id or runtime.get("turnId") == turn_id:
                                        turn_done = True
                                        turn_done_at = time.time()
                                        if runtime.get("ok") is False:
                                            error = str(runtime.get("error") or "MausBot turn failed")

                        if kind in ("error", "turn.error"):
                            error = str(ev.get("error") or ev.get("text") or "MausBot error")
                            turn_done = True
                            turn_done_at = time.time()

                    if error and not answers:
                        break
    except HTTPException:
        raise
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="MausBot upstream unreachable")
    except Exception:
        raise HTTPException(status_code=500, detail="Proxy internal error")

    ordered = sorted(answers.values(), key=lambda m: (m.get("at") or 0, m.get("id") or ""))
    if error and not ordered:
        raise HTTPException(status_code=504, detail=error)
    if not ordered:
        raise HTTPException(status_code=504, detail="No reply from MausBot yet")
    texts = [m["text"] for m in ordered]
    return {
        "ok": True,
        "botId": bot_id,
        "threadId": thread_id,
        "turnId": turn_id,
        "answers": ordered,
        "answer": "\n\n".join(texts),
    }


@app.post("/api/mausbot/ask/stream")
async def ask_stream(payload: AskIn, request: Request):
    origin = request.headers.get("origin")
    if origin and not _origin_allowed(origin):
        raise HTTPException(status_code=403, detail="Origin not allowed")
    _rate_limit(_client_ip(request))
    text = _sanitize(payload.text)
    bot_id = (payload.botId or BOT_ID).strip() or BOT_ID
    thread_id = (payload.threadId or "").strip() or None

    async def event_gen() -> AsyncIterator[str]:
        nonlocal thread_id
        headers = _headers()
        deadline = time.time() + ASK_TIMEOUT
        answers: dict[str, dict[str, Any]] = {}
        error = None
        turn_id = None
        turn_done = False
        turn_done_at = None
        user_msg_id = None
        ticket = None

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                thread_id, user_msg_id, meta = await _resolve_thread_and_post(
                    client, headers=headers, bot_id=bot_id, thread_id=thread_id, text=text
                )
                ticket = meta["ticket"]
                yield _sse(
                    {
                        "kind": "hello",
                        "botId": bot_id,
                        "threadId": thread_id,
                        "userMessageId": user_msg_id,
                    }
                )
                if isinstance(meta.get("message"), dict):
                    yield _sse(
                        {
                            "kind": "user",
                            "threadId": thread_id,
                            "message": _slim_message(meta["message"]),
                        }
                    )

                async with client.stream(
                    "GET",
                    f"{UPSTREAM}/api/events?ticket={ticket}",
                    headers={"Accept": "text/event-stream", "Authorization": headers["Authorization"]},
                    timeout=ASK_TIMEOUT,
                ) as stream:
                    buf = ""
                    async for chunk in stream.aiter_text():
                        if await request.is_disconnected():
                            return
                        now = time.time()
                        if now > deadline:
                            error = "timeout waiting for MausBot reply"
                            break
                        if turn_done and turn_done_at and now - turn_done_at >= STREAM_QUIET_AFTER_TURN_SEC:
                            break
                        buf += chunk
                        while "\n\n" in buf:
                            block, buf = buf.split("\n\n", 1)
                            data_lines = [ln[6:] for ln in block.splitlines() if ln.startswith("data: ")]
                            if not data_lines:
                                continue
                            raw = "\n".join(data_lines).strip()
                            if not raw or raw == "ping" or raw.startswith(":"):
                                continue
                            try:
                                ev = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            kind = ev.get("kind")
                            ev_thread = ev.get("threadId")
                            if ev_thread and thread_id and ev_thread != thread_id and kind in ("message", "message.patch", "runtime"):
                                runtime = ev.get("event") if kind == "runtime" else None
                                if not (isinstance(runtime, dict) and runtime.get("threadId") == thread_id):
                                    if kind != "runtime":
                                        continue

                            if kind in ("message", "message.patch"):
                                msg = ev.get("message") or {}
                                if isinstance(msg, dict):
                                    if not turn_id and msg.get("turnId"):
                                        turn_id = msg.get("turnId")
                                    _collect_text_message(answers, msg, user_msg_id)
                                    # Full forward: activity / options / digest / text — shell renders.
                                    yield _sse(
                                        {
                                            "kind": kind,
                                            "threadId": thread_id or ev_thread,
                                            "message": _slim_message(msg),
                                        }
                                    )
                                    if msg.get("kind") == "digest":
                                        digest = msg.get("digest") or {}
                                        if not turn_id or digest.get("turnId") == turn_id or digest.get("threadId") == thread_id:
                                            turn_done = True
                                            turn_done_at = time.time()
                                    if msg.get("turnTerminal") is True:
                                        if not turn_id or msg.get("turnId") == turn_id:
                                            turn_done = True
                                            turn_done_at = time.time()

                            if kind == "runtime":
                                runtime = ev.get("event") or {}
                                if isinstance(runtime, dict):
                                    if runtime.get("threadId") and thread_id and runtime.get("threadId") != thread_id:
                                        continue
                                    if not turn_id and runtime.get("turnId"):
                                        turn_id = runtime.get("turnId")
                                    rtype = runtime.get("type")
                                    slim_rt = _slim_runtime(runtime)
                                    # Forward every runtime event for live status / tool UI.
                                    yield _sse(
                                        {
                                            "kind": "runtime",
                                            "threadId": runtime.get("threadId") or thread_id,
                                            "turnId": runtime.get("turnId") or turn_id,
                                            "event": slim_rt,
                                        }
                                    )
                                    if rtype == "content.delta":
                                        delta = runtime.get("delta")
                                        if isinstance(delta, str) and delta:
                                            yield _sse(
                                                {
                                                    "kind": "delta",
                                                    "threadId": runtime.get("threadId") or thread_id,
                                                    "turnId": runtime.get("turnId") or turn_id,
                                                    "streamKind": runtime.get("streamKind"),
                                                    "delta": delta,
                                                }
                                            )
                                    elif rtype == "turn.started":
                                        # Follow-up turn (server-side queue): keep SSE open.
                                        turn_id = runtime.get("turnId") or turn_id
                                        turn_done = False
                                        turn_done_at = None
                                        error = None
                                        yield _sse(
                                            {
                                                "kind": "turn",
                                                "phase": "started",
                                                "threadId": thread_id,
                                                "turnId": turn_id,
                                            }
                                        )
                                    elif rtype == "turn.completed":
                                        # Mark quiet window; do not close until quiet with no new turn.started.
                                        if runtime.get("turnId"):
                                            turn_id = runtime.get("turnId") or turn_id
                                        turn_done = True
                                        turn_done_at = time.time()
                                        if runtime.get("ok") is False:
                                            error = str(runtime.get("error") or "MausBot turn failed")
                                        yield _sse(
                                            {
                                                "kind": "turn",
                                                "phase": "completed",
                                                "threadId": thread_id,
                                                "turnId": runtime.get("turnId") or turn_id,
                                                "ok": runtime.get("ok", True),
                                                "error": runtime.get("error"),
                                            }
                                        )

                            # Bot busy / activity / waiting — thin forward for sidebar + status strip.
                            if kind == "bot" or kind == "bot.updated":
                                bot_payload = ev.get("bot") if isinstance(ev.get("bot"), dict) else ev
                                bot_id_ev = None
                                if isinstance(bot_payload, dict):
                                    bot_id_ev = bot_payload.get("id") or ev.get("botId")
                                if not bot_id_ev or bot_id_ev == bot_id:
                                    yield _sse(
                                        {
                                            "kind": "bot",
                                            "bot": _slim_bot_event(ev if isinstance(ev, dict) else {"bot": bot_payload}),
                                        }
                                    )

                            if kind == "bot.queued":
                                queues = ev.get("queues")
                                slim_q: dict[str, Any] = {}
                                if isinstance(queues, dict):
                                    for qtid, items in list(queues.items())[:20]:
                                        if not isinstance(items, list):
                                            continue
                                        slim_q[str(qtid)[:80]] = [
                                            {
                                                "id": (it or {}).get("id") if isinstance(it, dict) else None,
                                                "text": _trunc((it or {}).get("text"), 200) if isinstance(it, dict) else None,
                                            }
                                            for it in items[:10]
                                        ]
                                yield _sse({"kind": "bot.queued", "queues": slim_q, "threadId": thread_id})

                            if kind in ("interrupted", "turn.interrupted", "bot.interrupted"):
                                yield _sse(
                                    {
                                        "kind": "interrupted",
                                        "threadId": thread_id or ev_thread,
                                        "turnId": ev.get("turnId") or turn_id,
                                        "event": ev,
                                    }
                                )
                                turn_done = True
                                turn_done_at = time.time()

                            if kind in ("error", "turn.error"):
                                error = str(ev.get("error") or ev.get("text") or "MausBot error")
                                turn_done = True
                                turn_done_at = time.time()
                                yield _sse({"kind": "error", "error": error, "threadId": thread_id})

                        if error and not answers and turn_done:
                            break

        except HTTPException as he:
            detail = he.detail if isinstance(he.detail, str) else str(he.detail)
            yield _sse({"kind": "error", "error": detail, "status": he.status_code})
            yield _sse({"kind": "done", "ok": False, "botId": bot_id, "threadId": thread_id, "answers": []})
            return
        except httpx.HTTPError:
            yield _sse({"kind": "error", "error": "MausBot upstream unreachable"})
            yield _sse({"kind": "done", "ok": False, "botId": bot_id, "threadId": thread_id, "answers": []})
            return
        except Exception:
            yield _sse({"kind": "error", "error": "Proxy internal error"})
            yield _sse({"kind": "done", "ok": False, "botId": bot_id, "threadId": thread_id, "answers": []})
            return

        ordered = sorted(answers.values(), key=lambda m: (m.get("at") or 0, m.get("id") or ""))
        yield _sse(
            {
                "kind": "done",
                "ok": bool(ordered) and not (error and not ordered),
                "botId": bot_id,
                "threadId": thread_id,
                "turnId": turn_id,
                "answers": ordered,
                "error": error,
            }
        )

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/")
async def root():
    return {
        "service": "mausbot-proxy",
        "version": "0.5.2",
        "endpoints": [
            "/api/mausbot/health",
            "/api/mausbot/ask",
            "/api/mausbot/ask/stream",
            "/api/mausbot/message",
            "/api/mausbot/interrupt",
            "/api/mausbot/attachments/{name}",
            "/api/mausbot/bots",
            "/api/mausbot/bots/{botId}/queue/{queueId}",
            "/api/mausbot/routines",
            "/api/mausbot/config",
            "/api/mausbot/bots/{botId}/memory",
            "/api/mausbot/threads/{threadId}/messages",
            "/api/mausbot/threads/{threadId}/respond",
            "/api/mausbot/tasks",
        ],
        "note": "Independent of financial-bot; secrets not exposed",
    }
