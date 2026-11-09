"""Deterministic relevance checks for retrieved web results.

Search providers can return a result because it shares a generic word with the
query (for example "Applied" when the user asked about Applied Materials).  We
prefer to omit that result entirely rather than presenting it as evidence.  The
check is intentionally conservative: if the query has no meaningful terms we
keep the result, and a quoted phrase or stock code must appear literally.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "what", "when",
    "where", "which", "who", "why", "how", "about", "latest", "recent",
    "news", "update", "updates", "today", "now", "new", "company", "stock",
    "stocks", "share", "shares", "market", "markets", "please", "tell",
    "give", "show", "find", "search", "research", "analysis", "report",
    "2023", "2024", "2025", "2026",
}

_ZH_STOP_PHRASES = (
    "为什么", "最近", "最新", "消息", "新闻", "公告", "动态", "今天", "现在",
    "如何", "怎么", "分析", "一下", "相关", "影响", "情况", "是否", "可以",
    "请问", "帮我", "看看", "了解", "这个", "那个", "哪些", "有什么",
)


def _haystack(row: Mapping[str, Any]) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("title", "snippet", "summary", "description", "source", "url")
    ).casefold()


def _query_terms(query: str) -> tuple[list[str], list[str]]:
    """Return (literal phrases/codes, meaningful terms)."""
    text = (query or "").casefold()
    phrases = [value.strip().casefold() for value in re.findall(r'"([^"]{2,80})"', text) if value.strip()]
    codes = re.findall(r"\b(?:us|hk|sh|sz)\.[a-z0-9]{1,10}\b|\b\d{4,6}(?:\.(?:us|hk|sh|sz))?\b", text)
    # A bare four-digit calendar year in a research question is not a ticker.
    # A suffixed symbol such as 2026.HK remains an explicit code.
    codes = [code for code in codes if not re.fullmatch(r"(?:19|20)\d{2}", code)]
    latin = [
        token for token in re.findall(r"[a-z][a-z0-9&.+-]{2,}", text)
        if token not in _STOPWORDS and token not in {"us", "hk", "sh", "sz", "inc", "ltd", "plc"}
    ]
    # Chinese segmentation is deliberately avoided: overlapping bigrams are a
    # cheap, stable proxy and still require a real topic token in the result.
    chinese_text = re.sub(r"[\s，。！？、；：,.!?;:（）()\[\]【】“”\"']", "", text)
    for phrase in _ZH_STOP_PHRASES:
        chinese_text = chinese_text.replace(phrase, "")
    chinese = []
    for segment in re.findall(r"[\u3400-\u9fff]+", chinese_text):
        chinese.extend(segment[index:index + 2] for index in range(0, max(0, len(segment) - 1)))
    terms = list(dict.fromkeys([*latin, *chinese]))
    return list(dict.fromkeys([*phrases, *codes])), terms


def web_result_is_relevant(row: Mapping[str, Any], query: str) -> bool:
    """Reject obviously off-topic search hits without a model call."""
    haystack = _haystack(row)
    if not haystack.strip():
        return False
    literals, terms = _query_terms(query)
    if literals:
        # A quoted phrase or explicit ticker is a hard requirement.
        return any(literal in haystack for literal in literals)
    if not terms:
        return True
    hits = sum(1 for term in terms if term in haystack)
    # One generic word is not enough for a multi-term query. This rejects
    # "Applied Industrial Technologies" for "Applied Materials semiconductor".
    required = min(2, max(1, (len(terms) + 1) // 2))
    return hits >= required
