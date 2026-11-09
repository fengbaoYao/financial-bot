"""Small authenticated data bridge for local research companions.

This endpoint returns source-backed data only. It does not synthesize financial
advice and keeps the iFinD-first policy used by Financial Bot.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/financial-research", tags=["financial-research"])


class FinancialResearchQuery(BaseModel):
    # OpenMuse can include a bounded amount of task context with the user query.
    # Match the 12k task prompt ceiling rather than rejecting normal research.
    query: str = Field(min_length=1, max_length=12000)


def _authorized(request: Request) -> bool:
    try:
        address = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        return False
    if not address.is_loopback:
        return False
    expected = os.environ.get("FINANCIAL_RESEARCH_SHARED_TOKEN", "").strip()
    if len(expected.encode("utf-8")) < 32:
        return False
    auth = request.headers.get("authorization", "")
    scheme, _, presented = auth.partition(" ")
    return scheme.lower() == "bearer" and bool(presented) and hmac.compare_digest(expected, presented.strip())


def _response(query: str, *, status: Literal["ok", "partial", "unavailable"], provider: str | None,
              data_as_of: str | None, records: list[dict[str, Any]], attempts: list[dict[str, Any]],
              data_gaps: list[str]) -> dict[str, Any]:
    return {
        "status": status,
        "query": query,
        "primary_provider": "iFinD",
        "provider": provider,
        "data_as_of": data_as_of,
        "records": records,
        "attempts": attempts,
        "data_gaps": data_gaps,
    }


def _normalize_security_code(value: Any) -> str | None:
    from ashare.stock_pool import normalize_stock_code

    raw = str(value or "").strip()
    if not raw or raw.lower() in {"none", "null", "--", "-"}:
        return None
    normalized = normalize_stock_code(raw)
    if re.fullmatch(r"\d{6}", normalized):
        if normalized.startswith(("60", "68", "90")):
            normalized = f"sh{normalized}"
        elif normalized.startswith(("00", "30")):
            normalized = f"sz{normalized}"
        elif normalized.startswith(("4", "8", "92")):
            normalized = f"bj{normalized}"
    return normalized.upper() if normalized.upper().startswith(("US.",)) or normalized.upper().endswith(".HK") else normalized.lower()


def _expected_security_code(query: str) -> str | None:
    explicit = re.findall(
        r"(?<![A-Za-z0-9])(?:SH|SZ|BJ)\d{6}(?!\d)|(?<!\d)\d{6}\.(?:SH|SZ|BJ)\b|"
        r"(?<!\d)\d{1,5}\.HK\b|\bHK\.?\d{1,5}\b|\bUS\.[A-Z][A-Z0-9.]{0,10}\b",
        query,
        re.I,
    )
    normalized = {_normalize_security_code(item) for item in explicit}
    normalized.discard(None)
    if len(normalized) == 1:
        return next(iter(normalized))
    if len(normalized) > 1:
        return None
    try:
        from api.financial_bot.fallback_research import find_company

        match = find_company(query)
        return _normalize_security_code(match[0]) if match else None
    except Exception:
        return None


def _returned_security_codes(value: Any) -> set[str]:
    codes: set[str] = set()
    code_keys = {"code", "thscode", "symbol", "stock_code", "security_code", "ticker"}

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                normalized_key = str(key).strip().lower().replace("_", "")
                is_code_key = normalized_key in {key.replace("_", "") for key in code_keys} or "代码" in str(key)
                if is_code_key:
                    values = item if isinstance(item, list) else [item]
                    for candidate in values:
                        normalized = _normalize_security_code(candidate)
                        if normalized:
                            codes.add(normalized)
                else:
                    visit(item)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(value)
    return codes


@router.post("/query")
async def query_financial_data(request: Request, payload: FinancialResearchQuery) -> dict[str, Any]:
    if not _authorized(request):
        # Keep the response identical for wrong token, missing configuration,
        # and non-loopback clients so no credential state is disclosed.
        raise HTTPException(status_code=403, detail="financial_research_access_denied")

    query = payload.query.strip()
    if not query:
        raise HTTPException(status_code=422, detail="query_required")

    attempts: list[dict[str, Any]] = []
    gaps: list[str] = []
    expected_code = _expected_security_code(query)
    from api.datasources.flags import ifind_enabled

    if ifind_enabled():
        try:
            from api.datasources.ifind_provider import IfindProvider

            result = await __import__("asyncio").to_thread(IfindProvider().smart_query, query)
            data = result.get("data") if isinstance(result, dict) else None
            if not data:
                raise ValueError("empty_result")
            if expected_code:
                returned_codes = _returned_security_codes(data)
                if not returned_codes:
                    raise ValueError("identity_unverified")
                if returned_codes != {expected_code}:
                    raise ValueError("identity_mismatch")
            attempts.append({"provider": "iFinD", "status": "ok", "reason": None})
            # smart_query does not expose a trustworthy uniform quote timestamp;
            # do not substitute request time for data_as_of.
            return _response(
                query,
                status="partial",
                provider="iFinD",
                data_as_of=None,
                records=[{"kind": "smart_query", "data": data}],
                attempts=attempts,
                data_gaps=["iFinD returned data, but its observation time could not be verified."],
            )
        except Exception as exc:  # provider boundary; details may contain secrets/vendor payload
            reason = str(exc) if str(exc) in {"identity_unverified", "identity_mismatch"} else type(exc).__name__
            attempts.append({"provider": "iFinD", "status": "failed", "reason": reason})
            if reason == "identity_unverified":
                gaps.append("iFinD returned data without a verifiable security identifier for the requested security.")
            elif reason == "identity_mismatch":
                gaps.append("iFinD returned a different security identifier; its data was discarded.")
            gaps.append("iFinD did not return usable data; a configured Financial Bot fallback was attempted.")
    else:
        attempts.append({"provider": "iFinD", "status": "not_configured", "reason": "provider_disabled"})
        gaps.append("iFinD is not configured; a Financial Bot fallback was attempted.")

    # The existing stock analysis service is the shared fallback used by the
    # Financial Bot tool path. It preserves its actual provider and source date.
    try:
        from ashare.search import stock_searcher

        match = re.search(r"(?<!\d)(?:SH|SZ|BJ)?\d{6}(?!\d)|(?<!\d)\d{1,5}\.HK\b|\bHK\.?\d{1,5}\b|\bUS\.[A-Z.]{1,8}\b", query, re.I)
        lookup_code = expected_code or (match.group(0) if match else None)
        info = stock_searcher.get_stock_info(lookup_code) if lookup_code else None
        if not info:
            matches = stock_searcher.search_stocks(query, max_results=1)
            info = matches[0] if matches else None
        code = (info or {}).get("code") or (info or {}).get("stock_code")
        if not code:
            raise LookupError("security_not_resolved")
        if expected_code and _normalize_security_code(code) != expected_code:
            raise ValueError("fallback_identity_mismatch")

        import api.main as main

        result = await __import__("asyncio").to_thread(main.stock_service.get_stock_analysis, code, False)
        data = result.model_dump(mode="json") if hasattr(result, "model_dump") else result.dict()
        metadata = data.get("metadata") or {}
        fallback_code = data.get("stock_code") or data.get("code")
        if expected_code and not fallback_code:
            raise ValueError("fallback_identity_unverified")
        if expected_code and _normalize_security_code(fallback_code) != expected_code:
            raise ValueError("fallback_identity_mismatch")
        quote_info = metadata.get("ifind_quote") if isinstance(metadata, dict) else None
        quote_info = quote_info if isinstance(quote_info, dict) else {}
        quote = data.get("quote")
        provider = (
            (quote_info.get("provider") or metadata.get("quote_provider"))
            if quote else (metadata.get("provider") or metadata.get("source"))
        )
        data_as_of = quote_info.get("data_as_of") if quote_info else metadata.get("data_as_of")
        if not provider or str(provider).strip().lower() in {"unknown", "none", "n/a"} or not (quote or data.get("chart_series")):
            raise ValueError("fallback_has_no_source_data")
        attempts.append({"provider": str(provider), "status": "ok", "reason": None})
        gaps.append("Fallback returned a market snapshot; it may not cover every requested fact.")
        if not data_as_of:
            gaps.append("The source did not provide a verifiable observation time.")
        return _response(query, status="partial", provider=str(provider), data_as_of=data_as_of,
                         records=[{"kind": "stock_analysis", "data": data}], attempts=attempts,
                         data_gaps=gaps)
    except Exception as exc:  # do not expose vendor response or local paths
        reason = str(exc) if str(exc) in {"fallback_identity_unverified", "fallback_identity_mismatch"} else type(exc).__name__
        attempts.append({"provider": "Financial Bot fallback", "status": "failed", "reason": reason})
        if reason == "fallback_identity_unverified":
            gaps.append("The fallback source did not return a verifiable security identifier for the requested security.")
        elif reason == "fallback_identity_mismatch":
            gaps.append("The fallback source returned a different security identifier; its data was discarded.")
        gaps.append("No configured Financial Bot source returned verifiable data for this query.")
        return _response(query, status="unavailable", provider=None, data_as_of=None,
                         records=[], attempts=attempts, data_gaps=gaps)
