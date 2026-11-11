"""Opt-in, read-only OpenMuse health client for trusted private deployments."""

from __future__ import annotations

import ipaddress
import math
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "fc00::/7",
        "::1/128",
    )
)


@dataclass(frozen=True)
class OpenMuseHealth:
    """Sanitized subset of the unauthenticated ``GET /api/health`` response."""

    available: bool
    mode: str | None = None
    agent_configured: bool | None = None
    browser_configured: bool | None = None
    shared_research_enabled: bool = False
    error: str | None = None


class OpenMuseClient:
    """Query OpenMuse health without authenticating or invoking computer routes.

    The client is disabled unless explicitly enabled. For SSRF safety it accepts
    exact localhost and private IP literals only; other DNS names are
    intentionally not resolved or contacted. Environment proxies are disabled.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        base_url: str | None = None,
        timeout: tuple[float, float] = (2.0, 2.0),
    ) -> None:
        self.enabled = enabled
        self.base_url = base_url.strip().rstrip("/") if base_url else None
        try:
            connect_timeout, read_timeout = (float(value) for value in timeout)
        except (TypeError, ValueError):
            connect_timeout = read_timeout = 2.0
        if not math.isfinite(connect_timeout) or not math.isfinite(read_timeout):
            connect_timeout = read_timeout = 2.0
        self.timeout = (
            max(0.1, min(10.0, connect_timeout)),
            max(0.1, min(10.0, read_timeout)),
        )

    @classmethod
    def from_env(cls) -> "OpenMuseClient":
        """Build from server-only environment settings; disabled by default."""
        enabled = os.environ.get("OPENMUSE_ENABLED", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        base_url = os.environ.get("OPENMUSE_BASE_URL") or None
        raw_timeout = os.environ.get("OPENMUSE_TIMEOUT_SECONDS", "2").strip()
        try:
            timeout_seconds = float(raw_timeout)
        except ValueError:
            timeout_seconds = 2.0
        timeout_seconds = max(0.1, min(10.0, timeout_seconds))
        return cls(enabled=enabled, base_url=base_url, timeout=(timeout_seconds, timeout_seconds))

    def check_health(self) -> OpenMuseHealth:
        """Return a bounded health result without following redirects or leaking errors."""
        if not self.enabled:
            return OpenMuseHealth(available=False, error="disabled")

        health_url = self._health_url()
        if health_url is None:
            return OpenMuseHealth(available=False, error="invalid_configuration")

        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    health_url,
                    timeout=self.timeout,
                    allow_redirects=False,
                )
        except Exception:
            # Do not return exception text: adapters may include URLs or headers.
            return OpenMuseHealth(available=False, error="unavailable")

        if response.status_code != 200:
            return OpenMuseHealth(available=False, error="http_error")
        try:
            payload = response.json()
        except Exception:
            return OpenMuseHealth(available=False, error="invalid_response")

        if not isinstance(payload, dict):
            return OpenMuseHealth(available=False, error="invalid_response")
        mode = payload.get("mode")
        agent_configured = payload.get("agentConfigured")
        browser_configured = payload.get("browserConfigured")
        shared_research_enabled = payload.get("sharedResearchEnabled", False)
        if (
            payload.get("ok") is not True
            or not isinstance(mode, str)
            or mode not in {"sample", "live"}
            or type(agent_configured) is not bool
            or type(browser_configured) is not bool
            or type(shared_research_enabled) is not bool
        ):
            return OpenMuseHealth(available=False, error="invalid_response")

        return OpenMuseHealth(
            available=True,
            mode=mode,
            agent_configured=agent_configured,
            browser_configured=browser_configured,
            shared_research_enabled=shared_research_enabled,
        )

    def _health_url(self) -> str | None:
        if not self.base_url:
            return None
        try:
            parsed = urlsplit(self.base_url)
            host = parsed.hostname
            parsed.port  # Access validates malformed port syntax.
        except ValueError:
            return None
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
        if not self._is_allowed_host(host):
            return None
        return f"{self.base_url}/api/health"

    @staticmethod
    def _is_allowed_host(host: str) -> bool:
        normalized = host.rstrip(".").lower()
        if normalized == "localhost":
            return True
        try:
            address = ipaddress.ip_address(normalized)
        except ValueError:
            return False
        return any(
            address.version == network.version and address in network
            for network in _PRIVATE_NETWORKS
        )
