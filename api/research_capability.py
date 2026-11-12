"""PydanticAI V2 research hooks: scoped tools and per-run retrieval reuse."""
from dataclasses import replace
import os
import re
from pydantic_ai.capabilities import Capability
from ashare.stock_pool import normalize_stock_code
from api.company_research import ensure_identity
from api.comparison_research import (
    is_broad_stock_comparison,
    is_company_news_query,
    is_financial_stock_comparison,
    is_first_pass_investor_brief,
    investor_brief_tools,
)
from api.company_research import resolved_codes


def _verified_simple_quote(query: str, result: dict) -> bool:
    """Whether iFinD completed a single resolved quote that needs no more retrieval."""
    if not re.search(r"股价|现价|最新价|报价|现在.*(?:多少|价格)|当前.*(?:多少|价格)|\b(?:stock|share)\s+price\b|\bquote\b", query, re.I):
        return False
    if re.search(r"历史|走势|分析|技术|为什么|原因|涨跌原因|比较|对比|财报|公告|新闻|估值|近[一二三四五六七八九十\d]+(?:个)?(?:天|日|周|月|年)|\b(?:history|historical|analysis|compare|comparison|news|earnings|valuation)\b", query, re.I):
        return False
    try:
        from api.financial_bot.fallback_research import find_company
        from api.datasources.ifind_provider import to_ths_code

        match = find_company(query)
        if not match:
            return False
        expected = to_ths_code(match[0]).upper()
        payload = result.get("data") or {}
        if result.get("endpoint") != "real_time_quotation":
            return False
        tables = (payload.get("data") or {}).get("tables") or []
        returned = {
            str(row.get("thscode") or "").strip().upper()
            for row in tables if isinstance(row, dict) and str(row.get("thscode") or "").strip()
        }
        has_price = any(
            isinstance(row, dict)
            and isinstance(row.get("table"), dict)
            and bool(row["table"].get("latest"))
            and row["table"]["latest"][0] is not None
            for row in tables
        )
        return returned == {expected} and has_price
    except Exception:
        return False


