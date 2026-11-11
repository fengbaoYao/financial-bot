"""Bot-specific first conversation.

The onboarding used to be one fixed list of five starting points, which made a
company-research Bot offer "review my portfolio".  This module asks the model
to turn a Bot's own role, instructions and capabilities into a short greeting
plus a few starting points that Bot can actually deliver.  Anything unexpected
(model unavailable, invalid output, too slow) falls back to the capability
based list on the client, so onboarding never blocks the conversation.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 6 * 60 * 60
_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}


class OnboardTemplate(BaseModel):
    label: str = Field(max_length=40)
    prompt: str = Field(max_length=600)


class OnboardOption(BaseModel):
    label: str = Field(max_length=40)
    ack: str = Field(default="", max_length=160)
    templates: list[OnboardTemplate] = Field(default_factory=list, max_length=3)


class OnboardOutput(BaseModel):
    greeting: str = Field(max_length=220)
    options: list[OnboardOption] = Field(min_length=2, max_length=4)


ONBOARD_PROMPT = """你在为一个金融研究 Bot 设计「第一次对话」的开场。你不是回答问题，只设计开场。

Bot 信息：
- 名字：{name}
- 角色：{role}
- 自定义指令：{instructions}
- 可用能力：{capabilities}
- 覆盖市场：{markets}
- 回答语言：{language}

输出要求：
1. greeting：中文必须以「嗨，我是{name}。」开头，英文必须以「Hi — I'm {name}.」开头，然后用一句话说清这个 Bot 具体帮你做什么；中文整句不超过 70 字，英文不超过 40 个词。不要写「我是 AI 助手」「有问题随时问我」这类空话，不要列举全部能力。
2. options：3 个研究起点，必须是这个 Bot 的能力范围内**真实可执行**的事，彼此不重复；没有的能力（例如未开启持仓能力）绝对不要出现。label 中文不超过 14 字，英文不超过 8 个词。
3. 每个 option 给 1-2 个 templates：label 是一个具体标的或主题，prompt 是一条可以直接发送的完整研究请求，必须包含「标注来源、时间和缺口」以及「不要给买卖建议」。
4. 语气与 Bot 的角色一致（例如公司研究 Bot 用分析师口吻，公告 Bot 用核对口吻）。
5. 只输出结构化结果，不要 Markdown，不要解释。"""

ONBOARD_PROMPT_EN = """You are designing the opening of a financial research Bot's first conversation. Do not answer questions; only design the opening.

Bot:
- Name: {name}
- Role: {role}
- Custom instructions: {instructions}
- Available capabilities: {capabilities}
- Markets: {markets}

Output requirements:
1. greeting: start with "Hi — I'm {name}." then one sentence about what this Bot specifically does for the person. At most 35 words. No filler like "I'm an AI assistant" or "ask me anything", and do not list every capability.
2. options: 3 starting points that are genuinely executable with this Bot's capabilities and do not repeat each other. Never offer something the Bot cannot do (for example portfolio work when that capability is off). Label at most 8 words.
3. Each option gets 1-2 templates: label is a concrete ticker or topic, prompt is a complete research request that must include marking sources, dates and gaps, and must not give trading advice.
4. Match the Bot's role in tone (a company-research Bot sounds like an analyst; a filings Bot sounds like a checker).
5. Write everything in English. Output structured fields only, no Markdown, no explanation."""

_LANGUAGE_NAMES = {"zh-CN": "中文（简体）", "en-US": "English"}


def _capability_names(bot: dict[str, Any]) -> str:
    config = bot.get("prompt_config") if isinstance(bot.get("prompt_config"), dict) else {}
    caps = config.get("capabilities") if isinstance(config.get("capabilities"), dict) else {}
    names = []
    for key, label in (
        ("market", "行情与技术面"),
        ("filings", "公告与财报"),
        ("news", "新闻"),
        ("web", "公开来源检索"),
        ("portfolio", "持仓分析"),
        ("watchlist", "自选股"),
    ):
        if caps.get(key):
            names.append(label)
    return "、".join(names) or "只有通用对话"


def _market_names(bot: dict[str, Any]) -> str:
    config = bot.get("prompt_config") if isinstance(bot.get("prompt_config"), dict) else {}
    markets = config.get("markets") if isinstance(config.get("markets"), list) else []
    labels = {"cn": "A股", "hk": "港股", "us": "美股"}
    return "、".join(labels.get(str(market), str(market)) for market in markets) or "未限定"


class BotOnboarding:
    """Generate (and cache) the opening of a Bot's first conversation."""

    def __init__(self, *, api_key: Optional[str], base_url: Optional[str], model: Optional[str]):
        self.api_key = api_key
        self.base_url = base_url or "https://api.deepseek.com"
        self.model_name = model or "deepseek-flash"

    @staticmethod
    def cached(bot_id: str, version: str, language: str) -> Optional[dict[str, Any]]:
        entry = _cache.get((bot_id, version, language))
        if not entry:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            _cache.pop((bot_id, version, language), None)
            return None
        return value

    async def generate(self, bot: dict[str, Any], *, language: str = "zh-CN") -> Optional[dict[str, Any]]:
        if not self.api_key:
            return None
        bot_id = str(bot.get("id") or "")
        version = f"{bot.get('updated_at') or ''}:{bot.get('prompt_version') or 1}"
        hit = self.cached(bot_id, version, language)
        if hit is not None:
            return hit
        try:
            from pydantic_ai import Agent
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider
            from openai import AsyncOpenAI
        except Exception:
            return None
        try:
            client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                max_retries=0,
                timeout=12,
            )
            provider = OpenAIProvider(openai_client=client)
            model = OpenAIChatModel(self.model_name, provider=provider)
            agent = Agent(
                model,
                output_type=OnboardOutput,
                system_prompt=(ONBOARD_PROMPT_EN if language == "en-US" else ONBOARD_PROMPT).format(
                    name=bot.get("name") or "Research Bot",
                    role=(bot.get("prompt_config") or {}).get("role") or "股票研究助手",
                    instructions=(bot.get("instructions") or "（无）")[:1200],
                    capabilities=_capability_names(bot),
                    markets=_market_names(bot),
                    language=_LANGUAGE_NAMES.get(language, language),
                ),
                retries={"output": 1},
                # deepseek-flash defaults to thinking mode, which rejects the
                # tool_choice that structured output uses; keep it off here.
                model_settings={"max_tokens": 1200, "timeout": 12, "extra_body": {"thinking": {"type": "disabled"}}},
            )
            result = await agent.run(
                "请为这个 Bot 设计第一次对话的开场。" if language != "en-US" else "Design the opening for this Bot."
            )
            payload = result.output.model_dump()
            options = [
                {
                    "label": str(option.get("label") or "").strip()[:40],
                    "ack": str(option.get("ack") or "").strip()[:160],
                    "templates": [
                        {
                            "label": str(template.get("label") or "").strip()[:40],
                            "prompt": str(template.get("prompt") or "").strip()[:600],
                        }
                        for template in (option.get("templates") or [])
                        if str(template.get("label") or "").strip()
                    ][:3],
                }
                for option in payload.get("options", [])
                if str(option.get("label") or "").strip()
            ][:4]
            greeting = str(payload.get("greeting") or "").strip()[:220]
            if not greeting or len(options) < 2:
                return None
            value = {"greeting": greeting, "options": options}
            _cache[(bot_id, version, language)] = (time.monotonic() + _CACHE_TTL_SECONDS, value)
            return value
        except Exception as exc:  # noqa: BLE001 - onboarding must never block the page
            logger.warning("Bot onboarding generation failed: %s", type(exc).__name__)
            return None
