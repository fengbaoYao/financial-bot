"""Provider adapters for the capability-based data layer.

yfinance remains the US/HK adapter. Fuyao / 同花顺 is the A-share provider added
by explicit product decision on 2026-10-30; it is inert without FUYAO_API_KEY.
"""

from api.datasources.yfinance_provider import (
    NotSupportedSymbol,
    YFinanceProvider,
    YFinanceUnavailable,
    to_yahoo_symbol,
)

__all__ = [
    "NotSupportedSymbol",
    "YFinanceProvider",
    "YFinanceUnavailable",
    "to_yahoo_symbol",
]
from api.datasources.fuyao_provider import (
    FuyaoError,
    FuyaoNotConfigured,
    FuyaoProvider,
    FuyaoUnavailable,
    FuyaoUnsupportedSymbol,
    get_fuyao_provider,
    to_fuyao_thscode,
)

__all__ += [
    "FuyaoError",
    "FuyaoNotConfigured",
    "FuyaoProvider",
    "FuyaoUnavailable",
    "FuyaoUnsupportedSymbol",
    "get_fuyao_provider",
    "to_fuyao_thscode",
]

from api.datasources import health, quality, validation

__all__ += ["health", "quality", "validation"]

from api.datasources.ifind_provider import (
    IfindProvider,
    IfindUnavailable,
    configured as ifind_configured,
    history_quota_exhausted,
    history_quota_user_note,
    to_ths_code,
)

__all__ += [
    "IfindProvider",
    "IfindUnavailable",
    "ifind_configured",
    "history_quota_exhausted",
    "history_quota_user_note",
    "to_ths_code",
]
