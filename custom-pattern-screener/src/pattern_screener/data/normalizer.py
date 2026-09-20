"""Column name and type normalization for OHLCV data."""

import pandas as pd

STANDARD_COLUMNS = ["open", "high", "low", "close", "volume", "amount"]

TDX_COLUMN_MAP = {
    "date": "date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "vol": "volume",
    "volume": "volume",
    "amount": "amount",
}

AKSHARE_COLUMN_MAP = {
    "日期": "date",
    "时间": "date",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
    "成交额": "amount",
}


def normalize_daily(df: pd.DataFrame, source: str = "tdx") -> pd.DataFrame:
    """Rename columns, set datetime index, cast dtypes, sort by date.

    Args:
        df: Raw DataFrame from TDX or akshare.
        source: 'tdx' or 'akshare'.

    Returns:
        Standardized DataFrame with DatetimeIndex and columns:
        open, high, low, close, volume, amount.
    """
    df = df.copy()

    col_map = AKSHARE_COLUMN_MAP if source == "akshare" else TDX_COLUMN_MAP
    df.rename(columns=col_map, inplace=True)

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
    elif not isinstance(df.index, pd.DatetimeIndex):
        if "date" in df.index.names:
            df.index = pd.to_datetime(df.index)

    for col in STANDARD_COLUMNS:
        if col not in df.columns:
            df[col] = 0.0

    type_map = {
        "open": "float64", "high": "float64", "low": "float64",
        "close": "float64", "volume": "int64", "amount": "float64",
    }
    df = df.astype({k: v for k, v in type_map.items() if k in df.columns})

    df = df[STANDARD_COLUMNS]
    df.sort_index(inplace=True)
    return df
