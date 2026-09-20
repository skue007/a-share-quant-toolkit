"""数据层子包 — 提供统一的 A 股行情数据访问。

数据源优先级: Parquet 缓存 → 本地通达信 (mootdx) → akshare 在线。
"""

from .hybrid_provider import HybridProvider, DataUnavailableError
from .interfaces import DataProvider

__all__ = ["HybridProvider", "DataProvider", "DataUnavailableError"]
