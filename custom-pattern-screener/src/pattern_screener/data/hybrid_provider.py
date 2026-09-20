"""Hybrid data provider: TDX local first, akshare fallback, Parquet cache.

Priority: 1. Cache (fresh)  2. TDX local  3. akshare online
All successful reads write back to cache.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .interfaces import DataProvider, DataUnavailableError
from .tdx_reader import TDXReader
from .online_reader import OnlineReader
from .cache import ParquetCache

logger = logging.getLogger("pattern_screener.data")


class HybridProvider(DataProvider):
    """Orchestrates local TDX + online akshare + Parquet cache."""

    def __init__(self, tdx_root: str | None = None,
                 cache_dir: str | None = None,
                 prefer_local: bool = True,
                 cache_ttl: dict | None = None):
        self.prefer_local = prefer_local
        tdx_root = tdx_root or ""
        cache_dir = cache_dir or "data/cache"
        self.cache_ttl = cache_ttl or {"daily": 1}

        self.cache = ParquetCache(cache_dir)
        self.tdx: TDXReader | None = None
        self.online = OnlineReader()

        if tdx_root:
            vipdoc = Path(tdx_root) / "vipdoc"
            if vipdoc.exists():
                self.tdx = TDXReader(tdx_root)
                logger.info("TDX found at %s", tdx_root)
            else:
                logger.warning("TDX root set but vipdoc missing: %s", vipdoc)
        else:
            logger.info("No TDX path configured, using online only")

    # ─── daily ───────────────────────────────────────────────

    def get_daily(self, symbol: str, start: str | None = None,
                  end: str | None = None, adjust: str = "qfq",
                  **kwargs) -> pd.DataFrame:
        ttl = self.cache_ttl.get("daily")
        return self._fetch("daily", symbol, start, end, ttl, adjust=adjust)

    # ─── stock list ──────────────────────────────────────────

    def get_stock_list(self) -> pd.DataFrame:
        if self.tdx and self.tdx.is_available():
            try:
                return self.tdx.get_stock_list()
            except Exception:
                pass
        return self.online.get_stock_list()

    # ─── internal ────────────────────────────────────────────

    def _fetch(self, freq_key: str, symbol: str,
               start: str | None, end: str | None,
               ttl: int | None, **kwargs) -> pd.DataFrame:
        """Generic fetch with cache → TDX → online fallback."""
        # Include adjust mode in cache key to avoid mixing 复权 data
        adjust = kwargs.get("adjust")
        if adjust and not freq_key.endswith(f"_{adjust}"):
            freq_key = f"{freq_key}_{adjust}"

        # 1. Cache (with source-vs-cache mtime check for daily data)
        if self.cache.is_fresh(symbol, freq_key, ttl):
            df = self.cache.read(symbol, freq_key)
            if df is not None and not df.empty:
                if self._source_newer_than_cache(symbol, freq_key):
                    logger.debug("Source newer, invalidating cache: %s/%s", symbol, freq_key)
                    self.cache.delete(symbol, freq_key)
                else:
                    logger.debug("Cache hit: %s/%s", symbol, freq_key)
                    return self._slice(df, start, end)

        # 2. TDX local
        if self.prefer_local and self.tdx and self.tdx.is_available():
            try:
                df = self.tdx.get_daily(symbol, adjust=kwargs.get("adjust"))
                self.cache.write(symbol, df, freq_key)
                logger.debug("TDX -> cache: %s/%s", symbol, freq_key)
                return self._slice(df, start, end)
            except Exception:
                logger.debug("TDX miss for %s/%s", symbol, freq_key)

        # 3. Online
        try:
            df = self.online.get_daily(symbol, adjust=kwargs.get("adjust"))
            self.cache.write(symbol, df, freq_key)
            logger.debug("Online -> cache: %s/%s", symbol, freq_key)
            return self._slice(df, start, end)
        except Exception as e:
            raise DataUnavailableError(
                f"All sources failed for {symbol}/{freq_key}: {e}"
            )

    def _source_newer_than_cache(self, symbol: str, freq_key: str) -> bool:
        """Check if TDX .day source file is newer than the Parquet cache.

        Returns True when the source has been updated (cache is stale).
        Only applies to daily data backed by TDX.
        """
        if not freq_key.startswith("daily") or not self.tdx:
            return False

        # Resolve market + code from symbol
        code = symbol.strip()
        if code.startswith("sh") or code.startswith("sz"):
            market = code[:2]
            code = code[2:]
        elif code.startswith("6") or code.startswith("9"):
            market = "sh"
        else:
            market = "sz"
        code = code.zfill(6)

        day_path = self.tdx.vipdoc / market / "lday" / f"{market}{code}.day"
        if not day_path.exists():
            return False

        cache_path = self.cache._key(code, freq_key)
        if not cache_path.exists():
            return True

        return day_path.stat().st_mtime > cache_path.stat().st_mtime

    @staticmethod
    def _slice(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
        if start:
            df = df.loc[df.index >= start]
        if end:
            df = df.loc[df.index <= end]
        return df
