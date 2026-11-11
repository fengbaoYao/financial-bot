from __future__ import annotations
import json, os, signal, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

RESULTS = []

class Timeout(Exception): pass

def _alarm(signum, frame):
    raise Timeout("timeout")

def probe(name, fn, timeout=18, configured=True):
    if not configured:
        RESULTS.append({"source": name, "status": "not_configured", "latency_ms": None, "detail": "missing key/config"})
        print(name, "not_configured", flush=True)
        return
    old = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    start = time.perf_counter()
    try:
        detail = fn()
        RESULTS.append({"source": name, "status": "ok", "latency_ms": round((time.perf_counter()-start)*1000), "detail": detail})
        print(name, "ok", RESULTS[-1]["latency_ms"], detail, flush=True)
    except Exception as exc:
        RESULTS.append({"source": name, "status": "error", "latency_ms": round((time.perf_counter()-start)*1000), "detail": f"{type(exc).__name__}: {str(exc)[:180]}"})
        print(name, "error", RESULTS[-1]["latency_ms"], RESULTS[-1]["detail"], flush=True)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)

def main():
    from api.datasources.fuyao_provider import FuyaoProvider
    p = FuyaoProvider()

    def q():
        x=p.quote("sh600519"); return f"{x['price']} @ {x['as_of']}"
    def h():
        now=datetime.now(timezone.utc); x=p.history("sh600519", int((now-timedelta(days=45)).timestamp()*1000), int(now.timestamp()*1000)); return f"{len(x['bars'])} bars, last {x['bars'][-1]['date']}"
    def f():
        x=p.financial_statements("sh600519","income",1); return f"{x['periods'][0]['period']} revenue={x['periods'][0]['items'].get('revenue')}"
    def v():
        x=p.valuations(["sh600519","sz000858"]); return f"{len(x['items'])} items"
    def s():
        x=p.search("贵州茅台"); return f"{len(x['items'])} items"
    def cal():
        end=datetime.now(); start=end-timedelta(days=10); x=p.trading_days(start.strftime("%Y%m%d"), end.strftime("%Y%m%d")); return f"{len(x['days'])} days"
    def idx():
        x=p.index_constituents("000300.SH"); return f"{len(x['members'])} members"
    probe("fuyao_quote", q, configured=p.configured)
    probe("fuyao_history", h, configured=p.configured)
    probe("fuyao_financials", f, configured=p.configured)
    probe("fuyao_valuations", v, configured=p.configured)
    probe("fuyao_search", s, configured=p.configured)
    probe("fuyao_calendar", cal, configured=p.configured)
    probe("fuyao_index", idx, configured=p.configured)

    import Ashare
    def sina():
        df=Ashare.get_price_sina("sh600519", count=5, frequency="1d"); return f"{len(df)} rows"
    probe("sina_history", sina, timeout=15)
    def tx():
        df=Ashare.get_price_day_tx("sh600519", count=5, frequency="1d"); return f"{len(df)} rows"
    probe("tencent_history", tx, timeout=15)
    import akshare as ak
    def akh():
        df=ak.stock_zh_a_hist(symbol="600519", period="daily", start_date="20261001", end_date="20261030", adjust="qfq"); return f"{len(df)} rows"
    probe("akshare_history", akh, timeout=20)

    from ashare.data import DataFetcher
    def yfus(): 
        df=DataFetcher().fetch_stock_data("us.aapl", count=5); return f"{0 if df is None else len(df)} rows"
    def yfhk():
        df=DataFetcher().fetch_stock_data("hk00700", count=5); return f"{0 if df is None else len(df)} rows"
    probe("yfinance_us", yfus, timeout=25)
    probe("yfinance_hk", yfhk, timeout=25)

    from api.local_research import cninfo_announcements, consensus_estimates, index_constituents, financial_statements
    def cn(): 
        x=cninfo_announcements("sh600519", 3); return f"{len(x['announcements'])} announcements"
    def ths():
        x=consensus_estimates("sh600519"); return f"{len(x['years'])} years"
    def csi():
        x=index_constituents("000300","csi"); return f"{x['member_count']} members"
    def cni():
        x=index_constituents("399300","cni"); return f"{x['member_count']} members"
    def sinafin():
        os.environ["FUYAO_FINANCIALS"]="0"
        try:
            x=financial_statements("sh600519","income",1); return f"{x['periods'][0]['period']} revenue={x['periods'][0]['items'].get('revenue')}"
        finally:
            os.environ.pop("FUYAO_FINANCIALS", None)
    probe("cninfo_announcements", cn, timeout=20)
    probe("ths_consensus", ths, timeout=25)
    probe("csi_constituents", csi, timeout=45)
    probe("cni_constituents", cni, timeout=45)
    probe("sina_financials", sinafin, timeout=20)

    def emnews():
        df=ak.stock_news_em(symbol="600519"); return f"{len(df)} rows"
    def clsnews():
        df=ak.stock_info_global_cls(symbol="全部"); return f"{len(df)} rows"
    def sinanews():
        df=ak.stock_info_global_sina(); return f"{len(df)} rows"
    def thsnews():
        df=ak.stock_info_global_ths(); return f"{len(df)} rows"
    probe("eastmoney_news", emnews, timeout=25)
    probe("cls_news", clsnews, timeout=20)
    probe("sina_news", sinanews, timeout=20)
    probe("ths_news", thsnews, timeout=20)

    from ashare.search import StockSearcher
    def emsearch(): 
        rows=StockSearcher().search_stocks("贵州茅台", max_results=3); return f"{len(rows)} rows"
    probe("eastmoney_search", emsearch, timeout=15)

    from api.services import WebSearchService
    ws=WebSearchService()
    def web():
        x=ws.search_report("贵州茅台 2026 半年报", limit=3); return f"{len(x['results'])} results; gaps={len(x['data_gaps'])}"
    def google():
        x=ws._search_google_news("贵州茅台", 3); return f"{len(x)} results"
    def ddg():
        x=ws._search_duckduckgo("贵州茅台", 3); return f"{len(x)} results"
    def bing():
        x=ws._search_bing_rss("贵州茅台", 3); return f"{len(x)} results"
    probe("web_search", web, timeout=35)
    probe("google_news_rss", google, timeout=20)
    probe("duckduckgo_html", ddg, timeout=20)
    probe("bing_rss", bing, timeout=20)

    if os.getenv("BRAVE_SEARCH_API_KEY"):
        probe("brave_search", lambda: f"{len(ws._search_brave('贵州茅台',3,False,'cn',os.environ['BRAVE_SEARCH_API_KEY']))} results", timeout=20)
    else:
        probe("brave_search", lambda: None, configured=False)
    if os.getenv("FINNHUB_API_KEY"):
        from ashare.monitor import NewsTracker
        from ashare.config import Config
        def fin():
            x=NewsTracker(Config())._fetch_finnhub_company_news("AAPL"); return f"{0 if x is None else len(x)} rows"
        probe("finnhub_news", fin, timeout=20)
    else:
        probe("finnhub_news", lambda: None, configured=False)

    if os.getenv("SEC_USER_AGENT") and "@" in os.getenv("SEC_USER_AGENT",""):
        from api.sec import SECClient
        def sec():
            x=SECClient().company("US.AAPL"); return f"{len(x.get('filings', []))} filings"
        probe("sec_edgar", sec, timeout=25)
    else:
        probe("sec_edgar", lambda: None, configured=False)

    out=Path("reports/financial-bot/data-source-probe-2026-10-30.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", out, "sources", len(RESULTS))

main()
