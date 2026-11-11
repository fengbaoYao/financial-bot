"""Optional outbound channels for proactive notifications.

Channels are deliberately opt-in and configured by environment variables. The
in-app inbox is always written first; a failed webhook/email must never roll it
back or block the polling worker. Secrets are never logged.
"""

from __future__ import annotations

import json
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit
import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Any

logger = logging.getLogger(__name__)


def _post_public_https(url: str, data: bytes, headers: dict[str, str]) -> int:
    """Resolve once, reject non-public addresses and connect to the checked IP.

    TLS still verifies the original hostname. Redirects are never followed.
    """
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("invalid webhook URL")
    port = parsed.port or 443
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError("webhook target is not public")
    target = addresses[0][4][0]

    class PinnedHTTPSConnection(http.client.HTTPSConnection):
        def connect(self):
            raw = socket.create_connection((target, port), self.timeout)
            try:
                self.sock = self._context.wrap_socket(raw, server_hostname=parsed.hostname)
            except BaseException:
                raw.close()
                raise

    conn = PinnedHTTPSConnection(parsed.hostname, port, timeout=8, context=ssl.create_default_context())
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    try:
        conn.request("POST", path, body=data, headers=headers)
        response = conn.getresponse()
        return response.status
    finally:
        conn.close()


def _webhook(notification: dict[str, Any], override_url: str | None = None) -> bool:
    url = (override_url or os.environ.get("FINANCIAL_BOT_WEBHOOK_URL", "")).strip()
    if not url:
        return False
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("FINANCIAL_BOT_WEBHOOK_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(
        {
            "type": "financial_bot.notification",
            "title": notification.get("title"),
            "body": notification.get("body"),
            "bot_id": notification.get("bot_id"),
            "monitor_id": notification.get("monitor_id"),
            "severity": notification.get("severity"),
            "created_at": notification.get("created_at"),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        status = _post_public_https(url, data, headers)
        return 200 <= status < 300
    except Exception as exc:  # noqa: BLE001 - outbound channels are best effort
        logger.warning("Notification webhook failed: %s", type(exc).__name__)
        return False


def _email(notification: dict[str, Any], override_to: str | None = None) -> bool:
    recipient = (override_to or os.environ.get("FINANCIAL_BOT_NOTIFY_EMAIL", "")).strip()
    host = os.environ.get("SMTP_HOST", "").strip()
    if not recipient or not host:
        return False
    port = int(os.environ.get("SMTP_PORT", "587") or 587)
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    message = EmailMessage()
    message["Subject"] = str(notification.get("title") or "Financial Bot notification")
    message["From"] = os.environ.get("SMTP_FROM", user or "financial-bot@localhost")
    message["To"] = recipient
    message.set_content(str(notification.get("body") or ""))
    try:
        with smtplib.SMTP(host, port, timeout=8) as client:
            if port != 25:
                client.starttls()
            if user:
                client.login(user, password)
            client.send_message(message)
        return True
    except Exception as exc:  # noqa: BLE001 - outbound channels are best effort
        logger.warning("Notification email failed: %s", type(exc).__name__)
        return False


def dispatch_notification(notification: dict[str, Any], *, webhook_url: str | None = None, email_to: str | None = None) -> dict[str, bool]:
    return {"webhook": _webhook(notification, webhook_url), "email": _email(notification, email_to)}


def configured_results(results: dict[str, bool], *, webhook_url: str | None = None, email_to: str | None = None) -> dict[str, bool]:
    enabled = {
        "webhook": bool((webhook_url or os.environ.get("FINANCIAL_BOT_WEBHOOK_URL", "")).strip()),
        "email": bool((email_to or os.environ.get("FINANCIAL_BOT_NOTIFY_EMAIL", "")).strip()),
    }
    return {channel: ok for channel, ok in results.items() if enabled.get(channel, False)}


def dispatch_channel(
    notification: dict[str, Any],
    channel: str,
    *,
    webhook_url: str | None = None,
    email_to: str | None = None,
) -> bool:
    """Retry one recorded channel without resending the other channel."""
    normalized = str(channel or "").removeprefix("manual_digest_").removeprefix("digest_")
    if normalized == "webhook":
        return _webhook(notification, webhook_url)
    if normalized == "email":
        return _email(notification, email_to)
    return False
