"""
股票数据获取模块

提供统一的股票数据获取接口，支持多种数据源
"""

import threading

import pandas as pd
from typing import Any, Dict, List, Optional
from ashare.logging import get_logger
from ashare.stock_pool import (
    extract_symbol,
    get_exchange_label,
    is_hk_index,
    is_hk_stock,
    is_us_stock,
    is_valid_stock_code,
    normalize_stock_code,
)

logger = get_logger(__name__)

# 尝试导入数据源（pip 的 ashare 包可能与项目根目录 Ashare.py 冲突，用文件路径显式加载）
as_api = None
try:
    import sys
    import importlib.util
    from pathlib import Path

    project_root = Path(__file__).parent.parent.resolve()
    sys.path.insert(0, str(project_root))

    def _load_ashare_module():
        """Load project Ashare.py by path to avoid name clash with installed 'ashare' package."""
        for name, path in [
            ("ashare_data_source", project_root / "Ashare.py"),
            ("ashare_backup", project_root / "_backup" / "_old_Ashare.py"),
        ]:
            if not path.exists():
                continue
            try:
                spec = importlib.util.spec_from_file_location(name, path)
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    if hasattr(mod, "get_price"):
                        return mod
            except Exception as ex:
                logger.warning("加载 %s 失败: %s", path, ex)
        return None

    as_api = _load_ashare_module()
    if as_api is None:
        try:
            import Ashare as as_api  # noqa: fallback if path load failed
        except ImportError:
            as_api = None
except Exception as e:
    as_api = None
    logger.warning("无法加载 Ashare 数据源模块: %s", e)

if as_api is None:
    logger.warning("as_api 未就绪，将使用 akshare 作为日线数据源备选")


