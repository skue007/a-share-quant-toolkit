"""TDX local data reader via mootdx.

Reads daily (.day) and minute K-line data from local TDX vipdoc directory.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .interfaces import DataProvider
from .normalizer import normalize_daily

logger = logging.getLogger("pattern_screener.tdx")


class TDXReader(DataProvider):
    """Reads daily and minute K-line data from local TDX vipdoc directory."""

    def __init__(self, tdx_root: str):
        self.tdx_root = Path(tdx_root)
        self.vipdoc = self.tdx_root / "vipdoc"
        self._reader = None
        self._init_reader()
        self._minute_avail_cache: dict[str, bool] = {}

    def _init_reader(self):
        if not self.vipdoc.exists():
            logger.warning("TDX vipdoc not found at %s", self.vipdoc)
            return
        try:
            from mootdx.reader import Reader
            self._reader = Reader.factory(market="std", tdxdir=str(self.tdx_root))
            logger.info("TDXReader initialized: %s", self.tdx_root)
        except Exception as e:
            logger.error("Failed to init TDXReader: %s", e)
            self._reader = None

    def is_available(self) -> bool:
        return self._reader is not None

    @staticmethod
    def _normalize_code(symbol: str) -> str:
        return symbol.strip().zfill(6)

    @staticmethod
    def _market(symbol: str) -> str:
        return "sh" if symbol[0] in ("6", "9") else "sz"

    # ─── daily ───────────────────────────────────────────────

    def get_daily(self, symbol: str, start: str | None = None,
                  end: str | None = None, adjust: str | None = None,
                  **kwargs) -> pd.DataFrame:
        code = self._normalize_code(symbol)

        try:
            df = self._reader.daily(symbol=code)
            if df is None or df.empty:
                raise ValueError(f"No TDX daily data for {code}")

            df = normalize_daily(df, source="tdx")

            if adjust:
                df = self._apply_adjust(df, code, adjust)

            return self._slice(df, start, end)
        except Exception as e:
            logger.warning("TDX daily failed for %s: %s", symbol, e)
            raise

    @staticmethod
    def _apply_adjust(df: pd.DataFrame, code: str, adjust: str) -> pd.DataFrame:
        """Apply 复权 (adjustment) to raw data using Sina factors via mootdx."""
        try:
            from mootdx.tools.reversion import fq_factor

            factor = fq_factor(code, adjust)
            if factor is None or factor.empty:
                logger.warning("No 复权 factor for %s, returning raw data", code)
                return df

            factor = factor.sort_index(ascending=True).astype(float)
            df = df.sort_index(ascending=True)

            data = pd.concat(
                [df, factor.loc[df.index[0]: df.index[-1], ["factor"]]], axis=1
            )
            data["factor"] = data["factor"].ffill()
            data["factor"] = data["factor"].fillna(1.0).astype(float)

            if adjust == "qfq":
                for col in ["open", "high", "low", "close"]:
                    data[col] = data[col] / data["factor"]
            elif adjust == "hfq":
                for col in ["open", "high", "low", "close"]:
                    data[col] = data[col] * data["factor"]

            return data.drop(columns=["factor"])
        except Exception as e:
            logger.warning("复权 failed for %s (%s): %s, returning raw data", code, adjust, e)
            return df

    # ─── minute ──────────────────────────────────────────────

    def get_minute(self, symbol: str, freq: str = "5",
                   start: str | None = None,
                   end: str | None = None) -> pd.DataFrame:
        raise NotImplementedError(
            "Minute data not needed by the pattern screener; "
            "implement via mootdx if required."
        )

    # ─── stock list ──────────────────────────────────────────

    def get_stock_list(self) -> pd.DataFrame:
        if not self.is_available():
            return pd.DataFrame()

        try:
            from mootdx.reader import Reader
            reader = Reader.factory(market="std", tdxdir=str(self.tdx_root))
            blocks = reader.block(symbol="block_zs", group=True)
            if blocks is not None and not blocks.empty:
                blocks = blocks.rename(columns={
                    "short_name": "name",
                    "code": "code",
                })
                blocks["market"] = blocks["code"].apply(
                    lambda x: "SH" if str(x).zfill(6)[0] in ("6", "9") else "SZ"
                )
                return blocks[["code", "name", "market"]]
        except Exception as e:
            logger.warning("TDX block read failed: %s", e)

        return self._scan_files()

    def _scan_files(self) -> pd.DataFrame:
        rows = []
        for market_dir, market_name in [("sh", "SH"), ("sz", "SZ")]:
            lday = self.vipdoc / market_dir / "lday"
            if not lday.exists():
                continue
            for f in lday.glob(f"{market_dir}*.day"):
                code = f.stem[2:]
                rows.append({"code": code, "name": "", "market": market_name})
        return pd.DataFrame(rows)

    # ─── helpers ─────────────────────────────────────────────

    @staticmethod
    def _slice(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
        if start:
            df = df.loc[df.index >= start]
        if end:
            df = df.loc[df.index <= end]
        return df
