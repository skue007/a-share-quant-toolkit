"""Online data reader via akshare.

Fetches A-share daily K-line and stock list from free public endpoints.
Used as fallback when local TDX data is unavailable.
"""

from __future__ import annotations

import logging

import pandas as pd

from .interfaces import DataProvider
from .normalizer import normalize_daily

logger = logging.getLogger("pattern_screener.online")


class OnlineReader(DataProvider):
    """Fetches market data from akshare."""

    # ─── daily ───────────────────────────────────────────────

    def get_daily(self, symbol: str, start: str | None = None,
                  end: str | None = None, adjust: str | None = None,
                  **kwargs) -> pd.DataFrame:
        code = symbol.strip().zfill(6)
        try:
            import akshare as ak

            start_date = start or "19900101"
            end_date = end or pd.Timestamp.now().strftime("%Y%m%d")

            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust=adjust or "qfq",
            )
            if df is None or df.empty:
                raise ValueError(f"No online daily data for {code}")

            df = normalize_daily(df, source="akshare")
            logger.debug("akshare daily hit: %s (%d rows)", symbol, len(df))
            return df
        except Exception as e:
            logger.warning("akshare daily failed for %s: %s", symbol, e)
            raise

    # ─── minute ──────────────────────────────────────────────

    def get_minute(self, symbol: str, freq: str = "5",
                   start: str | None = None,
                   end: str | None = None) -> pd.DataFrame:
        raise NotImplementedError(
            "Minute data not needed by the pattern screener."
        )

    # ─── stock list ──────────────────────────────────────────

    def get_stock_list(self) -> pd.DataFrame:
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            df = df.rename(columns={
                "代码": "code",
                "名称": "name",
            })
            df["market"] = df["code"].apply(
                lambda x: "SH" if str(x).zfill(6)[0] in ("6", "9") else "SZ"
            )
            return df[["code", "name", "market"]]
        except Exception as e:
            logger.warning("akshare stock list fetch failed: %s", e)
            raise
