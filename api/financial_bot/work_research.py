"""Deterministic evidence requirements for Work's causal stock-move answers."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import re
from typing import Any

from api.agent_pydantic import _is_stock_price_move_question


def build_work_research_guidance(query: str, *, bot_prompt: str = "") -> str:
    """Add a small fixed checklist only to causal Work questions.

    Bot has its own planner/persona and must not receive Work-specific guidance.
    """
    if bot_prompt or not _is_stock_price_move_question(query):
        return ""
    if _is_stability_query(query):
        return (
            "\n\nWork 稳定/横盘问题回答任务（先明确结论目标，再取证；按工具真实结果执行）：\n"
            "回答任务：先核对‘稳定/没有涨跌’是否符合已取得的区间数据；量化短期与较长区间的走势和波动；比较同期市场/行业表现；根据同一时段内可核实的公司事件，判断哪些解释有证据支持。\n"
            "1. 先用 search_stocks 核实证券身份；身份不明确时先澄清，不猜代码。\n"
            "2. 用 get_stock_analysis 取得带日期的价格序列和区间表现；同时检查量价或波动指标是否可用。不要把‘没涨跌’当作事实，先核对数据。\n"
            "3. 用 get_benchmark_comparison 对比同期市场/行业基准，判断是个股窄幅波动、跟随市场，还是相对走强/走弱。\n"
            "4. 用 get_stock_news 检索讨论时段内与公司直接相关的事件；只把日期、内容和时段吻合的记录作为候选解释。\n"
            "5. 先给一句结论：实际走势是否稳定、相对基准如何。再说明最多两条有证据支持的可能解释；没有直接催化证据时，明确说具体原因无法确认，并指出还缺什么证据。不得用模型记忆补当前行情、事件或原因，也不要只输出数据清单或通用免责声明。"
        )
    return (
        "\n\nWork 因果问题取证清单（按工具真实结果执行，不得声称未完成的步骤已完成）：\n"
        "1. 先用 search_stocks 核实用户指的证券及代码；身份不明确时先澄清，不猜代码。\n"
        "2. 用 get_stock_analysis 获取这次涨跌的价格、幅度和数据时间。\n"
        "3. 用 get_benchmark_comparison 获取同一期间的市场/行业基准；没有可比结果就明确写出缺口。\n"
        "4. 用 get_stock_news 查找与该公司直接相关、带日期和来源的消息；宽泛主题新闻或其他公司的新闻不能作为该公司的催化因素。必要时再用 web_search/read_public_source 核实。\n"
        "5. 回答先给结论，再列对应期间的行情/基准事实和最多两条有来源的可能原因。若缺少直接催化证据，明确说原因未能确认；不要用‘多因素共振’等空泛话替代证据。区分事实与推断。"
    )


def _is_stability_query(query: str) -> bool:
    return bool(re.search(
        r"稳定|横盘|窄幅|波动不大|没(?:有)?涨跌|涨跌不大|基本持平|走势平稳|\b(?:stable|steady|flat|sideways|little movement)\b",
        query or "", re.I,
    ))


def should_replace_with_factual_fallback(response: Any, query: str = "") -> bool:
    """Only replace prose when synthesis actually stalled, not when evidence is partial."""
    summary = str(getattr(response, "summary", "") or "").strip()
    payload = getattr(response, "payload", None)
    meta = payload.get("_meta", {}) if isinstance(payload, dict) else {}
    if not summary or getattr(response, "intent", None) == "error":
        return True
    if isinstance(meta, dict) and meta.get("synthesis_incomplete"):
        return True
    if re.search(
        r"研究综合未完成|研究汇总未能完成|Research synthesis incomplete|Research synthesis could not",
        summary,
        re.I,
    ):
        return True
    # A neutral stability question has no single rise/fall direction for a
    # catalyst matcher. Preserve its completed synthesis and surface evidence
    # gaps; directional move questions retain the stricter factual fallback.
    return not _is_stability_query(query)


def evaluate_work_research(query: str, tool_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate checklist coverage against recorded tool results, never prose."""
    if not _is_stock_price_move_question(query):
        return {"status": "not_applicable", "data_gaps": []}

    successful = {
        item.get("tool")
        for item in tool_results
        if item.get("tool") and item.get("status") != "unavailable" and item.get("data")
    }
    gaps: list[str] = []
    if "search_stocks" not in successful:
        gaps.append("Work 因果核验缺口：未取得证券身份核验结果。")
    if "get_stock_analysis" not in successful:
        gaps.append("Work 因果核验缺口：未取得本次涨跌的行情数据。")
    if "get_benchmark_comparison" not in successful:
        gaps.append("Work 因果核验缺口：未取得同期市场或行业基准，无法判断个股是否跑赢/跑输。")
    if not _has_relevant_catalyst(query, tool_results):
        if _is_stability_query(query):
            gaps.append("Work 因果核验缺口：没有找到足以解释该时段走势的直接公司事件，具体原因不能确认。")
        else:
            gaps.append("Work 因果核验缺口：没有找到与讨论时段对齐、带日期且直接关联该公司的消息，涨跌原因不能确认。")
    return {"status": "partial" if gaps else "completed", "data_gaps": gaps}


