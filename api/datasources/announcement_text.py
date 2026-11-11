"""Normalize noisy announcement titles before UI / model context.

iFinD (and some aggregators) sometimes return reportTitle as a raw dump:
comma-separated listing codes, bond short names, then an English MTN/notes
sentence. That is unusable in 「来源与数据覆盖」 and wastes model context.
"""

from __future__ import annotations

import re

_CODE_SOUP = re.compile(
    r"^(?:"
    r"(?:\d{4,5}|[A-Z]{2,12}\s*N?\d{2,6})"
    r"(?:\s*,\s*(?:\d{4,5}|[A-Z]{2,12}\s*N?\d{2,6})){3,}"
    r")\s*[,:;\-–—]?\s*",
    re.I,
)
_NOTES = re.compile(
    r"Senior Notes|Medium Term Note|\bMTN\b|Pricing of US\$|Global Medium Term Note",
    re.I,
)
_PRICING_PHRASE = re.compile(
    r"(Publication of Pricing of[^.]{8,160}|"
    r"US\$[\d,]+(?:\s+and\s+US\$[\d,]+)*(?:\s+[\d.]+%\s+Senior Notes Due \d{4})+"
    r"(?:\s+and\s+US\$[\d,]+\s+[\d.]+%\s+Senior Notes Due \d{4})*)",
    re.I,
)
_ISSUER = re.compile(r"\bTENCENT\b|腾讯", re.I)
_AMOUNT_NOTE = re.compile(r"US\$[\d,]+\s+[\d.]+%\s+Senior Notes Due \d{4}", re.I)


def sanitize_announcement_title(title: str | None, *, language: str = "zh", max_len: int = 140) -> str:
    """Return a short human-readable announcement title.

    Never invent facts beyond what the raw title already states. When the raw
    string is only a code soup with no readable phrase, return a labeled stub
    that names the document type instead of echoing the soup.
    """
    raw = re.sub(r"\s+", " ", str(title or "").strip())
    if not raw:
        return ""
    text = _CODE_SOUP.sub("", raw).strip(" ,;:-–—")
    if not text:
        text = raw

    en = str(language or "").lower().startswith("en")
    if _NOTES.search(raw) or _NOTES.search(text):
        issuer_zh = "腾讯控股" if _ISSUER.search(raw) else "公司"
        issuer_en = "Tencent" if _ISSUER.search(raw) else "Company"
        amounts = _AMOUNT_NOTE.findall(raw)
        phrase = _PRICING_PHRASE.search(raw) or _PRICING_PHRASE.search(text)
        if amounts:
            detail = "；".join(amounts[:3])
            text = (
                f"{issuer_en}: pricing of {detail}"
                if en
                else f"{issuer_zh}：美元优先票据定价/发行（{detail}）"
            )
        elif phrase:
            clipped = re.sub(r"\s+", " ", phrase.group(1)).strip()
            text = clipped if en else f"{issuer_zh}：{clipped}"
        else:
            text = (
                f"{issuer_en}: USD senior notes / MTN pricing or issuance announcement"
                if en
                else f"{issuer_zh}：美元优先票据（MTN）定价或发行公告"
            )

    # Collapse leftover dense code lists that survived the prefix strip.
    if len(re.findall(r"\b\d{4,5}\b", text)) >= 8 and len(text) > max_len:
        text = re.sub(r"(?:\d{4,5}\s*,\s*){3,}\d{4,5}", "…", text)

    text = re.sub(r"\s+", " ", text).strip(" ,;:-–—")
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def is_noisy_announcement_title(title: str | None) -> bool:
    """True when a title looks like a raw code/MTN dump and should be sanitized."""
    raw = str(title or "")
    if not raw:
        return False
    code_hits = len(re.findall(r"\b\d{4,5}\b", raw))
    return code_hits >= 6 or bool(_CODE_SOUP.match(raw)) or (
        bool(_NOTES.search(raw)) and code_hits >= 3
    )
