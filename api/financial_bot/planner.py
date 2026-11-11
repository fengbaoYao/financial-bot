"""Optional LLM planner for genuinely multi-step financial requests.

The planner never receives tools.  Its only job is to emit a bounded,
structured plan.  If the model is unavailable, returns invalid JSON, or picks
an unknown tool, the caller keeps a deterministic plan instead.
"""

from __future__ import annotations

import logging
from typing import Any

from api.financial_bot.plan import (
    ALLOWED_TOOLS,
    PlanOutput,
    TaskPlan,
    normalize_llm_plan,
    should_use_llm_planner,
)

logger = logging.getLogger(__name__)

PLANNER_PROMPT = """你是金融任务规划器，只负责把用户的金融请求拆解成有序步骤，禁止调用任何工具，只输出结构化任务清单。

规则：
1. 保留用户原始请求的完整意图，不要删减需求，不要把多个独立动作合并成一个步骤。
2. 步骤必须串行/可依赖；前置步骤未完成时不能执行后续步骤。
3. 每一步必须能在当前系统内执行并校验。可用工具只有：
   {tools}
4. 不要规划文件导出、Shell、写数据库、交易下单等本系统没有的能力。没有对应工具的需求不要伪装成可执行步骤。
5. validation 只能用 tool_success、evidence、min_evidence、summary：
   - tool_success：指定工具至少成功返回一次；
   - evidence：返回了实质证据；
   - min_evidence：证据数达到阈值；
   - summary：最终回答非空（只用于 synthesis 步骤）。
6. 金融数据必须校验空值、空数组、缺失字段或异常值；不要用“模型觉得成功”代替校验。
7. 最多 6 步；普通问题 2-3 步即可。不要过度拆分。
8. 如果用户请求缺少关键对象或参数，返回一个只有 clarification 的简短方案是不可执行的；此时优先保留原请求并规划“核验对象”步骤，不要猜测具体股票代码。
9. 任务终止条件：全部步骤 completed 才算完成；可重试步骤达到 retry_max 仍失败则终止。

只输出 JSON 结构，不要 Markdown，不要解释。"""


class LLMPlanner:
    def __init__(self, *, api_key: str | None, base_url: str | None, model: str | None):
        self.api_key = api_key
        self.base_url = base_url or "https://api.deepseek.com"
        self.model_name = model or "deepseek-flash"

    async def plan(self, query: str, *, plan_id: str, allowed_tools: set[str] | None = None) -> TaskPlan | None:
        if not self.api_key or not should_use_llm_planner(query):
            return None
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
                timeout=25,
            )
            provider = OpenAIProvider(openai_client=client)
            model = OpenAIChatModel(self.model_name, provider=provider)
            tools = "\n".join(f"- {tool}" for tool in sorted(allowed_tools or ALLOWED_TOOLS))
            agent = Agent(
                model,
                output_type=PlanOutput,
                system_prompt=PLANNER_PROMPT.format(tools=tools),
                retries={"output": 1},
                model_settings={"max_tokens": 1600, "timeout": 25},
            )
            result = await agent.run(query)
            plan = normalize_llm_plan(result.output, query=query, plan_id=plan_id)
            return plan
        except Exception as exc:
            logger.warning("LLM planner unavailable, using template plan: %s", type(exc).__name__)
            return None