def _has_relevant_catalyst(query: str, tool_results: list[dict[str, Any]]) -> bool:
    # A recent company mention alone is not a causal explanation. Require a
    # dated direct item whose classified sentiment matches the requested move.
    direction = _move_sentiment(query)
    if direction is None:
        return False
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    cutoff = today - timedelta(days=_requested_window_days(query))
    for item in tool_results:
        if item.get("status") == "unavailable":
            continue
        tool, data = item.get("tool"), item.get("data")
        if tool == "get_stock_news" and isinstance(data, list):
            for row in data:
                if not isinstance(row, dict):
                    continue
                published = str(row.get("published_at") or row.get("date") or "")[:10]
                try:
                    published_date = datetime.strptime(published, "%Y-%m-%d").date()
                except ValueError:
                    continue
                try:
                    impact_level = int(row.get("impact_level") or 0)
                except (TypeError, ValueError):
                    impact_level = 0
                has_event_content = bool(str(row.get("title") or row.get("summary") or "").strip())
                if (row.get("relation_type") == "direct"
                        and row.get("sentiment") == direction
                        and impact_level >= 2
                        and has_event_content
                        and cutoff <= published_date <= today):
                    return True
    return False


def _move_sentiment(query: str) -> str | None:
    text = (query or "").casefold()
    down = bool(re.search(r"下跌|下挫|走弱|走低|回落|下滑|跳水|暴跌|跌|利空|\b(?:fall|fell|drop|dropped|decline|declined|plunge|plunged|down|bearish)\b", text))
    up = bool(re.search(r"上涨|走强|走高|回升|反弹|飙升|暴涨|涨|利好|\b(?:rise|rose|surge|surged|rally|rallied|gain|gained|up|bullish)\b", text))
    if down == up:
        return None
    return "bearish" if down else "bullish"


def _requested_window_days(query: str) -> int:
    text = (query or "").casefold()
    numeric = re.search(r"(?:近|最近|过去)?\s*(\d{1,3})\s*(?:个)?\s*(天|日|周|星期|个月|月)", text)
    if numeric:
        count = int(numeric.group(1))
        unit = numeric.group(2)
        multiplier = 30 if unit in {"月", "个月"} else 7 if unit in {"周", "星期"} else 1
        return max(1, min(180, count * multiplier))
    chinese = re.search(r"(?:近|最近|过去)?\s*(一个|一|两|二|三|四|五|六|七|八|九|十)\s*(?:个)?\s*(天|日|周|星期|个月|月)", text)
    if chinese:
        count = {"一个": 1, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
                 "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}[chinese.group(1)]
        unit = chinese.group(2)
        multiplier = 30 if unit in {"月", "个月"} else 7 if unit in {"周", "星期"} else 1
        return min(180, count * multiplier)
    if any(word in text for word in ("今天", "今日", "当日")):
        return 1
    if any(word in text for word in ("这两天", "这两日")):
        return 3
    return 7


def apply_work_research_result(response: Any, query: str, tool_results: list[dict[str, Any]]) -> dict[str, Any]:
    result = evaluate_work_research(query, tool_results)
    if result["status"] == "not_applicable":
        return result
    existing = list(response.data_gaps or [])
    response.data_gaps = list(dict.fromkeys([*existing, *result["data_gaps"]]))
    meta = response.payload.setdefault("_meta", {})
    meta["work_research_plan_status"] = result["status"]
    meta["work_research_plan"] = ["identity", "price_move", "benchmark", "catalyst", "evidence_based_synthesis"]
    return result
