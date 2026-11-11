"""Build source references from retrieved data, never model-generated URLs."""
import hashlib
import json
from urllib.parse import parse_qsl, urlparse

from api.datasources.announcement_text import sanitize_announcement_title
from api.schemas import ResearchEvidence


def safe_source_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    sensitive = {"token", "access_token", "api_key", "apikey", "key", "userid", "user_id", "ip", "password", "signature", "sig"}
    if any(key.lower() in sensitive or key.lower().endswith("_token") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        return None
    if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password:
        return value
    if value.startswith("/api/") and not value.startswith("//"):
        return value
    return None


def sources_for_tool(item: dict) -> list[ResearchEvidence]:
    tool, data = item.get("tool"), item.get("data")
    retrieved = item["retrieved_at"]
    sources = []
    if not data or item.get("status") == "unavailable":
        return []
    if tool == "ifind_smart_query":
        raw = json.dumps(data.get("data") or {}, ensure_ascii=False, default=str)
        identity = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return [ResearchEvidence(
            id="ev_" + identity,
            source="同花顺 iFinD",
            url=None,
            retrieved_at=retrieved,
            data_as_of=None,
            freshness="provider_data_time_unverified",
            title="同花顺智能查询",
            excerpts=[{"location": "ifind_smart_query", "text": raw[:1200]}],
            provider_endpoint=data.get("endpoint"),
        )]
    if tool == "get_sec_company_data":
        for row in [*data.get("filings", []), *data.get("financials", [])]:
            sources.append(("SEC " + (row.get("concept") or row.get("form", "filing")), row.get("url"), row.get("end") or row.get("report_date") or row.get("filed"), row.get("freshness", "reported_period"), row))
    elif tool in {"get_stock_news", "get_global_news", "web_search"}:
        for row in data:
            sources.append((row.get("source") or "Provider summary", row.get("url"), row.get("published_at"), "provider_summary", row))
    elif tool == "read_public_source":
        sources.append((data.get("source") or "Original document", data.get("url"), data.get("published_at"), "source_excerpts", data))
    elif tool == "get_stock_analysis":
        bars = data.get("chart_series", [])
        date = bars[-1].get("date") if bars else None
        metadata = data.get("metadata", {}) or {}
        sources.append((str(metadata.get("source") or "Daily market data"), f"/api/stocks/{data.get('stock_code')}/analysis?include_ai=false", date, "historical_or_delayed", {
            "stock_code": data.get("stock_code"),
            "last_bar": bars[-1] if bars else None,
            "fetched_at": metadata.get("retrieved_at"),
            "provider_endpoint": metadata.get("provider_endpoint"),
            "currency": metadata.get("currency"),
            "adjustment": metadata.get("adjustment"),
            "raw_ref": metadata.get("raw_ref"),
            "raw_sha256": metadata.get("raw_sha256"),
        }))
        quote_info = metadata.get("ifind_quote") or {}
        if isinstance(quote_info, dict) and quote_info.get("provider") and data.get("quote"):
            sources.append((str(quote_info["provider"]), f"/api/stocks/{data.get('stock_code')}/analysis?include_ai=false",
                quote_info.get("data_as_of"), quote_info.get("price_status") or "delayed_or_snapshot", {
                    "stock_code": data.get("stock_code"), "quote": data.get("quote"),
                    "fetched_at": metadata.get("retrieved_at"), "currency": metadata.get("currency"),
                    "record_key": f"quote:{data.get('stock_code')}",
                }))
    elif tool == "search_stocks":
        sources.append(("Stock identity search", "/api/stocks/search", None, "identity_mapping", data))
    elif tool in {"get_portfolio_analysis", "list_hotspots", "get_hotspot_detail"}:
        path = {"get_portfolio_analysis": "/api/portfolio/analysis", "list_hotspots": "/api/hotspots", "get_hotspot_detail": "/api/hotspots"}[tool]
        sources.append((tool, path, None, "retrieved_snapshot", data))
    result = []
    if tool == "get_benchmark_comparison":
        windows = data.get("windows") or []
        summary = "；".join(
            f"{row.get('sessions')}日 标的 {row.get('stock_change_pct')}% / 基准 {row.get('benchmark_change_pct')}% / 相对 {row.get('relative_pct')}%"
            for row in windows[:3]
        ) or "无重叠收益窗口"
        identity = hashlib.sha256(json.dumps([tool, data.get("stock_code"), data.get("benchmark_code"), data.get("data_as_of")], sort_keys=True, default=str).encode()).hexdigest()[:16]
        result.append(ResearchEvidence(
            id="ev_" + identity,
            source=data.get("benchmark_label") or "Benchmark",
            url=safe_source_url(data.get("endpoint")) if isinstance(data.get("endpoint"), str) else None,
            retrieved_at=retrieved,
            data_as_of=data.get("data_as_of"),
            freshness="historical_or_delayed",
            title=f"{data.get('stock_code')} vs {data.get('benchmark_label') or data.get('benchmark_code')}",
            excerpts=[{"location": "benchmark_windows", "text": summary[:1200]}],
            provider_endpoint=data.get("endpoint"),
            record_key=f"{data.get('stock_code')}:{data.get('benchmark_code')}",
        ))
        return result
    if tool == "get_cninfo_announcements":
        for row in data.get("announcements", []) or []:
            identity = hashlib.sha256(json.dumps([tool, row.get("record_key"), row.get("title")], sort_keys=True, default=str).encode()).hexdigest()[:16]
            result.append(ResearchEvidence(
                id="ev_" + identity,
                source="巨潮资讯",
                url=safe_source_url(row.get("url")),
                retrieved_at=data.get("retrieved_at", retrieved),
                data_as_of=row.get("date"),
                freshness="official_filing",
                title=row.get("title"),
                provider_endpoint=data.get("endpoint"),
                adjustment=data.get("adjustment"),
                raw_ref=data.get("raw_ref"),
                raw_sha256=data.get("raw_sha256"),
                record_key=row.get("record_key") or row.get("url"),
            ))
        return result
    if tool == "get_index_constituents":
        members = data.get("members", []) or []
        sample = "；".join(
            f"{row.get('code')} {row.get('name')} {row.get('weight_percent')}%"
            for row in members[:5]
        )
        result.append(ResearchEvidence(
            id="ev_" + hashlib.sha256(json.dumps([tool, data.get("index_code"), data.get("data_as_of")], sort_keys=True, default=str).encode()).hexdigest()[:16],
            source=data.get("provider_label") or "指数官方",
            url=safe_source_url(data.get("endpoint")),
            retrieved_at=data.get("retrieved_at", retrieved),
            data_as_of=data.get("data_as_of"),
            freshness="official_index_snapshot",
            title=f"{data.get('provider_label') or '指数官方'} {data.get('index_code')} 成分权重（{data.get('member_count')} 只）",
            excerpts=[{"location": "官方成分文件", "text": f"权重合计 {data.get('weight_total_percent')}%；前五：{sample}"}],
            provider_endpoint=data.get("endpoint"),
            currency=data.get("currency"),
            adjustment=data.get("adjustment"),
            raw_ref=data.get("raw_ref"),
            raw_sha256=data.get("raw_sha256"),
            record_key=data.get("index_code"),
        ))
        return result
    if tool == "get_company_fundamentals":
        is_yfinance = data.get("provider") == "yfinance"
        source = "yfinance" if is_yfinance else "新浪财报"
        endpoint = data.get("endpoint")
        for row in data.get("periods", []) or []:
            values = row.get("values") if isinstance(row.get("values"), dict) else row.get("items") or {}
            excerpt = "；".join(f"{key}={value}" for key, value in values.items() if value is not None)
            if not excerpt:
                continue
            result.append(ResearchEvidence(
                id="ev_" + hashlib.sha256(json.dumps([tool, row, source], sort_keys=True, default=str).encode()).hexdigest()[:16],
                source=source,
                url=safe_source_url(endpoint) if isinstance(endpoint, str) and endpoint.startswith(("http://", "https://")) else None,
                retrieved_at=data.get("retrieved_at", retrieved),
                data_as_of=row.get("period"),
                freshness="reported_period",
                title=f"{data.get('statement') or data.get('statement_label') or 'Financial statements'} {row.get('period')}",
                excerpts=[{"location": source, "text": excerpt[:1200]}],
                provider_endpoint=endpoint,
                currency=data.get("currency"),
                adjustment=data.get("adjustment"),
                raw_ref=data.get("raw_ref"),
                raw_sha256=data.get("raw_sha256"),
                record_key=row.get("period"),
            ))
        return result
    if tool == "get_analyst_estimates":
        is_yfinance = data.get("provider") == "yfinance"
        source = "yfinance" if is_yfinance else "同花顺一致预期"
        if is_yfinance:
            targets = data.get("targets") or {}
            excerpt = "；".join(f"{key}={value}" for key, value in targets.items() if value is not None)
            title = f"{data.get('symbol')} 分析师目标价"
        else:
            years = data.get("years") or []
            excerpt = "；".join(
                f"{row.get('period')}: 均值 {row.get('mean')}，区间 {row.get('min')}–{row.get('max')}，机构数 {row.get('count')}"
                for row in years if row.get("mean") is not None
            )
            title = f"{data.get('symbol')} 一致预期 EPS"
        if excerpt:
            result.append(ResearchEvidence(
                id="ev_" + hashlib.sha256(json.dumps([tool, data.get("symbol"), excerpt], sort_keys=True, default=str).encode()).hexdigest()[:16],
                source=source,
                url="https://query1.finance.yahoo.com/" if is_yfinance else "https://basic.10jqka.com.cn/",
                retrieved_at=data.get("retrieved_at", retrieved),
                data_as_of=data.get("as_of") or data.get("data_as_of"),
                freshness="consensus_estimate",
                title=title,
                excerpts=[{"location": source, "text": excerpt[:1200]}],
                provider_endpoint=data.get("endpoint"),
                currency=data.get("currency"),
                adjustment=data.get("adjustment"),
                raw_ref=data.get("raw_ref"),
                raw_sha256=data.get("raw_sha256"),
                record_key=data.get("symbol"),
            ))
        return result
    if tool == "get_financial_statements":
        for row in data.get("periods", []) or []:
            items = row.get("items") or {}
            excerpt = "；".join(f"{key}={value}" for key, value in items.items() if value is not None)
            if not excerpt:
                continue
            result.append(ResearchEvidence(
                id="ev_" + hashlib.sha256(json.dumps([tool, row], sort_keys=True, default=str).encode()).hexdigest()[:16],
                source="新浪财报",
                url="https://quotes.sina.cn/",
                retrieved_at=data.get("retrieved_at", retrieved),
                data_as_of=row.get("period"),
                freshness="reported_period",
                title=f"{data.get('statement_label')} {row.get('period')}",
                excerpts=[{"location": "新浪财报三表", "text": excerpt}],
                provider_endpoint=data.get("endpoint"),
                currency=data.get("currency"),
                adjustment=data.get("adjustment"),
                raw_ref=data.get("raw_ref"),
                raw_sha256=data.get("raw_sha256"),
                record_key=row.get("period"),
            ))
        return result
    if tool == "get_consensus_estimates":
        for row in data.get("years", []) or []:
            if row.get("mean") is None:
                continue
            excerpt = f"{row.get('period')}: 均值 {row.get('mean')}，区间 {row.get('min')}–{row.get('max')}，机构数 {row.get('count')}"
            identity = hashlib.sha256(json.dumps([tool, row], sort_keys=True, default=str).encode()).hexdigest()[:16]
            result.append(ResearchEvidence(
                id="ev_" + identity,
                source="同花顺一致预期",
                url="https://basic.10jqka.com.cn/",
                retrieved_at=data.get("retrieved_at", retrieved),
                data_as_of=data.get("data_as_of"),
                freshness="consensus_estimate",
                title=f"{row.get('period')} EPS 一致预期",
                excerpts=[{"location": "worth.html", "text": excerpt}],
                provider_endpoint=data.get("endpoint"),
                currency=data.get("currency"),
                adjustment=data.get("adjustment"),
                raw_ref=data.get("raw_ref"),
                raw_sha256=data.get("raw_sha256"),
                record_key=row.get("period"),
            ))
        return result
    for source, url, date, freshness, row in sources:
        identity = hashlib.sha256(json.dumps([tool, url, row], sort_keys=True, default=str).encode()).hexdigest()[:16]
        result.append(ResearchEvidence(id="ev_" + identity, source=source, url=safe_source_url(url),
            retrieved_at=(row.get("fetched_at") or retrieved) if isinstance(row, dict) else retrieved,
            data_as_of=date or None, freshness=freshness,
            title=sanitize_announcement_title(row.get("title")) if isinstance(row, dict) else None,
            excerpts=row.get("excerpts", []) if tool == "read_public_source" else [],
            provider_endpoint=row.get("provider_endpoint") if isinstance(row, dict) else None,
            currency=row.get("currency") if isinstance(row, dict) else None,
            adjustment=row.get("adjustment") if isinstance(row, dict) else None,
            raw_ref=row.get("raw_ref") if isinstance(row, dict) else None,
            raw_sha256=row.get("raw_sha256") if isinstance(row, dict) else None,
            record_key=row.get("record_key") if isinstance(row, dict) else None))
    return result
