"""TDX .day 文件向量化读取工具（精简版）。

从原量化系统 data_utils.py 中提取联动选股所需的核心能力：
- read_tdx_day_fast: 一条 numpy.frombuffer 替代逐条 struct.unpack
- is_a_share: 标准 A 股代码判断

TDX .day 文件格式 (32 bytes/条, little-endian):
    date(u4 YYYYMMDD) open(u4*100) high(u4*100) low(u4*100)
    close(u4*100) amount(u4) volume(u4) reserved(u4)
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd

# ── TDX .day 文件格式 (32 bytes, little-endian) ──────────────
TDX_DAY_DTYPE = np.dtype([
    ("date",     "<u4"),  # uint32: YYYYMMDD
    ("open",     "<u4"),  # uint32: open * 100
    ("high",     "<u4"),  # uint32: high * 100
    ("low",      "<u4"),  # uint32: low * 100
    ("close",    "<u4"),  # uint32: close * 100
    ("amount",   "<u4"),  # uint32: 成交额
    ("volume",   "<u4"),  # uint32: 成交量
    ("reserved", "<u4"),  # uint32: 保留
])

# 标准 A 股前缀（排除 ETF/可转债/B股/基金）
A_SHARE_PREFIXES = [
    "sh600", "sh601", "sh603", "sh605", "sh688",
    "sz000", "sz001", "sz002", "sz003", "sz300", "sz301",
    "bj43", "bj83", "bj87", "bj92",
]


def is_a_share(code: str) -> bool:
    """判断是否为标准 A 股（排除 ETF、可转债、B 股、基金）。"""
    for pfx in A_SHARE_PREFIXES:
        if code.startswith(pfx):
            return True
    return False


def read_tdx_day_fast(filepath: Union[str, Path]) -> pd.DataFrame:
    """用 numpy 向量化读取 TDX .day 文件，比逐条 struct.unpack 快 10-50x。

    Args:
        filepath: .day 文件路径

    Returns:
        DataFrame with columns: date, open, high, low, close, amount, volume
    """
    filepath = Path(filepath)
    if not filepath.exists():
        return pd.DataFrame()

    raw = filepath.read_bytes()
    if len(raw) < 32:
        return pd.DataFrame()

    data = np.frombuffer(raw, dtype=TDX_DAY_DTYPE)
    mask = data["date"] > 0
    data = data[mask]

    if len(data) == 0:
        return pd.DataFrame()

    df = pd.DataFrame({
        "date": pd.to_datetime(data["date"].astype(str), format="%Y%m%d"),
        "open": data["open"] / 100.0,
        "high": data["high"] / 100.0,
        "low": data["low"] / 100.0,
        "close": data["close"] / 100.0,
        "amount": data["amount"].astype(np.int64),
        "volume": data["volume"].astype(np.int64),
    })
    return df.sort_values("date").reset_index(drop=True)
