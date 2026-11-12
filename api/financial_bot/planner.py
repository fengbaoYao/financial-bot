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

PLANNER_PROMPT = """你是金融任务规划器，只负责先理解用户意图、明确最终要回答的问题，再拆解成有序取证步骤。禁止调用任何工具，只输出结构化任务清单。

规则：
1. answer_intent 概括用户真正想解决的决策或问题；answer_objective 写明最终回答必须给出的结论。
2. 保留用户原始请求的完整意图，不要删减需求，不要把多个独立动作合并成一个步骤。
3. 每个取证步骤必须说明要解决的子问题、需要的数据和可用工具；步骤必须串行/可依赖，前置步骤未完成时不能执行后续步骤。工具字段只能列出该步骤实际需要执行的工具，不要列泛用备选工具；身份核验单独成步，依赖它的行情/财务工具放在后续步骤。
4. 每一步必须能在当前系统内执行并校验。可用工具只有：
   {tools}
5. 不要规划文件导出、Shell、写数据库、交易下单等本系统没有的能力。没有对应工具的需求不要伪装成可执行步骤。
6. validation 只能用 tool_success、evidence、min_evidence、summary：
   - tool_success：指定工具至少成功返回一次；
   - evidence：返回了实质证据；
   - min_evidence：证据数达到阈值；
   - summary：最终回答非空（只用于 synthesis 步骤）。
7. 金融数据必须校验空值、空数组、缺失字段或异常值；不要用“模型觉得成功”代替校验。
8. 最多 6 步；普通问题 2-3 步即可。不要过度拆分。
9. 如果用户请求缺少关键对象或参数，返回一个只有 clarification 的简短方案是不可执行的；此时优先保留原请求并规划“核验对象”步骤，不要猜测具体股票代码。
10. 研究计划最后必须有 synthesis 步骤；该步骤按 answer_objective 汇总每个子问题的证据，给出明确结论，并标出尚不能确认的部分。
11. 任务终止条件：全部步骤 completed 才算完成；可重试步骤达到 retry_max 仍失败则终止。

只输出 JSON 结构，不要 Markdown，不要解释。"""


class LLMPlanner:
    def __init__(self, *, api_key: str | None, base_url: str | None, model: str | None):
        self.api_key = api_key
        self.base_url = base_url or "https://api.deepseek.com"
        self.model_name = model or "deepseek-flash"

    async def plan(
        self,
        query: str,
        *,
        plan_id: str,
        allowed_tools: set[str] | None = None,
        model_instance: Any = None,
        force: bool = False,
        usage: Any = None,
        usage_limits: Any = None,
    ) -> TaskPlan | None:
        if model_instance is None and (not self.api_key or (not force and not should_use_llm_planner(query))):
            return None
        try:
            from pydantic_ai import Agent
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider
            from openai import AsyncOpenAI
        except Exception:
            return None
        try:
            if model_instance is not None:
                model = model_instance
            else:
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
            result = await agent.run(query, usage=usage, usage_limits=usage_limits)
            plan = normalize_llm_plan(
                result.output, query=query, plan_id=plan_id, allowed_tools=allowed_tools
            )
            return plan
        except Exception as exc:
            logger.warning("LLM planner unavailable, using template plan: %s", type(exc).__name__)
            return None
