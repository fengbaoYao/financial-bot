"""Conservative extraction of explicit user statements and model view proposals.

Rules:
* Only first-person, explicit statements become long-term facts.
* A normal question ("腾讯怎么样") never invents holdings, trades or motives.
* Model-proposed investment views are always pending candidates; this module
  never writes a confirmed viewpoint on the model's behalf.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

MAX_FACT_LENGTH = 240

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "holding",
        re.compile(
            r"(?:我|本人)(?:目前|现在|已经|还)?(?:持有|持仓|拿着|买了|买入)(?:了)?(?P<value>[^。；;！？\n]{1,120})"
        ),
    ),
    (
        "watch_object",
        re.compile(
            r"(?:我|本人)(?:目前|现在|最近|一直)?(?:关注|在看|盯着|留意)(?:着|了)?(?P<value>[^。；;！？\n]{1,120})"
        ),
    ),
    (
        "preference",
        re.compile(
            r"(?:我|本人)(?:更|比较|非常|一直)?(?:偏好|喜欢|倾向(?:于)?|不喜欢|不想|不接受)(?P<value>[^。；;！？\n]{1,120})"
        ),
    ),
    (
        "research_question",
        re.compile(
            r"(?:请|帮我|帮忙|我想|我要)?(?:持续|长期|一直|以后|之后|后续)(?:跟踪|关注|研究|盯)(?P<value>[^。；;！？\n]{1,120})"
        ),
    ),
)

_KIND_LABELS_ZH = {
    "holding": "持仓陈述",
    "watch_object": "关注对象",
    "preference": "明确偏好",
    "research_question": "研究问题",
}


def extract_explicit_memories(query: str) -> list[dict[str, str]]:
    text = (query or "").strip()
    if not text:
        return []
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            value = re.sub(r"\s+", " ", match.group("value")).strip(" ，,、:：")
            if len(value) < 1:
                continue
            fact = value[:MAX_FACT_LENGTH]
            key = (kind, fact.lower())
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {
                    "kind": kind,
                    "text": fact,
                    "source_text": _sentence_around(text, match.start(), match.end()),
                    "label": _KIND_LABELS_ZH.get(kind, kind),
                }
            )
    return found


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind(mark, 0, start) for mark in ("。", "；", ";", "！", "？", "\n")) + 1
    right_candidates = [text.find(mark, end) for mark in ("。", "；", ";", "！", "？", "\n")]
    right_candidates = [value for value in right_candidates if value != -1]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    return text[left:right].strip()[:MAX_FACT_LENGTH]


def candidate_payloads(response: Any) -> list[dict[str, str]]:
    """Validate the model's pending view proposals from a response payload.

    Any malformed proposal is dropped. Nothing here is a user statement.
    """
    payload = getattr(response, "payload", None) or {}
    raw = payload.get("viewpoint_proposals") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    proposals: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw[:3]:
        if not isinstance(item, dict):
            continue
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if len(text) < 8 or len(text) > 500:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        proposals.append(
            {
                "text": text,
                "rationale": re.sub(r"\s+", " ", str(item.get("rationale") or "")).strip()[:500],
            }
        )
    return proposals
