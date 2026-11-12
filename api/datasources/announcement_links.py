"""Best-effort official CNINFO URL matching for announcement rows."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit


def _title_key(value: Any) -> str:
    title = re.sub(r"\s+", "", str(value or "")).strip()
    # iFinD sometimes prefixes the title with the company name and a colon.
    prefix = re.match(r"^.{1,32}?[：:]", title)
    if prefix:
        title = title[prefix.end():]
    return title.casefold()


def _official_url(value: Any) -> str | None:
    url = str(value or "").strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname != "www.cninfo.com.cn":
        return None
    return url


def merge_official_announcement_urls(
    primary_rows: list[dict[str, Any]], official_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fill missing primary-source URLs from exact normalized CNINFO titles."""
    urls_by_title = {
        key: url
        for row in official_rows
        if (key := _title_key(row.get("title")))
        and (url := _official_url(row.get("url")))
    }
    merged = []
    for row in primary_rows:
        if row.get("url"):
            merged.append(row)
            continue
        url = urls_by_title.get(_title_key(row.get("title")))
        merged.append({**row, "url": url} if url else row)
    return merged
