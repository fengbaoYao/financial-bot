"""Private run diagnostics and conservative, explicitly provisional usage estimates."""
import json
import os
import re
import time
from pathlib import Path


def specialist_failure_category(exc: Exception) -> str:
    """Return a fixed diagnostic code, never exception text or provider data."""
    if type(exc).__name__ != "UnexpectedModelBehavior":
        return "other"
    message = str(exc)
    if message.startswith("Model token limit ("):
        return "output_token_limit"
    if message.startswith("Exceeded maximum output retries ("):
        cause = exc.__cause__
        reason = str(cause) if cause and type(cause).__name__ == "ModelRetry" else ""
        if reason == "Select the retrieved evidence IDs supporting your answer.":
            return "output_evidence_selection"
        if reason.startswith("Cite the relevant financial or news evidence inline"):
            return "output_inline_evidence"
        if reason.startswith("Use only evidence IDs returned by the tools"):
            return "output_unknown_evidence"
        return "output_retries_exhausted"
    return "unexpected_model_behavior"


def _safe_tool_diagnostic(item: dict) -> dict:
    result = {key: value for key, value in item.items() if key in {
        "tool", "status", "reason_code", "endpoint", "retrieved_at", "evidence", "data_gaps"}}
    if item.get("reason_code") == "specialist_error":
        if item.get("error_category") in {"other", "output_token_limit", "output_evidence_selection", "output_inline_evidence", "output_unknown_evidence", "output_retries_exhausted", "unexpected_model_behavior"}:
            result["error_category"] = item["error_category"]
        for gap in (item.get("data") or {}).get("data_gaps", []):
            match = re.fullmatch(r"(?:Specialist|Financial|Comparison) review unavailable \(([A-Za-z][A-Za-z0-9_]*)\); use original sources\.", str(gap))
            if match:
                result["error_type"] = match.group(1)
                break
    return result


def run_metadata(deps, status: str, failure: str | None = None) -> dict:
    usage = deps.run_usage
    unresolved = (deps.provider_requests_started != deps.provider_requests_finished
                  or deps.provider_requests_started != usage.requests)
    # Peak DeepSeek Flash rates; charge all input as uncached. This overestimates
    # cache/off-peak savings, but excludes any provider call lacking usage.
    flash = deps.model_name.removeprefix("openai:") == "deepseek-flash"
    known_estimate = round((usage.input_tokens * 0.30 + usage.output_tokens * 1.20) / 1_000_000, 8) if flash else None
    return {"run_id": deps.run_id, "status": status, "failure_type": failure,
            "finish_reason": deps.finish_reason or "completed", "latency_seconds": round(time.monotonic() - deps.started_at, 3),
            "model": deps.model_name, "usage": {"requests": usage.requests, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
            "usage_unresolved": unresolved, "estimated_cost_usd": known_estimate,
            "cost_basis": "DeepSeek Flash peak rates, all input uncached; known usage only; provisional, not billing reconciliation" if flash else "Model pricing unavailable",
            "provider_requests_started": deps.provider_requests_started, "provider_requests_finished": deps.provider_requests_finished,
            "retrieval_calls": deps.retrieval_count, "cache_hits": list(dict.fromkeys(deps.cache_hits))}


def response_run_status(response, fallback: str = "incomplete") -> str:
    """Treat a usable synthesis recovery as completed while preserving failure metadata."""
    payload = getattr(response, "payload", None)
    meta = payload.get("_meta") if isinstance(payload, dict) else None
    if not isinstance(meta, dict):
        return fallback
    recovered = meta.get("llm_research_recovery") or meta.get("evidence_synthesis_recovery")
    if recovered and not meta.get("synthesis_incomplete") and (getattr(response, "summary", "") or "").strip():
        return "completed"
    return fallback


def save_run(deps, status: str, failure: str | None = None, response=None) -> dict:
    metadata = run_metadata(deps, status, failure)
    path = Path(os.environ.get("RESEARCH_RUN_DIR", str(Path(__file__).resolve().parents[1] / "output" / "research-runs")))
    try:
        path.mkdir(parents=True, exist_ok=True)
        # No credentials, private reasoning, model messages, conversation history
        # or provider error bodies. These files are ignored by Git.
        record = {"metadata": metadata, "tools": [_safe_tool_diagnostic(item) for item in deps.tool_results]}
        if response is not None:
            record["report"] = response.model_dump()
        temp = path / f"{deps.run_id}.tmp"
        temp.write_text(json.dumps(record, ensure_ascii=False, default=str), encoding="utf-8")
        temp.replace(path / f"{deps.run_id}.json")
    except OSError:
        metadata["diagnostics_saved"] = False
    else:
        metadata["diagnostics_saved"] = True
    return metadata
