"""Index/board ids used by iFinD's constitution report (``data_pool`` / ``p03291``).

The vendor requires a board id, not a ticker, and an unknown id returns an empty
list instead of an error - so the mapping has to be explicit. Values were read
from the vendor's own board dictionary (``get_blocks.py``), not guessed.
"""

from __future__ import annotations

# index code (as our product writes it) -> (block id, display name)
BLOCK_BY_INDEX: dict[str, tuple[str, str]] = {
    "000300": ("001045011006001", "沪深300"),
    "000905": ("001045011006004", "中证500"),
    "000016": ("001005260", "上证50"),
    "399006": ("001045009021006", "创业板指"),
    "000688": ("001045009015029", "科创50"),
    "ALL_A": ("001005010", "全部A股"),
}


def block_for_index(index_code: str) -> tuple[str, str] | None:
    raw = str(index_code or "").strip().upper()
    if raw in BLOCK_BY_INDEX:
        return BLOCK_BY_INDEX[raw]
    digits = raw.split(".")[0]
    return BLOCK_BY_INDEX.get(digits)
