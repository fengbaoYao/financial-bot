"""Provider registry.

Policy history: yfinance was frozen as the last provider in the initial rollout.
On 2026-10-30 the product owner explicitly approved Fuyao / 同花顺 as the one
A-share exception. On 2026-11-02 iFinD (同花顺 quantapi) was approved as the**highest-priority** source for every capability it covers; Fuyao, sina,
tencent, akshare and yfinance keep their roles as fallbacks. The registry
remains the single place where new providers must be declared.
"""

from __future__ import annotations

FROZEN_AFTER = "fuyao"

PROVIDER_REGISTRY = {
    "ifind": {
        "status": "primary",
        "priority": 0,
        "markets": ["cn", "hk"],
        "capabilities": [
            "quote",
            "history",
            "financials",
            "financial_indicators",
            "valuations",
            "announcements",
            "news",
            "calendar",
            "edb",
        ],
        "auth": "IFIND_ACCESS_TOKEN",
        "note": (
            "Highest-priority source (2026-11-02). A-share and HK symbols map "
            "deterministically; US needs an exchange suffix and stays on yfinance "
            "until that mapping is confirmed."
        ),
    },
    "fuyao": {
        "status": "newest",
        "markets": ["cn"],
        "capabilities": [
            "ticker_search",
            "quote",
            "history",
            "financials",
            "financial_indicators",
            "valuations",
            "calendar",
            "index_constituents",
        ],
        "auth": "FUYAO_API_KEY",
        "note": "A-share only; explicit 2026-10-30 exception to the yfinance freeze.",
    },
    "yfinance": {
        "status": "newest",
        "markets": ["us", "hk", "cn"],
        "capabilities": ["quote", "history", "financials", "estimates", "actions", "news"],
        "auth": None,
    },
    "legacy_a_share": {
        "status": "legacy",
        "markets": ["cn"],
        "capabilities": ["quote", "history", "financials", "news", "announcements"],
        "auth": None,
    },
    "legacy_us_sec": {
        "status": "legacy",
        "markets": ["us"],
        "capabilities": ["filings"],
        "auth": "SEC_USER_AGENT",
    },
    "legacy_news_search": {
        "status": "legacy",
        "markets": ["us", "hk", "cn"],
        "capabilities": ["news", "web_search"],
        "auth": "optional keys",
    },
}


def registered_provider(name: str) -> bool:
    return name in PROVIDER_REGISTRY


def provider_priority(name: str) -> int:
    """Lower runs first. Unregistered providers sort last."""
    entry = PROVIDER_REGISTRY.get(name)
    if not entry:
        return 999
    if "priority" in entry:
        return int(entry["priority"])
    return 100 if entry.get("status") == "legacy" else 50


def providers_by_priority(capability: str | None = None) -> list[str]:
    names = [
        name
        for name, entry in PROVIDER_REGISTRY.items()
        if capability is None or capability in entry.get("capabilities", [])
    ]
    return sorted(names, key=lambda name: (provider_priority(name), name))