class ResearchCapability(Capability):
    async def before_model_request(self, ctx, request_context):
        if getattr(ctx.deps, "finish_reason", None) == "unresolved_provider_usage":
            from pydantic_ai.exceptions import UsageLimitExceeded
            raise UsageLimitExceeded("Unknown provider usage blocks additional paid model requests.")
        if ctx.deps.provider_requests_started > ctx.deps.provider_requests_finished:
            from pydantic_ai.exceptions import UsageLimitExceeded
            raise UsageLimitExceeded("Unknown provider usage blocks additional paid model requests.")
        ctx.deps.provider_requests_started += 1
        if ctx.model.model_name.startswith("deepseek") and ctx.deps.synthesis_reason():
            # The final pass should spend its output allowance on the cited report,
            # after the planning and specialist passes have reviewed the evidence.
            settings = dict(request_context.model_settings or {})
            settings["extra_body"] = {**settings.get("extra_body", {}), "thinking": {"type": "disabled"}}
            settings.pop("openai_reasoning_effort", None)
            return replace(request_context, model_settings=settings)
        return request_context

    async def after_model_request(self, ctx, *, request_context, response):
        ctx.deps.provider_requests_finished += 1
        return response

    async def prepare_tools(self, ctx, tool_defs):
        reason = ctx.deps.synthesis_reason()
        # Leave room for the final answer and validation retries. Specialist
        # reviews share this same usage counter with the parent agent.
        limit = getattr(ctx.deps, "model_request_limit", None)
        usage = getattr(ctx, "usage", None)
        if (not reason and getattr(ctx.deps, "research_mode", "quick") == "deep"
                and limit is not None and getattr(usage, "requests", 0) >= max(1, limit - 3)):
            reason = "request_reserved_for_synthesis"
        if reason:
            ctx.deps.finish_reason = reason
            return []  # Output tools remain available, including validation repair.
        if getattr(ctx.deps, "plan_before_research", False) and getattr(ctx.deps, "research_plan", None) is not None:
            # Work's structured plan is the routing authority for this run.
            # Do not replace it with the older iFinD-first broad-query gate;
            # the outer PrepareTools hook exposes only the current plan step.
            return [tool for tool in tool_defs
                    if tool.name != "get_sec_company_data"
                    or (getattr(ctx.deps, "verified_us_codes", set()) and "@" in os.environ.get("SEC_USER_AGENT", ""))]
        gated = self._apply_ifind_primary_gate(ctx, tool_defs)
        if gated is not None:
            return gated
        if is_first_pass_investor_brief(ctx.deps.user_query):
            results = ctx.deps.tool_results
            searches = [item for item in results if item.get("tool") == "search_stocks"]
            if not searches:
                return [tool for tool in tool_defs if tool.name == "search_stocks"]
            resolved = resolved_codes(ctx.deps)
            if (len(resolved) != 1 or any(item.get("status") == "unavailable"
                    or (isinstance(item.get("data"), list) and len(item.get("data", [])) != 1)
                    for item in searches)):
                return []
            code = next(iter(resolved))
            if code.startswith("US.") and code not in ctx.deps.verified_us_codes:
                return []

            completed = {item.get("tool") for item in results}
            required = investor_brief_tools(ctx.deps.user_query, us_company=code.startswith("US."))
            missing = set(required) - completed
            if missing:
                return [tool for tool in tool_defs if tool.name in missing]

            web_results = next((item for item in reversed(results)
                                if item.get("tool") == "web_search"), None)
            has_web_sources = bool(web_results and web_results.get("status") != "unavailable"
                                   and any(str(row.get("url") or "").startswith(("https://", "http://"))
                                           for row in web_results.get("data", []) if isinstance(row, dict)))
            if has_web_sources and "read_public_source" not in completed:
                return [tool for tool in tool_defs if tool.name == "read_public_source"]
            if (ctx.deps.research_mode == "deep" and "research_company" not in completed
                    and any(item.get("evidence") and item.get("tool") != "search_stocks"
                            and item.get("status") != "unavailable" for item in results)):
                return [tool for tool in tool_defs if tool.name == "research_company"]
            return []
        if is_broad_stock_comparison(ctx.deps.user_query):
            comparison = next((item for item in reversed(ctx.deps.tool_results)
                               if item.get("tool") == "research_comparison"), None)
            if comparison is None:
                # Start with one balanced, cross-market evidence pass.
                return [tool for tool in tool_defs if tool.name == "research_comparison"]
            companies = (comparison.get("data") or {}).get("companies", [])
            identities_verified = (
                comparison.get("status") != "unavailable"
                and len(companies) == 2
                and all((company.get("identity") or {}).get("status") == "ok" for company in companies)
            )
            if not identities_verified:
                # An ambiguous pair should be clarified before any further research.
                return []
            search_failures = [item for item in ctx.deps.tool_results if item.get("tool") == "web_search"
                               and item.get("status") == "unavailable"]
            provider_failures = [item for item in search_failures if item.get("reason_code") == "provider_error"]
            has_search_results = any(item.get("tool") == "web_search" and item.get("status") != "unavailable"
                                     and item.get("data") for item in ctx.deps.tool_results)
            if not has_search_results and (len(provider_failures) >= 2 or len(search_failures) >= 4):
                # Repeating a provider that has already failed for both sides
                # or exhausting four unsuccessful searches cannot improve coverage.
                ctx.deps.finish_reason = "source_provider_unavailable" if len(provider_failures) >= 2 else "comparison_search_exhausted"
                return []
            # After the first balanced pass, allow targeted follow-up research
            # without reopening unrelated SEC-only or single-company routes.
            return [tool for tool in tool_defs if tool.name in {"web_search", "read_public_source"}]
        if is_financial_stock_comparison(ctx.deps.user_query):
            if "@" not in os.environ.get("SEC_USER_AGENT", ""):
                ctx.deps.finish_reason = "sec_not_configured"
                return []
            searches = [item for item in ctx.deps.tool_results if item.get("tool") == "search_stocks"]
            resolved = resolved_codes(ctx.deps)
            verified = resolved & ctx.deps.verified_us_codes
            has_non_us = any(code and not code.startswith("US.") for code in resolved)
            if has_non_us:
                # SEC cannot compare a mixed-market pair; leave the normal
                # market-specific tools available rather than forcing US-only data.
                return tool_defs
            if len(verified) < 2:
                return [tool for tool in tool_defs if tool.name == "search_stocks"]
            comparison = next((item for item in reversed(ctx.deps.tool_results)
                               if item.get("tool") == "compare_companies"), None)
            if comparison is None:
                return [tool for tool in tool_defs if tool.name == "compare_companies"]
            return []
        if is_company_news_query(ctx.deps.user_query):
            searches = [item for item in ctx.deps.tool_results if item.get("tool") == "search_stocks"]
            resolved = resolved_codes(ctx.deps)
            if not searches:
                return [tool for tool in tool_defs if tool.name == "search_stocks"]
            if len(resolved) != 1 or any(item.get("status") == "unavailable"
                    or (isinstance(item.get("data"), list) and len(item["data"]) != 1) for item in searches):
                return []
            code = next(iter(resolved))
            if code.startswith("US.") and code not in ctx.deps.verified_us_codes:
                return []
            return [tool for tool in tool_defs if tool.name == "get_stock_news"]
        verified = ctx.deps.verified_us_codes
        sec_ready = "@" in os.environ.get("SEC_USER_AGENT", "")
        return [tool for tool in tool_defs
                if (tool.name != "get_sec_company_data" or (verified and sec_ready))
                and (tool.name != "compare_companies" or (len(verified) >= 2 and sec_ready))
                and (tool.name != "research_company" or ctx.deps.research_mode == "deep")]


    # Competing market/fundamentals providers. Kept closed for single-security
    # quote/fundamental questions until iFinD skill has failed.
    _IFIND_FALLBACK_MARKET_TOOLS = frozenset({
        "get_stock_analysis",
        "get_company_fundamentals",
        "get_financial_statements",
        "get_valuations",
        "get_consensus_estimates",
        "get_analyst_estimates",
        "get_cninfo_announcements",
    })

    def _apply_ifind_primary_gate(self, ctx, tool_defs):
        """Hard-prefer iFinD for single-name quote/fundamental questions.

        Comparisons, investor briefs, technical-signal-only asks, and non-market
        chat stay on the existing soft path. Failure opens the competing tools.
        """
        try:
            from api.datasources.flags import ifind_enabled, ifind_primary
            from api.agent_pydantic import is_simple_stock_quote_query, should_prefetch_ifind
        except Exception:
            return None
        query = str(getattr(ctx.deps, "user_query", "") or "")
        if not (ifind_enabled() and ifind_primary() and should_prefetch_ifind(query)):
            return None
        try:
            from api.agent_pydantic import _is_stock_price_move_question
            if _is_stock_price_move_question(query, verified_identity=bool(resolved_codes(ctx.deps))):
                # Causal questions use structured quote, news and benchmark
                # retrieval. Do not also expose the broad NL router, whose
                # independent failure was being mistaken for total iFinD failure.
                return [tool for tool in tool_defs if tool.name != "ifind_smart_query"]
        except Exception:
            pass
        if is_simple_stock_quote_query(query):
            results = list(getattr(ctx.deps, "tool_results", None) or [])
            searches = [item for item in results if item.get("tool") == "search_stocks"]
            if not searches:
                return [tool for tool in tool_defs if tool.name == "search_stocks"]
            search = searches[-1]
            rows = search.get("data") if isinstance(search.get("data"), list) else []
            if search.get("status") == "unavailable" or len(rows) != 1:
                return []
            try:
                from api.financial_bot.fallback_research import find_company

                expected = normalize_stock_code(find_company(query)[0]).upper()
            except Exception:
                return []
            if resolved_codes(ctx.deps) != {expected}:
                return []
            analysis = next((item for item in reversed(results) if item.get("tool") == "get_stock_analysis"), None)
            if analysis is None:
                return [tool for tool in tool_defs if tool.name == "get_stock_analysis"]
            # A successful quote is sufficient for this narrow request; an
            # unavailable one should produce a clear gap without web detours.
            return []
        # Technical-signal asks still prefetch iFinD for context, but need local
        # analysis tools; do not hard-block them.
        if re.search(r"技术面|技术分析|\bMACD\b|\bRSI\b|均线|信号评分|买卖点", query, re.I):
            return None
        if (is_first_pass_investor_brief(query)
                or is_broad_stock_comparison(query)
                or is_financial_stock_comparison(query)):
            return None
        results = list(getattr(ctx.deps, "tool_results", None) or [])
        ifind_rows = [item for item in results if item.get("tool") == "ifind_smart_query"]
        if not ifind_rows:
            only = [tool for tool in tool_defs if tool.name == "ifind_smart_query"]
            return only or None
        if any(item.get("status") == "unavailable" for item in ifind_rows):
            return None  # failure → normal tool set
        if _verified_simple_quote(query, ifind_rows[-1]):
            # A verified live quote is already the complete evidence for a short
            # price request; additional identity and web reads only add latency.
            return []
        # Success: keep competing market providers closed; leave identity/web/etc.
        verified = getattr(ctx.deps, "verified_us_codes", set()) or set()
        sec_ready = "@" in os.environ.get("SEC_USER_AGENT", "")
        return [
            tool for tool in tool_defs
            if tool.name not in self._IFIND_FALLBACK_MARKET_TOOLS
            and (tool.name != "get_sec_company_data" or (verified and sec_ready))
            and (tool.name != "compare_companies" or (len(verified) >= 2 and sec_ready))
            and (tool.name != "research_company" or getattr(ctx.deps, "research_mode", "quick") == "deep")
        ]

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        # Mutations and list reads must see the current persisted workspace state.
        if tool_def.name == "manage_watchlist":
            if args.get("action", "").strip().lower() == "add":
                code = normalize_stock_code(args.get("stock_code", "")).upper()
                if not code or not await ensure_identity(ctx.deps, code):
                    return ctx.deps.record_tool({"tool": "manage_watchlist", "status": "unavailable", "reason_code": "identity_required", "data": {"data_gaps": ["Verify an unambiguous company before changing the watchlist."]}})
            return await handler(args)
        # These tools admit their own reads so compound company research shares
        # the same per-run deduplication and retrieval telemetry.
        if tool_def.name in {"web_search", "read_public_source"}:
            return await handler(args)
        if tool_def.name in {"get_stock_analysis", "get_stock_news", "get_sec_company_data"}:
            code = normalize_stock_code(args.get("stock_code", "")).upper()
            if not await ensure_identity(ctx.deps, code):
                return ctx.deps.record_tool({"tool": tool_def.name, "status": "unavailable", "reason_code": "identity_required", "data": {"data_gaps": ["Company identity is ambiguous or could not be verified. Ask for an exact ticker; do not guess."]}})
            args = {**args, "stock_code": code if code.startswith(("US.", "HK.")) else code.lower()}
        # State belongs to the request, not to the shared Agent instance.
        if tool_def.name == "research_comparison":
            # The orchestrator itself is not a source read; its nested identity,
            # quote, search and document calls each consume the shared budget.
            return await ctx.deps.retrieve(tool_def.name, args, lambda: handler(args), use_slot=False, count=False)
        return await ctx.deps.retrieve(tool_def.name, args, lambda: handler(args), use_slot=tool_def.name not in {"research_company", "compare_companies"})
