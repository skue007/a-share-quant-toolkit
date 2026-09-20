"""形态特征工程 — 纯数学工具函数。

本模块只依赖 numpy，不依赖任何外部数据源，可独立单元测试。
所有函数逻辑与原始实现保持一致（来源: custom_pattern_screener.py）。
"""

from __future__ import annotations

import numpy as np


def rolling_mean(arr: np.ndarray, period: int) -> np.ndarray:
    """滚动均值，返回与输入等长的数组 (前 period-1 位为 NaN)"""
    result = np.full(len(arr), np.nan, dtype=np.float64)
    if len(arr) < period:
        return result
    cum = np.cumsum(np.insert(arr, 0, 0.0))
    result[period - 1:] = (cum[period:] - cum[:-period]) / period
    return result


def pearson_corr(x, y) -> float:
    """手动 Pearson 相关系数，避免 scipy 依赖"""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    xm = x - x.mean()
    ym = y - y.mean()
    num = np.sum(xm * ym)
    denom = np.sqrt(np.sum(xm * xm) * np.sum(ym * ym))
    if denom < 1e-12:
        return 0.0
    return float(num / denom)


def normalize_price(close: np.ndarray) -> np.ndarray:
    """价格 → 累计收益率曲线 (首日=0)"""
    c = np.asarray(close, dtype=np.float64)
    if c[0] <= 0:
        raise ValueError("首日收盘价必须大于0")
    return (c / c[0]) - 1.0


def normalize_volume(vol: np.ndarray, ma: np.ndarray) -> np.ndarray:
    """成交量 → 相对均量比 (vol / MA)"""
    v = np.asarray(vol, dtype=np.float64)
    m = np.asarray(ma, dtype=np.float64)
    m_safe = np.where(m > 0, m, 1e-8)
    return v / m_safe


def resample_curve(y: np.ndarray, target_len: int) -> np.ndarray:
    """np.interp 重采样到固定长度"""
    y = np.asarray(y, dtype=np.float64)
    if len(y) < 2:
        return np.full(target_len, y[0] if len(y) == 1 else 0.0)
    x_old = np.linspace(0, 1, len(y))
    x_new = np.linspace(0, 1, target_len)
    return np.interp(x_new, x_old, y)


def calc_body_ratio(open_arr, high, low, close) -> np.ndarray:
    """K线实体幅度: abs(close-open) / (high-low)，0=十字星，1=无影线"""
    o = np.asarray(open_arr, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    denom = np.maximum(h - l, 1e-8)
    return np.abs(c - o) / denom


def calc_upper_shadow(open_arr, high, low, close) -> np.ndarray:
    """上影线占比: (high - max(open,close)) / (high-low)"""
    o = np.asarray(open_arr, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    denom = np.maximum(h - l, 1e-8)
    return (h - np.maximum(o, c)) / denom


def calc_daily_return(close: np.ndarray) -> np.ndarray:
    """单日涨跌幅: (close[t] - close[t-1]) / close[t-1]，首日=0"""
    c = np.asarray(close, dtype=np.float64)
    ret = np.zeros_like(c)
    if len(c) > 1:
        ret[1:] = (c[1:] - c[:-1]) / np.maximum(c[:-1], 1e-8)
    return ret


# 五维特征名称与中文标签
DIMENSIONS = ["price", "volume", "body", "shadow", "return"]
DIM_LABELS = {
    "price": "价格",
    "volume": "量",
    "body": "实体",
    "shadow": "上影",
    "return": "涨跌",
}
