"""Capability-specific data-source quality policy.

There is no single "best" provider: CNINFO is the right source for official
announcements but irrelevant for quotes, while yfinance is useful for US/HK
prices and weak for Chinese filings.  This module records the decision for each
capability and gives the runtime a stable priority list.

Quality dimensions (1 = weak, 5 = strong):
  authority       official/primary source vs aggregator vs scrape
  structure       typed fields, units, timestamps, adjustment semantics
  freshness       real-time / delayed / historical
  stability       observed timeouts, rate limits, key fragility
  provenance      request IDs, raw-response hash, explicit gaps
  coverage        markets, history depth, instruments
  compliance      server-side auth and data-use clarity

The weighted score is advisory.  ``compliance_ok`` and freshness gates can veto
a source regardless of score.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable

WEIGHTS = {
    "authority": 0.26,
    "structure": 0.20,
    "freshness": 0.15,
    "stability": 0.15,
    "provenance": 0.14,
    "coverage": 0.10,
}


@dataclass(frozen=True)
class QualityProfile:
    provider: str
    capability: str
    tier: str  # official / primary / secondary / fallback / discovery
    authority: int
    structure: int
    freshness: int
    stability: int
    provenance: int
    coverage: int
    compliance_ok: bool
    note: str

    @property
    def score(self) -> float:
        return round(
            sum(getattr(self, key) * weight for key, weight in WEIGHTS.items()) / 5 * 100,
            1,
        )

    def as_dict(self) -> dict:
        return {**asdict(self), "score": self.score}


PROFILES: tuple[QualityProfile, ...] = (
    # A-share prices: Fuyao is structured and has a documented contract.  Sina
    # and Tencent are long-running free fallbacks; akshare is a wrapper over
    # several fragile public endpoints.  yfinance A-share is last resort only.
    # iFinD (同花顺 quantapi) is the product owner's designated top source
    # (2026-11-02): licensed contract, typed fields, explicit adjustment and
    # request telemetry, and one token for A-share and HK.
    QualityProfile("ifind", "a_share_history", "primary", 5, 5, 4, 4, 5, 5, True,
                   "Licensed iFinD history_data; explicit CPS adjustment, currency and data_as_of."),
    QualityProfile("ifind", "a_share_quote", "primary", 5, 5, 5, 4, 5, 5, True,
                   "Licensed real_time_quotation snapshot with trade date/time and preClose."),
    QualityProfile("ifind", "a_share_financials", "primary", 5, 5, 3, 4, 5, 5, True,
                   "Licensed basic_data_service / date_sequence fundamentals."),
    QualityProfile("ifind", "a_share_valuations", "primary", 5, 5, 4, 4, 5, 4, True,
                   "Licensed valuation indicators on the same contract as prices."),
    QualityProfile("ifind", "a_share_announcements", "primary", 5, 4, 4, 4, 5, 4, True,
                   "Licensed report_query; titles and pdf links with publication dates."),
    QualityProfile("fuyao", "a_share_history", "primary", 4, 5, 4, 5, 5, 4, True,
                   "A-share licensed/structured provider; qfq daily bars and explicit data_as_of."),
    QualityProfile("sina", "a_share_history", "fallback", 3, 3, 3, 3, 2, 3, True,
                   "Long-running public K-line endpoint; unadjusted by default, no request id."),
    QualityProfile("tencent", "a_share_history", "fallback", 3, 3, 3, 3, 2, 3, True,
                   "Public qfq K-line endpoint; useful fallback, no contract."),
    QualityProfile("akshare", "a_share_history", "fallback", 3, 3, 3, 1, 2, 4, True,
                   "Wrapper over public endpoints; broad coverage but brittle."),
    QualityProfile("yfinance", "a_share_history", "last_resort", 2, 3, 3, 3, 3, 2, True,
                   "Only after all A-share paths fail; raw close / no Chinese announcements."),

    QualityProfile("fuyao", "a_share_quote", "primary", 4, 5, 5, 5, 5, 4, True,
                   "Delayed/last snapshot with currency and timestamp; not exchange real-time."),
    QualityProfile("sina", "a_share_quote", "fallback", 3, 3, 4, 3, 2, 3, True,
                   "Public quote used by legacy analysis; less explicit schema."),
    QualityProfile("tencent", "a_share_quote", "fallback", 3, 3, 4, 3, 2, 3, True,
                   "Public quote; useful fallback."),

    QualityProfile("fuyao", "a_share_financials", "primary", 4, 5, 3, 5, 5, 4, True,
                   "Consolidated statements with period_end_ms and currency; annual API."),
    QualityProfile("sina", "a_share_financials", "fallback", 3, 3, 3, 3, 2, 3, True,
                   "Sina statement table; long-lived fallback without request id."),

    QualityProfile("fuyao", "a_share_valuations", "primary", 4, 5, 4, 5, 5, 3, True,
                   "PE/PB/PS/PCF snapshot; no historical valuation or fair-value verdict."),
    QualityProfile("fuyao", "a_share_calendar", "primary", 4, 5, 4, 4, 5, 3, True,
                   "A-share trading days with millisecond and readable date."),
    QualityProfile("fuyao", "a_share_index_constituents", "secondary", 4, 4, 4, 4, 4, 4, True,
                   "THS/standard index constituents; official CSI/CNI remain primary for standard indices."),
    QualityProfile("csi", "a_share_index_constituents", "official", 5, 4, 3, 3, 3, 3, True,
                   "Official CSI published constituent weight file."),
    QualityProfile("cni", "a_share_index_constituents", "fallback", 5, 4, 3, 2, 3, 3, True,
                   "Official CNI published constituent weight file."),

    QualityProfile("cninfo", "a_share_announcements", "official", 5, 4, 4, 3, 3, 3, True,
                   "Official CNINFO disclosure titles/links; body is not read."),
    QualityProfile("eastmoney", "a_share_announcements", "secondary", 3, 3, 4, 3, 2, 3, True,
                   "Aggregated announcements; useful fallback, not official."),
    QualityProfile("ths", "a_share_consensus", "secondary", 3, 3, 3, 3, 2, 2, True,
                   "THS consensus EPS page; analyst count/min/max and coverage gaps."),
    QualityProfile("eastmoney_forecast", "a_share_consensus", "fallback", 3, 3, 3, 3, 2, 3, True,
                   "Eastmoney consensus EPS snapshot; report count but no analyst min/max."),
    QualityProfile("fuyao", "a_share_financial_indicators", "primary", 4, 5, 3, 4, 5, 3, True,
                   "Five indicator groups; raw strings preserve precision."),

    QualityProfile("yfinance", "us_hk_history", "primary", 4, 4, 4, 4, 3, 4, True,
                   "US/HK Yahoo history and quotes; no Chinese filings."),
    QualityProfile("yfinance", "us_hk_fundamentals", "primary", 4, 4, 3, 4, 3, 4, True,
                   "US/HK Yahoo statements; no Chinese filings."),
    QualityProfile("finnhub", "us_hk_history", "fallback", 3, 4, 4, 3, 3, 2, True,
                   "US quote fallback when FINNHUB_API_KEY exists."),
    QualityProfile("eastmoney", "us_hk_fundamentals", "fallback", 3, 3, 3, 3, 2, 3, True,
                   "Eastmoney US/HK statement fallback when yfinance fails; secondary aggregator."),
    QualityProfile("finnhub", "us_news", "primary", 4, 4, 4, 3, 3, 3, True,
                   "US company/general news when key exists."),
    QualityProfile("sec", "filings", "official", 5, 5, 4, 4, 5, 3, True,
                   "SEC EDGAR submissions/companyfacts; needs SEC_USER_AGENT."),
    QualityProfile("federal_register", "us_regulatory", "official", 5, 4, 4, 3, 3, 2, True,
                   "US Federal Register API; only for regulatory queries."),

    QualityProfile("eastmoney", "a_share_news", "secondary", 3, 3, 4, 3, 2, 4, True,
                   "Direct stock news, fast and broad; aggregator."),
    QualityProfile("cls", "a_share_news", "secondary", 3, 3, 4, 3, 2, 3, True,
                   "Chinese newswire; market-wide, needs entity filtering."),
    QualityProfile("sina", "a_share_news", "fallback", 3, 3, 3, 3, 2, 3, True,
                   "Market-wide feed; fallback only."),
    QualityProfile("ths_news", "a_share_news", "fallback", 3, 3, 3, 3, 2, 3, True,
                   "Market-wide feed; fallback only."),

    QualityProfile("brave", "web_search", "primary", 3, 4, 3, 4, 3, 4, True,
                   "Licensed search API when key exists; result snippets are evidence leads, not facts."),
    QualityProfile("google_news_rss", "web_search", "fallback", 2, 3, 3, 3, 2, 4, True,
                   "RSS discovery; redirect URLs and aggregator metadata."),
    QualityProfile("bing_rss", "web_search", "fallback", 2, 3, 3, 3, 2, 3, True,
                   "RSS discovery fallback."),
    QualityProfile("duckduckgo", "web_search", "discovery", 2, 2, 3, 1, 1, 3, True,
                   "HTML scrape; discovery only, can be challenged."),
    QualityProfile("fuyao", "ticker_search", "primary", 4, 5, 4, 5, 5, 4, True,
                   "Structured cross-market ticker search; only used when the caller has a key."),
    QualityProfile("local", "ticker_search", "primary", 4, 3, 3, 5, 3, 4, True,
                   "Local stock pool; deterministic and offline."),
    QualityProfile("eastmoney", "ticker_search", "secondary", 3, 3, 4, 3, 2, 4, True,
                   "Eastmoney suggest; useful online fallback."),
    QualityProfile("sina", "ticker_search", "fallback", 3, 2, 3, 3, 1, 3, True,
                   "Sina suggest; legacy fallback."),
)

_BY_KEY = {(profile.provider, profile.capability): profile for profile in PROFILES}

CAPABILITY_PRIORITY: dict[str, list[str]] = {
    # iFinD first everywhere it is licensed to serve (2026-11-02 policy).
    "a_share_history": ["ifind", "fuyao", "sina", "tencent", "akshare", "yfinance"],
    "a_share_quote": ["ifind", "fuyao", "sina", "tencent"],
    "a_share_financials": ["ifind", "fuyao", "sina"],
    "a_share_financial_indicators": ["ifind", "fuyao"],
    "a_share_valuations": ["ifind", "fuyao"],
    "a_share_calendar": ["ifind", "fuyao"],
    "a_share_announcements": ["ifind", "cninfo", "eastmoney"],
    "a_share_consensus": ["ths", "eastmoney_forecast"],
    "a_share_index_constituents": ["csi", "cni", "fuyao"],
    "a_share_news": ["cninfo", "eastmoney", "cls", "sina", "ths_news"],
    # HK is covered by iFinD; US stays on yfinance until the exchange suffix
    # mapping is confirmed (see api/datasources/ifind_provider.to_ths_code).
    "us_hk_history": ["ifind", "yfinance", "finnhub"],
    "us_hk_fundamentals": ["yfinance", "eastmoney"],
    "us_news": ["finnhub"],
    "filings": ["sec"],
    "web_search": ["brave", "google_news_rss", "bing_rss", "duckduckgo"],
    "ticker_search": ["local", "fuyao", "eastmoney", "sina"],
}

TIER_LABELS = {
    "official": "官方",
    "primary": "主源",
    "secondary": "次源",
    "fallback": "降级源",
    "last_resort": "最后兜底",
    "discovery": "仅发现",
}


def profile(provider: str, capability: str) -> QualityProfile | None:
    return _BY_KEY.get((provider, capability))


def priority(capability: str) -> list[str]:
    return list(CAPABILITY_PRIORITY.get(capability, []))


def score(provider: str, capability: str) -> float:
    item = profile(provider, capability)
    return item.score if item else 0.0


def tier(provider: str, capability: str) -> str:
    item = profile(provider, capability)
    return item.tier if item else "unknown"


def quality_snapshot() -> list[dict]:
    return [profile.as_dict() for profile in sorted(PROFILES, key=lambda p: (p.capability, p.provider))]


def summary_for(capabilities: Iterable[str]) -> dict[str, list[dict]]:
    return {
        capability: [
            {
                "provider": name,
                "tier": tier(name, capability),
                "score": score(name, capability),
            }
            for name in priority(capability)
        ]
        for capability in capabilities
    }