class DataFetcher:
    """股票数据获取器"""
    
    def __init__(self, default_count: int = 120):
        """
        初始化数据获取器
        
        Args:
            default_count: 默认获取的数据条数
        """
        self.default_count = default_count
        self.data_cache = {}
        # Actual provider provenance of the last successful fetch. The analysis
        # layer must never infer a provider from the market.
        self.source_by_code: Dict[str, str] = {}
        self.adjustment_by_code: Dict[str, str] = {}
        self.stale_by_code: Dict[str, bool] = {}
        self.data_as_of_by_code: Dict[str, str] = {}
        # Provenance of the fetch currently in flight. `_mark_source` writes it
        # without knowing the code, so it is only ever consumed immediately by
        # the same synchronous fetch (see `_take_pending_source`). It is never
        # read for another symbol: concurrent or sequential fetches of different
        # tickers must not inherit each other's provider label.
        self._last_fetch_source: Optional[str] = None
        self._last_fetch_adjustment: Optional[str] = None
        # Serialises "run the provider chain, then attribute its provenance".
        # Re-entrant so a nested fetch on the same instance cannot deadlock.
        self._fetch_provenance_lock = threading.RLock()
        logger.info(f"数据获取器已初始化，默认获取{default_count}条数据")

    def _mark_source(self, source: str, adjustment: str = "unknown") -> None:
        self._last_fetch_source = source
        self._last_fetch_adjustment = adjustment

    def _take_pending_source(self, fallback: str = "unknown") -> tuple[str, str]:
        """Consume the provenance of the fetch that just finished.

        The pending label is cleared on read so a later symbol can never inherit
        it, and an unlabelled fetch is reported as ``unknown`` instead of being
        attributed to whatever provider ran last.
        """
        source = self._last_fetch_source or fallback
        adjustment = self._last_fetch_adjustment or "unknown"
        self._last_fetch_source = None
        self._last_fetch_adjustment = None
        return source, adjustment

    def provider_info(self, code: str) -> Dict[str, str]:
        normalized = normalize_stock_code(code)
        return {
            "provider": self.source_by_code.get(normalized, "unknown"),
            "adjustment": self.adjustment_by_code.get(normalized, "unknown"),
        }

    def freshness_info(self, code: str) -> Dict[str, Any]:
        normalized = normalize_stock_code(code)
        return {
            "stale": bool(self.stale_by_code.get(normalized)),
            "data_as_of": self.data_as_of_by_code.get(normalized),
        }
    
    def _fetch_a_share_fuyao(self, code: str, count: int, frequency: str) -> Optional[pd.DataFrame]:
        """Fetch A-share daily bars from Fuyao when configured.

        Fuyao exposes daily bars only. Weekly/monthly requests are resampled
        locally so the public DataFetcher contract stays unchanged. If the
        adapter is absent, unconfigured, or fails, the caller falls back to the
        existing Sina/Tencent/akshare chain.
        """
        if frequency not in {"1d", "1w", "1M"}:
            return None
        try:
            from api.datasources.flags import fuyao_primary_a_share

            if not fuyao_primary_a_share():
                return None
            from api.datasources.fuyao_provider import get_fuyao_provider, FuyaoError

            provider = get_fuyao_provider()
            if not provider.configured:
                return None
            lookback = max(14, int(count * 2.2))
            end = pd.Timestamp.now().normalize() + pd.Timedelta(days=1)
            start = end - pd.Timedelta(days=lookback)
            payload = provider.history(
                code,
                int(start.timestamp() * 1000),
                int(end.timestamp() * 1000),
                adjust="forward",
                interval="1d",
            )
            bars = payload.get("bars") or []
            if not bars:
                return None
            frame = pd.DataFrame(
                [
                    {
                        "open": bar.get("open"),
                        "close": bar.get("close"),
                        "high": bar.get("high"),
                        "low": bar.get("low"),
                        "volume": bar.get("volume"),
                    }
                    for bar in bars
                ],
                index=pd.DatetimeIndex([pd.Timestamp(bar["date"]) for bar in bars]),
            ).astype(float)
            frame = frame.dropna()
            if frame.empty:
                return None
            if frequency == "1w":
                frame = frame.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
            elif frequency == "1M":
                frame = frame.resample("ME").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
            frame = frame.tail(count)
            if frame.empty:
                return None
            frame.index = frame.index.tz_localize(None) if getattr(frame.index, "tz", None) is not None else frame.index
            frame.index.name = ""
            frame.columns.name = None
            self._mark_source("fuyao", "qfq")
            logger.info("fuyao A-share history succeeded for %s, %d rows", code, len(frame))
            return frame
        except Exception as exc:
            logger.warning("fuyao A-share history failed for %s: %s", code, type(exc).__name__)
            return None

    def _fetch_stock_data_ifind(self, code: str, count: int, frequency: str) -> Optional[pd.DataFrame]:
        """iFinD daily bars - the product's highest-priority source.

        Only A-share and HK codes map deterministically to iFinD symbols; an
        unmapped market returns None so the existing chain keeps working.
        """
        if frequency not in {"1d", "1w", "1M"}:
            return None
        try:
            from api.datasources.flags import ifind_history_enabled, ifind_primary
            from api.datasources.ifind_provider import (
                IfindProvider,
                IfindUnavailable,
                history_quota_exhausted,
                is_history_quota_error,
            )

            if not ifind_primary() or not ifind_history_enabled():
                return None
            if history_quota_exhausted():
                logger.info(
                    "iFinD history skipped for %s: history_quota_exhausted (prefer realtime/fundamentals/fallback)",
                    code,
                )
                return None
            provider = IfindProvider()
            if not provider.is_configured:
                return None
            # A fixed window per (code, adjustment) so repeated calls in one task
            # hit the provider cache instead of re-buying the same bars. Every
            # request is billed as data cells, and per-count windows used to
            # multiply the bill by the number of different counts asked for.
            import os as _os

            window = max(120, int(_os.getenv("IFIND_HISTORY_DAYS", "420")))
            end = pd.Timestamp.now().normalize() + pd.Timedelta(days=1)
            start = end - pd.Timedelta(days=window)
            payload = provider.history(
                code,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                adjust="forward",
            )
            bars = payload.get("bars") or []
            if not bars:
                return None
            frame = pd.DataFrame(
                [
                    {
                        "open": bar.get("open"),
                        "close": bar.get("close"),
                        "high": bar.get("high"),
                        "low": bar.get("low"),
                        "volume": bar.get("volume"),
                    }
                    for bar in bars
                ],
                index=pd.DatetimeIndex([pd.Timestamp(bar["date"]) for bar in bars]),
            ).astype(float)
            frame = frame.dropna()
            if frame.empty:
                return None
            if frequency == "1w":
                frame = frame.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
            elif frequency == "1M":
                frame = frame.resample("ME").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
            frame = frame.tail(count)
            if frame.empty:
                return None
            if getattr(frame.index, "tz", None) is not None:
                frame.index = frame.index.tz_localize(None)
            frame.index.name = ""
            frame.columns.name = None
            self._mark_source("ifind", "qfq")
            logger.info("iFinD history succeeded for %s, %d rows", code, len(frame))
            return frame
        except IfindUnavailable as exc:
            if is_history_quota_error(exc.reason) or getattr(exc, "quota_exhausted", False):
                logger.info("iFinD history quota exhausted for %s: %s", code, exc.reason)
            else:
                logger.info("iFinD skipped for %s: %s", code, exc.reason)
            return None
        except Exception as exc:  # noqa: BLE001 - provider boundary
            logger.warning("iFinD history failed for %s: %s", code, type(exc).__name__)
            return None

    def _fetch_a_share(self, code: str, count: int, frequency: str) -> Optional[pd.DataFrame]:
        """A-share fetch with explicit provenance.

        Ashare.get_price itself tries Sina (unadjusted) first and Tencent qfq as
        fallback, but hides which one succeeded. We call the two paths directly
        so source and adjustment cannot be mixed under a generic "ashare" label.
        """
        if as_api is None:
            return None
        if frequency in {"1d", "1w", "1M"}:
            return self._try_ashare_paths(
                code,
                count,
                frequency,
                (("get_price_sina", "sina", "none"), ("get_price_day_tx", "tencent", "qfq")),
            )
        if frequency == "1m":
            return self._try_ashare_paths(
                code,
                count,
                frequency,
                (("get_price_min_tx", "tencent", "none"),),
            )
        return self._try_ashare_paths(
            code,
            count,
            frequency,
            (("get_price_sina", "sina", "none"), ("get_price_min_tx", "tencent", "none")),
        )

    def _try_ashare_paths(self, code, count, frequency, paths) -> Optional[pd.DataFrame]:
        from api.datasources import health
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

        # Sina/Tencent can hang without a socket timeout; keep /bot analysis moving
        # when iFinD history is skipped and Fuyao is down.
        call_timeout = 8.0

        for function_name, source, adjustment in paths:
            function = getattr(as_api, function_name, None)
            if not callable(function):
                continue
            if not health.is_available(source, "a_share_history"):
                logger.info("Skipping %s for %s: circuit open", source, code)
                continue
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                fut = pool.submit(function, code, end_date="", count=count, frequency=frequency)
                frame = fut.result(timeout=call_timeout)
            except FuturesTimeout:
                health.record_failure(source, "a_share_history", "timeout")
                logger.warning("Ashare.%s timed out for %s after %.0fs", function_name, code, call_timeout)
                continue
            except Exception as exc:
                health.record_failure(source, "a_share_history", type(exc).__name__)
                logger.warning("Ashare.%s failed for %s: %s", function_name, code, exc)
                continue
            finally:
                # Do not wait for a hung Sina/Tencent socket — that blocked analysis for minutes.
                pool.shutdown(wait=False, cancel_futures=True)
            if frame is not None and not frame.empty:
                health.record_success(source, "a_share_history")
                self._mark_source(source, adjustment)
                return frame
            health.record_failure(source, "a_share_history", "empty")
        # Compatibility with older/fake Ashare modules that only expose get_price.
        get_price = getattr(as_api, "get_price", None)
        if callable(get_price):
            if not health.is_available("ashare", "a_share_history"):
                return None
            try:
                frame = get_price(code, count=count, frequency=frequency)
            except Exception as exc:
                health.record_failure("ashare", "a_share_history", type(exc).__name__)
                logger.warning("Ashare.get_price failed for %s: %s", code, exc)
                return None
            if frame is not None and not frame.empty:
                health.record_success("ashare", "a_share_history")
                self._mark_source("ashare", "unknown")
                return frame
            health.record_failure("ashare", "a_share_history", "empty")
        return None

    def _remember_last_good(
        self,
        code: str,
        frequency: str,
        frame: pd.DataFrame,
        *,
        source: Optional[str] = None,
        adjustment: Optional[str] = None,
    ) -> None:
        try:
            from api.datasources.last_good import put

            bars = []
            for index, row in frame.tail(2000).iterrows():
                bars.append({
                    "date": str(index)[:10],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                })
            put(
                "price_history",
                f"{code}:{frequency}",
                {"bars": bars},
                provider=source or "unknown",
                adjustment=adjustment or "unknown",
                data_as_of=bars[-1]["date"] if bars else None,
            )
        except Exception:
            logger.debug("last-good cache write skipped for %s", code, exc_info=True)

    def _restore_last_good(self, code: str, frequency: str, count: int) -> Optional[pd.DataFrame]:
        try:
            from api.datasources.last_good import get

            cached = get("price_history", f"{code}:{frequency}")
            if not cached or not isinstance(cached.get("value"), dict):
                return None
            bars = cached["value"].get("bars") or []
            if not bars:
                return None
            frame = pd.DataFrame(
                [
                    {
                        "open": bar.get("open"),
                        "close": bar.get("close"),
                        "high": bar.get("high"),
                        "low": bar.get("low"),
                        "volume": bar.get("volume"),
                    }
                    for bar in bars
                ],
                index=pd.DatetimeIndex([pd.Timestamp(bar["date"]) for bar in bars]),
            ).astype(float).tail(count)
            if frame.empty:
                return None
            frame.index.name = ""
            frame.columns.name = None
            self.source_by_code[code] = f"last_good_cache({cached.get('provider') or 'unknown'})"
            self.adjustment_by_code[code] = cached.get("adjustment") or "unknown"
            self.data_as_of_by_code[code] = str(cached.get("data_as_of") or "")
            self.stale_by_code[code] = True
            logger.warning(
                "Using stale last-good price for %s (%s, as_of=%s)",
                code, cached.get("provider"), cached.get("data_as_of"),
            )
            return frame
        except Exception:
            logger.debug("last-good cache read skipped for %s", code, exc_info=True)
            return None

    def _fetch_via_provider_chain(self, code: str, count: int, frequency: str) -> Optional[pd.DataFrame]:
        """Run the per-market provider chain and return its raw frame.

        Callers must hold ``_fetch_provenance_lock`` so the provider label this
        chain writes is attributed to this symbol only.
        """
        if is_hk_index(code):
            df = self._fetch_stock_data_hk_index(code, count, frequency)
        elif is_hk_stock(code):
            df = self._fetch_stock_data_ifind(code, count, frequency)
            if df is None or (isinstance(df, pd.DataFrame) and df.empty):
                df = self._fetch_stock_data_hk(code, count, frequency)
        elif is_us_stock(code):
            # 美股走独立链路：yfinance 主源，Finnhub 备选
            df = self._fetch_stock_data_us(code, count, frequency)
        else:
            df = self._fetch_stock_data_ifind(code, count, frequency)
            if df is None or (isinstance(df, pd.DataFrame) and df.empty):
                df = self._fetch_a_share_fuyao(code, count, frequency)
            # When iFinD 历史数据 is exhausted, Sina/Tencent often stall; prefer
            # akshare/yfinance before the legacy Ashare HTTP paths.
            prefer_alt = False
            try:
                from api.datasources.flags import ifind_history_enabled
                from api.datasources.ifind_provider import history_quota_exhausted
                prefer_alt = (not ifind_history_enabled()) or history_quota_exhausted()
            except Exception:
                prefer_alt = False
            if prefer_alt:
                if (df is None or (isinstance(df, pd.DataFrame) and df.empty)) and frequency == "1d":
                    df = self._fetch_stock_data_akshare(code, count)
                if (df is None or (isinstance(df, pd.DataFrame) and df.empty)) and frequency == "1d":
                    try:
                        from api.datasources.flags import yfinance_a_share_fallback
                        if yfinance_a_share_fallback():
                            df = self._fetch_stock_data_yfinance_adapter(code, count, frequency, auto_adjust=False)
                    except Exception:
                        logger.debug("A-share yfinance fallback failed for %s", code, exc_info=True)
            if df is None or (isinstance(df, pd.DataFrame) and df.empty):
                df = self._fetch_a_share(code, count, frequency)
            if (df is None or (isinstance(df, pd.DataFrame) and df.empty)) and frequency == "1d" and not prefer_alt:
                df = self._fetch_stock_data_akshare(code, count)
            if (df is None or (isinstance(df, pd.DataFrame) and df.empty)) and frequency == "1d" and not prefer_alt:
                try:
                    from api.datasources.flags import yfinance_a_share_fallback

                    if yfinance_a_share_fallback():
                        df = self._fetch_stock_data_yfinance_adapter(code, count, frequency, auto_adjust=False)
                except Exception:
                    logger.debug("A-share yfinance fallback failed for %s", code, exc_info=True)
        return df

    def fetch_stock_data(self, code: str, count: Optional[int] = None, 
                        frequency: str = '1d') -> Optional[pd.DataFrame]:
        """
        获取单只股票数据
        
        Args:
            code: 股票代码
            count: 获取的数据条数
            frequency: 数据频率 ('1d', '1w', '1m' 等)
            
        Returns:
            股票数据DataFrame，失败时返回None
        """
        count = count or self.default_count
        code = normalize_stock_code(code)
        cache_key = f"{code}_{count}_{frequency}"
        
        # 检查缓存
        if cache_key in self.data_cache:
            logger.debug(f"从缓存获取股票数据: {code}")
            return self.data_cache[cache_key]
        
        try:
            logger.info(f"正在获取股票 {code} 的数据...")
            df = None
            with self._fetch_provenance_lock:
                self._last_fetch_source = None
                self._last_fetch_adjustment = None
                df = self._fetch_via_provider_chain(code, count, frequency)
                source, adjustment = self._take_pending_source()
            # 检查数据是否有效
            if df is None or df.empty:
                logger.warning(f"股票 {code} 返回空数据")
                stale = self._restore_last_good(code, frequency, count)
                if stale is not None:
                    self.data_cache[cache_key] = stale
                    return stale
                self._log_stock_code_help(code)
                return None
            self.source_by_code[code] = source
            self.adjustment_by_code[code] = adjustment
            self.stale_by_code[code] = False
            self.data_as_of_by_code[code] = str(df.index[-1])[:10] if len(df.index) else ""
            self._remember_last_good(code, frequency, df, source=source, adjustment=adjustment)
            logger.info(f"成功获取 {code} 数据，共 {len(df)} 条记录")
            logger.info(f"数据时间范围: {df.index[0]} 到 {df.index[-1]}")
            
            # 缓存数据
            self.data_cache[cache_key] = df
            return df
            
        except Exception as e:
            logger.error(f"获取股票 {code} 数据失败: {str(e)}")
            stale = self._restore_last_good(code, frequency, count)
            if stale is not None:
                self.data_cache[cache_key] = stale
                return stale
            self._log_stock_code_help(code)
            return None
    
    def fetch_multiple_stocks(self, codes: List[str], count: Optional[int] = None,
                             frequency: str = '1d') -> Dict[str, pd.DataFrame]:
        """
        批量获取多只股票数据
        
        Args:
            codes: 股票代码列表
            count: 获取的数据条数
            frequency: 数据频率
            
        Returns:
            股票代码到DataFrame的映射
        """
        logger.info(f"开始批量获取{len(codes)}只股票的数据")
        result = {}
        
        for code in codes:
            df = self.fetch_stock_data(code, count, frequency)
            if df is not None:
                result[code] = df
        
        logger.info(f"成功获取{len(result)}只股票的数据")
        return result
    
    def get_stock_info(self, code: str) -> Dict[str, str]:
        """
        获取股票基本信息
        
        Args:
            code: 股票代码
            
        Returns:
            股票基本信息字典
        """
        normalized = normalize_stock_code(code)
        try:
            return {
                'code': normalized,
                'exchange': get_exchange_label(normalized),
                'status': 'active'  # 可以扩展获取实际状态
            }
        except Exception as e:
            logger.error(f"获取股票 {normalized} 信息失败: {str(e)}")
            return {'code': normalized, 'exchange': 'unknown', 'status': 'unknown'}
    
    def validate_stock_code(self, code: str) -> bool:
        """
        验证股票代码格式
        
        Args:
            code: 股票代码
            
        Returns:
            是否为有效格式
        """
        if not code:
            return False
        
        normalized = normalize_stock_code(code)
        if is_valid_stock_code(normalized):
            return True

        logger.warning(f"股票代码格式可能不正确: {normalized}")
        return False
    
    def clear_cache(self):
        """清空数据缓存"""
        self.data_cache.clear()
        logger.info("数据缓存已清空")
    
    def get_cache_info(self) -> Dict[str, int]:
        """获取缓存信息"""
        return {
            'cached_items': len(self.data_cache),
            'cache_keys': list(self.data_cache.keys())
        }
    
    # ------------------------------------------------------------------
    # 美股数据源
    # ------------------------------------------------------------------
    # yfinance 频率映射
    _YF_INTERVAL = {
        "1d": "1d", "1w": "1wk", "1M": "1mo",
        "60m": "60m", "30m": "30m", "15m": "15m", "5m": "5m", "1m": "1m",
    }
    # Finnhub 频率映射（resolution）
    _FINNHUB_RESOLUTION = {
        "1d": "D", "1w": "W", "1M": "M",
        "60m": "60", "30m": "30", "15m": "15", "5m": "5", "1m": "1",
    }
    # 估算需要回溯的自然日数（按频率把 count 转成日历跨度，留足非交易日冗余）
    _LOOKBACK_DAYS_PER_BAR = {
        "1d": 2, "1w": 9, "1M": 40,
        "60m": 1, "30m": 1, "15m": 1, "5m": 1, "1m": 1,
    }

    @staticmethod
    def _us_yf_symbol(code: str) -> str:
        """US.BRK.B -> BRK-B（yfinance 用 ``-`` 表示类别股）。"""
        return extract_symbol(code).replace(".", "-")

    def _fetch_stock_data_us(self, code: str, count: int, frequency: str) -> Optional[pd.DataFrame]:
        """美股行情：yfinance 主源，失败时降级到 Finnhub。"""
        df = self._fetch_stock_data_yfinance(code, count, frequency)
        if df is not None and not df.empty:
            return df
        logger.info("yfinance 未取到 %s 数据，尝试 Finnhub 备选源", code)
        return self._fetch_stock_data_finnhub(code, count, frequency)

    def _fetch_stock_data_hk_index(self, code: str, count: int, frequency: str) -> Optional[pd.DataFrame]:
        """恒生指数：使用 Yahoo Finance 的指数代码，不走美股 Finnhub 兜底。"""
        return self._fetch_stock_data_yfinance_symbol("^HSI", count, frequency)

    def _fetch_stock_data_hk(self, code: str, count: int, frequency: str) -> Optional[pd.DataFrame]:
        """Fetch Hong Kong equity history from Yahoo Finance using its HKEX symbol format."""
        symbol = extract_symbol(code)
        yahoo_symbol = f"{symbol[1:] if symbol.startswith('0') else symbol}.HK"
        return self._fetch_stock_data_yfinance_symbol(yahoo_symbol, count, frequency)

    def _fetch_stock_data_yfinance(self, code: str, count: int, frequency: str) -> Optional[pd.DataFrame]:
        return self._fetch_stock_data_yfinance_symbol(self._us_yf_symbol(code), count, frequency)

    def _fetch_stock_data_yfinance_symbol(self, symbol: str, count: int, frequency: str) -> Optional[pd.DataFrame]:
        # Phase 3b: adapter first for US/HK history. The old yf.download path
        # remains as a fallback, so an adapter change cannot take quotes offline.
        # Yahoo/network stalls must not leave /work research panel spinning forever.
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

        call_timeout = 10.0

        def _call_adapter():
            from api.datasources.flags import yfinance_primary_us_hk

            if not yfinance_primary_us_hk():
                return None
            return self._fetch_stock_data_yfinance_adapter(symbol, count, frequency)

        try:
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                adapted = pool.submit(_call_adapter).result(timeout=call_timeout)
            except FuturesTimeout:
                logger.warning("yfinance adapter timed out for %s after %.0fs", symbol, call_timeout)
                adapted = None
            except Exception:
                logger.debug("yfinance adapter path failed for %s; falling back", symbol, exc_info=True)
                adapted = None
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            if adapted is not None:
                return adapted
        except Exception:
            logger.debug("yfinance adapter path failed for %s; falling back", symbol, exc_info=True)

        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance 未安装，无法获取行情数据")
            return None

        interval = self._YF_INTERVAL.get(frequency)
        if interval is None:
            logger.warning("yfinance 不支持的频率: %s", frequency)
            return None

        lookback = max(7, count * self._LOOKBACK_DAYS_PER_BAR.get(frequency, 2))
        start = (pd.Timestamp.now() - pd.Timedelta(days=lookback)).strftime("%Y-%m-%d")
        end = (pd.Timestamp.now() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

        def _download():
            return yf.download(
                symbol, start=start, end=end, interval=interval,
                auto_adjust=True, progress=False, threads=False,
            )

        try:
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                raw = pool.submit(_download).result(timeout=call_timeout)
            except FuturesTimeout:
                logger.warning("yfinance download timed out for %s after %.0fs", symbol, call_timeout)
                return None
            except Exception as e:
                logger.warning("yfinance download failed %s: %s", symbol, e)
                return None
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        except Exception as e:
            logger.warning("yfinance download failed %s: %s", symbol, e)
            return None
        return self._normalize_us_dataframe(raw, count, source=f"yfinance({symbol})")

    def _fetch_stock_data_yfinance_adapter(self, symbol: str, count: int, frequency: str, auto_adjust: bool = True) -> Optional[pd.DataFrame]:
        from api.datasources.yfinance_provider import get_yfinance_provider, to_yahoo_symbol

        canonical_symbol = to_yahoo_symbol(symbol)
        interval = self._YF_INTERVAL.get(frequency)
        if interval is None:
            return None
        lookback = max(7, count * self._LOOKBACK_DAYS_PER_BAR.get(frequency, 2))
        start = (pd.Timestamp.now() - pd.Timedelta(days=lookback)).strftime("%Y-%m-%d")
        end = (pd.Timestamp.now() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        from api.datasources import health

        if not health.is_available("yfinance", "a_share_history"):
            return None
        try:
            data = get_yfinance_provider().history(canonical_symbol, interval=interval, start=start, end=end, auto_adjust=auto_adjust)
        except Exception:
            health.record_failure("yfinance", "a_share_history", "adapter_error")
            raise
        bars = data.get("bars") or []
        if not bars:
            return None
        frame = pd.DataFrame(
            [
                {
                    "open": bar.get("open"),
                    "close": bar.get("close"),
                    "high": bar.get("high"),
                    "low": bar.get("low"),
                    "volume": bar.get("volume"),
                }
                for bar in bars
            ],
            index=pd.DatetimeIndex([
                pd.Timestamp(bar.get("date")).tz_localize(None)
                if pd.Timestamp(bar.get("date")).tzinfo is not None
                else pd.Timestamp(bar.get("date"))
                for bar in bars
            ]),
        ).astype(float)
        frame = frame.dropna().tail(count)
        if frame.empty:
            return None
        frame.index.name = ""
        frame.columns.name = None
        health.record_success("yfinance", "a_share_history")
        self._mark_source(f"yfinance_adapter({canonical_symbol})", "auto_adjust" if auto_adjust else "none")
        logger.info("yfinance_adapter(%s) 成功，共 %d 条", canonical_symbol, len(frame))
        return frame

    def _normalize_us_dataframe(self, raw, count: int, source: str) -> Optional[pd.DataFrame]:
        if raw is None or raw.empty:
            return None
        df = raw.copy()
        # yfinance 多 ticker 时返回 MultiIndex 列，单 ticker 也可能带一层，统一压平
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        col_map = {"Open": "open", "Close": "close", "High": "high", "Low": "low", "Volume": "volume"}
        rename = {k: v for k, v in col_map.items() if k in df.columns}
        df = df.rename(columns=rename)
        for c in ["open", "close", "high", "low", "volume"]:
            if c not in df.columns:
                logger.warning("%s 缺少必要列 %s", source, c)
                return None
        df = df[["open", "close", "high", "low", "volume"]].astype(float)
        df.index = pd.to_datetime(df.index)
        df.index.name = ""
        df.columns.name = None
        df = df.dropna().tail(count)
        if df.empty:
            return None
        self._mark_source(source, "auto_adjust")
        logger.info("%s 成功，共 %d 条", source, len(df))
        return df

    def _fetch_stock_data_finnhub(self, code: str, count: int, frequency: str) -> Optional[pd.DataFrame]:
        try:
            from ashare.config import Config
        except ImportError:
            Config = None
        api_key = None
        if Config is not None:
            try:
                api_key = Config().finnhub_api_key
            except Exception:
                api_key = None
        if not api_key:
            logger.info("未配置 FINNHUB_API_KEY，跳过 Finnhub 备选源")
            return None

        resolution = self._FINNHUB_RESOLUTION.get(frequency)
        if resolution is None:
            logger.warning("Finnhub 不支持的频率: %s", frequency)
            return None

        symbol = extract_symbol(code).replace(".", "-")
        lookback = max(7, count * self._LOOKBACK_DAYS_PER_BAR.get(frequency, 2))
        to_ts = int(pd.Timestamp.now().timestamp())
        from_ts = int((pd.Timestamp.now() - pd.Timedelta(days=lookback)).timestamp())
        try:
            import requests
            resp = requests.get(
                "https://finnhub.io/api/v1/stock/candle",
                params={"symbol": symbol, "resolution": resolution,
                        "from": from_ts, "to": to_ts, "token": api_key},
                timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            logger.warning("Finnhub request failed %s: %s", symbol, e)
            return None

        if not isinstance(payload, dict) or payload.get("s") != "ok":
            logger.warning("Finnhub 返回无效数据 %s: status=%s", symbol, payload.get("s") if isinstance(payload, dict) else payload)
            return None
        try:
            df = pd.DataFrame({
                "open": payload["o"], "close": payload["c"],
                "high": payload["h"], "low": payload["l"],
                "volume": payload["v"],
            }, index=pd.to_datetime(payload["t"], unit="s")).astype(float)
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("Finnhub 数据解析失败 %s: %s", symbol, e)
            return None
        df.index.name = ""
        df = df.dropna().tail(count)
        if df.empty:
            return None
        self._mark_source("finnhub", "unknown")
        logger.info("Finnhub(%s) 成功，共 %d 条", symbol, len(df))
        return df

    def _fetch_stock_data_akshare(self, code: str, count: int) -> Optional[pd.DataFrame]:
        """
        akshare 日线备选：当 Ashare.get_price 不可用时使用。
        symbol 为 6 位数字，不含 sh/sz 前缀。
        """
        try:
            import akshare as ak
        except ImportError:
            return None
        code = normalize_stock_code(code).lower()
        if not (code.startswith("sh") or code.startswith("sz")) or len(code) < 8:
            return None
        symbol = code[2:8]  # 600036
        from api.datasources import health

        if not health.is_available("akshare", "a_share_history"):
            return None
        try:
            # 多取一些再 tail，避免节假日导致条数不足
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
            end = pd.Timestamp.now().strftime("%Y%m%d")
            start = (pd.Timestamp.now() - pd.Timedelta(days=count * 3)).strftime("%Y%m%d")
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                fut = pool.submit(
                    ak.stock_zh_a_hist,
                    symbol=symbol,
                    period="daily",
                    start_date=start,
                    end_date=end,
                    adjust="qfq",
                )
                df = fut.result(timeout=15.0)
            except FuturesTimeout as e:
                health.record_failure("akshare", "a_share_history", "timeout")
                logger.warning("akshare stock_zh_a_hist timed out for %s", code)
                return None
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        except Exception as e:
            health.record_failure("akshare", "a_share_history", type(e).__name__)
            logger.warning("akshare stock_zh_a_hist failed %s: %s", code, e)
            return None
        if df is None or df.empty:
            return None
        # akshare 列名：日期 开盘 收盘 最高 最低 成交量 ...
        col_map = {
            "日期": "time",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
        }
        rename = {k: v for k, v in col_map.items() if k in df.columns}
        if not rename:
            return None
        df = df.rename(columns=rename)
        for c in ["open", "close", "high", "low", "volume"]:
            if c not in df.columns:
                return None
        time_col = "time" if "time" in df.columns else df.columns[0]
        df[time_col] = pd.to_datetime(df[time_col])
        df = df.set_index(time_col)
        df.index.name = ""
        df = df[["open", "close", "high", "low", "volume"]].astype(float)
        df = df.tail(count)
        if df.empty:
            return None
        health.record_success("akshare", "a_share_history")
        self._mark_source("akshare", "qfq")
        logger.info(f"akshare 备选成功 {code} 共 {len(df)} 条")
        return df

    def _log_stock_code_help(self, code: str):
        """记录股票代码格式帮助信息"""
        logger.info("请检查股票代码格式是否正确。常见格式:")
        logger.info("  - 上交所: sh000001 (上证指数), sh600036 (招商银行)")
        logger.info("  - 深交所: sz399001 (深证成指), sz000001 (平安银行)")
        logger.info("  - 港股: 00700.HK (腾讯控股)")
        logger.info("建议检查:")
        logger.info("  1. 股票代码格式是否正确")
        logger.info("  2. 网络连接是否正常")
        logger.info("  3. 股票是否已停牌或退市")
    
    def fetch_multi_timeframe_data(self, code: str, count: Optional[int] = None) -> Dict[str, pd.DataFrame]:
        """
        获取多个时间框架的股票数据
        
        Args:
            code: 股票代码  
            count: 获取的数据条数
            
        Returns:
            包含不同时间框架数据的字典
        """
        timeframes = {
            '日线': '1d',
            '周线': '1w', 
            '月线': '1M'
        }
        
        results = {}
        for name, freq in timeframes.items():
            try:
                df = self.fetch_stock_data(code, count, freq)
                if df is not None:
                    results[name] = df
                    logger.info(f"{code} {name}数据获取成功，共{len(df)}条记录")
                else:
                    logger.warning(f"{code} {name}数据获取失败")
            except Exception as e:
                logger.error(f"获取{code} {name}数据失败: {str(e)}")
                
        return results 
