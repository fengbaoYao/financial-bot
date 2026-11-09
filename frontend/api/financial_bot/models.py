"""Request/response contracts for Financial Bot endpoints."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class UIContext(BaseModel):
    """A bounded, frontend-reported view of the current interaction.

    This is context, not authority: tool execution, approvals and progress are
    still server-owned. Literal fields prevent a client from smuggling arbitrary
    instructions into the UI-state system block.
    """

    model_config = ConfigDict(extra="forbid")

    page: Literal["bot", "work"] = "bot"
    last_action: Literal[
        "ask",
        "confirm_model_budget",
        "retry",
        "follow_up",
        "open_artifact",
        "confirm_memory",
        "run_routine",
        "other",
    ] = "other"
    task_id: Optional[str] = Field(default=None, max_length=200)
    task_status: Optional[Literal[
        "queued",
        "running",
        "waiting_user",
        "completed",
        "partial",
        "failed",
        "cancelled",
        "unknown",
    ]] = None
    view: Literal["transcript", "trace", "studio"] = "transcript"


class ThreadCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: str = Field(min_length=1, max_length=200)
    parent_session_id: Optional[str] = Field(default=None, max_length=200)
    title: Optional[str] = Field(default=None, max_length=120)


class BotOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["zh-CN", "en-US"] = "zh-CN"


class WelcomeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=200)
    bot_id: str = Field(min_length=1, max_length=200)
    persona: Literal["beginner", "ticker", "filings", "portfolio", "news", "pro", "watchlist"] = "beginner"
    language: Literal["zh-CN", "en-US"] = "zh-CN"


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=4000)
    session_id: Optional[str] = Field(default=None, max_length=200)
    bot_id: Optional[str] = Field(default=None, max_length=200)
    model_approved: bool = False
    language: Literal["auto", "zh-CN", "en-US"] = "auto"
    research_mode: Literal["quick", "deep"] = "quick"
    model_provider: Literal["deepseek", "chatgpt"] = "deepseek"
    continued_from_task_id: Optional[str] = Field(default=None, max_length=200)
    idempotency_key: Optional[str] = Field(default=None, max_length=200)
    ui_context: Optional[UIContext] = None


class OpenMuseSharedResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=4000)
    session_id: Optional[str] = Field(default=None, max_length=200)
    bot_id: Optional[str] = Field(default=None, max_length=200)
    language: Literal["auto", "zh-CN", "en-US"] = "auto"
    idempotency_key: Optional[str] = Field(default=None, max_length=200)


class CandidateConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    final_text: str = Field(min_length=2, max_length=2000)
    idempotency_key: Optional[str] = Field(default=None, max_length=200)


class CandidateDismissRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    idempotency_key: Optional[str] = Field(default=None, max_length=200)


class MemoryPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    text: str = Field(min_length=2, max_length=2000)
    source_text: Optional[str] = Field(default=None, max_length=2000)


class DeleteAllRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: Literal["delete_all"]
    request_id: Optional[str] = Field(default=None, max_length=200)


class PromptCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    web: bool = True
    filings: bool = True
    market: bool = True
    news: bool = True
    portfolio: bool = True
    watchlist: bool = True


class PromptConfigPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(default="", max_length=4000)
    markets: list[Literal["us", "hk", "cn"]] = Field(default_factory=lambda: ["us", "hk", "cn"])
    output_style: Literal["concise", "standard", "detailed"] = "standard"
    capabilities: PromptCapabilities = Field(default_factory=PromptCapabilities)
    memory_enabled: bool = True
    routines_enabled: bool = True
    proactive_enabled: bool = True


class BotCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    instructions: str = Field(default="", max_length=4000)
    approval_threshold: int = Field(default=4, ge=0, le=100)
    prompt_template: Optional[str] = Field(default=None, max_length=8000)
    prompt_config: Optional[PromptConfigPayload] = None


class BotPromptPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: Optional[str] = Field(default=None, max_length=200)
    name: str = Field(default="新 Bot", max_length=80)
    description: str = Field(default="", max_length=500)
    instructions: str = Field(default="", max_length=4000)
    prompt_template: Optional[str] = Field(default=None, max_length=8000)
    prompt_config: Optional[PromptConfigPayload] = None
    query: str = Field(default="", max_length=4000)


class BotPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=500)
    instructions: Optional[str] = Field(default=None, max_length=4000)
    approval_threshold: Optional[int] = Field(default=None, ge=0, le=100)
    prompt_template: Optional[str] = Field(default=None, max_length=8000)
    prompt_config: Optional[PromptConfigPayload] = None
    market_hours_only: Optional[bool] = None
    max_alerts_per_day: Optional[int] = Field(default=None, ge=1, le=200)
    quiet_hours_start: Optional[str] = Field(default=None, max_length=5)
    quiet_hours_end: Optional[str] = Field(default=None, max_length=5)
    webhook_url: Optional[str] = Field(default=None, max_length=1000)
    notification_email: Optional[str] = Field(default=None, max_length=300)
    report_style: Optional[Literal["brief", "standard"]] = None
    digest_mode: Optional[Literal["immediate", "daily"]] = None
    digest_hour: Optional[str] = Field(default=None, max_length=5)


class MonitorCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    symbol: str = Field(min_length=1, max_length=40)
    condition: Literal["price", "announcement", "earnings", "report"] = "price"
    threshold: float = Field(ge=0, le=100)
    direction: Literal["up", "down", "both"] = "both"
    interval_minutes: int = Field(default=15, ge=1, le=1440)
    cooldown_minutes: int = Field(default=120, ge=0, le=1440)
    bot_id: Optional[str] = Field(default=None, max_length=200)
    session_id: Optional[str] = Field(default=None, max_length=200)


class MonitorPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Optional[Literal["active", "paused"]] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    threshold: Optional[float] = Field(default=None, gt=0, le=100)


class NotificationAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_approved: bool = False


class MarketClosureCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: Literal["us", "hk", "cn"]
    date: str = Field(min_length=10, max_length=10)
    reason: str = Field(default="", max_length=200)


class MonitorMuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mute_minutes: int = Field(default=0, ge=0, le=10080)
