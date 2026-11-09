"""Financial Bot server domain: owner-scoped tasks, events, memory and deletion.

The package is intentionally framework-light. `api.main` mounts the router, while
tests can instantiate the store with an isolated SQLite file or a PostgreSQL DSN.
"""

from api.financial_bot.config import BotConfig
from api.financial_bot.store import FinancialBotStore

__all__ = ["BotConfig", "FinancialBotStore"]
