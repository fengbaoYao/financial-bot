"""Local-only research data adapters.

Scope: explicitly local / internal research use. These adapters call public
web endpoints without credentials and must never be enabled for a hosted
multi-user trial without a separate data-authorization review.

The implementation is a small, readable adaptation of the provider patterns in
Vibe-Research (MIT; data-layer portions adapted there from Apache-2.0 upstreams)
and is intentionally limited to four high-value endpoints from the assessment in
docs/vibe-research-data-sources.md. No third-party code is vendored wholesale.
"""

from api.local_research.providers import (
    LocalDataError,
    consensus_estimates,
    cninfo_announcements,
    financial_statements,
    index_constituents,
)
from api.local_research.scope import local_research_enabled, local_research_scope

__all__ = [
    "LocalDataError",
    "consensus_estimates",
    "cninfo_announcements",
    "financial_statements",
    "index_constituents",
    "local_research_enabled",
    "local_research_scope",
]

from api.local_research.providers import eastmoney_announcements

__all__ += ["eastmoney_announcements"]
