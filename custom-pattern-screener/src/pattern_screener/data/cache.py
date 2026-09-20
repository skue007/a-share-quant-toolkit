"""Parquet-based local cache with TTL freshness check."""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd


class ParquetCache:
    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)

    def _key(self, symbol: str, freq: str = "daily") -> Path:
        """Cache file path for a symbol and frequency."""
        market = "sh" if symbol.startswith(("6", "9")) else "sz"
        subdir = self.root_dir / freq
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / f"{market}{symbol}.parquet"

    def read(self, symbol: str, freq: str = "daily") -> pd.DataFrame | None:
        path = self._key(symbol, freq)
        if path.exists():
            return pd.read_parquet(path)
        return None

    def write(self, symbol: str, df: pd.DataFrame, freq: str = "daily") -> None:
        path = self._key(symbol, freq)
        df.to_parquet(path, index=True)

    def update(self, symbol: str, new_df: pd.DataFrame, freq: str = "daily") -> pd.DataFrame:
        """Merge new data with existing, deduplicate by date index."""
        existing = self.read(symbol, freq)
        if existing is not None and not existing.empty:
            combined = pd.concat([existing, new_df])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined.sort_index(inplace=True)
        else:
            combined = new_df
        self.write(symbol, combined, freq)
        return combined

    def is_fresh(self, symbol: str, freq: str = "daily", ttl_days: int | None = None) -> bool:
        """Check if cache file is within TTL. None TTL means never expire."""
        if ttl_days is None:
            return self._key(symbol, freq).exists()
        path = self._key(symbol, freq)
        if not path.exists():
            return False
        age_seconds = time.time() - path.stat().st_mtime
        return age_seconds < (ttl_days * 86400)

    def delete(self, symbol: str, freq: str = "daily") -> None:
        path = self._key(symbol, freq)
        if path.exists():
            path.unlink()
