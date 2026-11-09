"""Feature flags for the staged yfinance rollout."""

from __future__ import annotations

import os


def _enabled(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def yfinance_us_hk_fundamentals() -> bool:
    """Phase 3 additive capability: US/HK financials and analyst estimates.

    This does not replace the existing quote/news path; it only enables the new
    fundamentals/estimates tools for US/HK. Set YFINANCE_US_HK_FUNDAMENTALS=0 to
    fall back to the legacy "not configured" tool result.
    """
    return _enabled("YFINANCE_US_HK_FUNDAMENTALS", "1")


def yfinance_primary_us_hk() -> bool:
    """Phase 3b: use the adapter for US/HK history, with the old yf.download path as fallback."""
    return _enabled("YFINANCE_PRIMARY_US_HK", "1")


def yfinance_a_share_fallback() -> bool:
    """Phase 4: when all existing A-share paths fail, try the yfinance adapter.

    This is a fallback only; the existing Sina/Tencent/akshare path remains the
    primary A-share source.
    """
    return _enabled("YFINANCE_A_SHARE_FALLBACK", "1")


def fuyao_enabled() -> bool:
    """Whether the Fuyao A-share adapter may be used.

    The adapter itself is inert without FUYAO_API_KEY; this flag allows an
    operator to disable it even when a key exists.
    """
    return _enabled("FUYAO_ENABLED", "1")


def fuyao_primary_a_share() -> bool:
    """Use Fuyao for A-share daily history before the legacy Sina/Tencent path."""
    return fuyao_enabled() and _enabled("FUYAO_PRIMARY_A_SHARE", "1")


def fuyao_financials() -> bool:
    """Use Fuyao financial statements before the legacy Sina statements path."""
    return fuyao_enabled() and _enabled("FUYAO_FINANCIALS", "1")


def ifind_enabled() -> bool:
    """Whether the iFinD adapter may be used at all (default on)."""
    return _enabled("IFIND_ENABLED", "1")


def ifind_primary() -> bool:
    """Use iFinD before every other provider for the markets it can map.

    The adapter is inert without IFIND_ACCESS_TOKEN, so this flag can stay on
    in every environment: an unconfigured deployment falls through at no cost.
    """
    return ifind_enabled() and _enabled("IFIND_PRIMARY", "1")


def ifind_history_enabled() -> bool:
    """Whether iFinD ``history_data`` (历史数据) may be called.

    Default on. When the monthly 历史数据 quota is exhausted, set
    ``IFIND_HISTORY_ENABLED=0`` (or rely on the -4318 circuit in
    ``IfindProvider``) so /work and /bot prefer realtime / basics / Fuyao.
    """
    return ifind_enabled() and _enabled("IFIND_HISTORY_ENABLED", "1")
