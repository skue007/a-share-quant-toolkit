from abc import ABC, abstractmethod

import pandas as pd


class DataProvider(ABC):
    """Unified interface for all data providers."""

    @abstractmethod
    def get_daily(self, symbol: str, start: str | None = None,
                  end: str | None = None, adjust: str | None = None,
                  **kwargs) -> pd.DataFrame:
        """Return standardized daily OHLCV DataFrame.

        Columns: open, high, low, close, volume, amount
        Index: DatetimeIndex (daily)
        """
        ...

    @abstractmethod
    def get_stock_list(self) -> pd.DataFrame:
        """Return all A-share codes with name and market.

        Columns: code, name, market (SH/SZ)
        """
        ...

    def get_minute(self, symbol: str, freq: str = "5",
                   start: str | None = None,
                   end: str | None = None) -> pd.DataFrame:
        """Return standardized minute K-line DataFrame. (Not used by screener)"""
        raise NotImplementedError

    def get_spot(self) -> pd.DataFrame:
        raise NotImplementedError

    def get_spot_single(self, symbol: str) -> dict:
        raise NotImplementedError

    def is_available(self) -> bool:
        return True


class DataUnavailableError(Exception):
    """Raised when all data sources fail for a symbol."""
    pass
