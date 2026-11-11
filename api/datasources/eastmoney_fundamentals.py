"""Secondary US/HK fundamentals adapter (Eastmoney) for yfinance outages.

The adapter normalizes Eastmoney's long-form report table (one row per line
item and period) into the same ``periods: [{period, values}]`` envelope used by
the yfinance provider.  It is intentionally a fallback: Eastmoney is an
aggregator, not a primary filing source, and SEC/HKEX remain authoritative when
the question is about an actual filing.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from ashare.stock_pool import extract_symbol, normalize_stock_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _statement_name(statement: str, market: str) -> str:
    if market == "us":
        return {"income": "综合损益表", "balance": "资产负债表", "cashflow": "现金流量表"}[statement]
    return {"income": "利润表", "balance": "资产负债表", "cashflow": "现金流量表"}[statement]


def eastmoney_financials(stock_code: str, statement: str = "income", quarterly: bool = False, periods: int = 4) -> dict[str, Any]:
    normalized = normalize_stock_code(stock_code)
    market = "us" if normalized.upper().startswith("US.") else "hk" if normalized.upper().endswith(".HK") else ""
    if market not in {"us", "hk"}:
        raise ValueError("eastmoney_fundamentals only supports US/HK")
    statement = (statement or "income").strip().lower()
    if statement not in {"income", "balance", "cashflow"}:
        raise ValueError("statement must be income, balance or cashflow")
    periods = max(1, min(8, int(periods)))
    import akshare as ak

    if market == "us":
        symbol = extract_symbol(normalized)
        frame = ak.stock_financial_us_report_em(
            stock=symbol,
            symbol=_statement_name(statement, market),
            indicator="单季报" if quarterly else "年报",
        )
        item_column, amount_column = "ITEM_NAME", "AMOUNT"
        endpoint = "emweb.eastmoney.com/PC_USF10/pages/index.html"
        currency = "USD"
    else:
        symbol = extract_symbol(normalized)
        frame = ak.stock_financial_hk_report_em(
            stock=symbol,
            symbol=_statement_name(statement, market),
            indicator="报告期" if quarterly else "年度",
        )
        item_column, amount_column = "STD_ITEM_NAME", "AMOUNT"
        endpoint = "emweb.securities.eastmoney.com/PC_HKF10/FinancialAnalysis/index"
        currency = "HKD"
    if frame is None or getattr(frame, "empty", True):
        raise ValueError("Eastmoney returned no financial report rows")
    required = {"REPORT_DATE", item_column, amount_column}
    if not required.issubset(set(frame.columns)):
        raise ValueError(f"Eastmoney report schema missing: {sorted(required - set(frame.columns))}")
    records = frame.to_dict("records")
    grouped: dict[str, dict[str, float | None]] = {}
    for row in records:
        raw_date = str(row.get("REPORT_DATE") or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
            continue
        label = str(row.get(item_column) or "").strip()
        if not label:
            continue
        grouped.setdefault(raw_date, {})[label] = _number(row.get(amount_column))
    rows = [
        {"period": day, "values": values}
        for day, values in sorted(grouped.items(), reverse=True)[:periods]
    ]
    if not rows:
        raise ValueError("Eastmoney financial report had no usable periods")
    digest = hashlib.sha256(
        "|".join(f"{row['period']}:" + ",".join(f"{k}={v}" for k, v in sorted(row["values"].items())) for row in rows).encode("utf-8")
    ).hexdigest()
    return {
        "provider": "eastmoney",
        "endpoint": endpoint,
        "symbol": normalized,
        "market": market,
        "statement": statement,
        "quarterly": quarterly,
        "currency": currency,
        "currency_basis": "report_currency_default_proxy",
        "as_of": rows[0]["period"],
        "retrieved_at": _now(),
        "periods": rows,
        "adjustment": "not_applicable",
        "raw_sha256": digest,
        "data_gaps": [
            "Eastmoney 美股/港股财报是二级聚合源，不能替代 SEC/HKEX 原始 filing；",
            "币种按市场默认推断，未逐行校验报告币种。",
        ],
        "note": "yfinance US/HK fundamentals fallback; secondary aggregator.",
    }
