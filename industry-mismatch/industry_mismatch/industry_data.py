"""行业分类与候选池构建。

数据来源（按优先级）:
  1. 用户缓存 JSON（~/.industry_mismatch/cache/industry_map.json）
  2. 随包分发的内置缓存（data/industry_map.json，公开的 baostock CSRC 行业分类）
  3. baostock 在线拉取（网络可用时自动刷新缓存）

无外部路径依赖：缓存与包内置数据均可独立工作。
"""

from __future__ import annotations

import re
from typing import Dict, List

from .data_source import BaostockDataSource, DataSource

# 全局默认数据源（惰性初始化）
_default_source: DataSource | None = None


def _get_default_source() -> DataSource:
    """获取全局默认数据源（baostock）。"""
    global _default_source
    if _default_source is None:
        _default_source = BaostockDataSource()
    return _default_source


def get_stock_industry_map(
    force_refresh: bool = False,
    source: DataSource | None = None,
) -> Dict[str, dict]:
    """获取全市场 股票→行业 映射。

    Returns:
        {code: {"name": str, "industry": str, "industry_csrc": str}, ...}
    """
    src = source or _get_default_source()
    return src.get_stock_industry_map(force_refresh=force_refresh)


# ============================================================
# 候选池构建
# ============================================================

def get_target_industry(code: str) -> str:
    """获取目标股票的 CSRC 行业名称。"""
    code = str(code).zfill(6)
    ind_map = get_stock_industry_map()
    info = ind_map.get(code, {})
    return info.get("industry", "")


def get_target_name(code: str) -> str:
    """获取目标股票名称。"""
    code = str(code).zfill(6)
    ind_map = get_stock_industry_map()
    info = ind_map.get(code, {})
    return info.get("name", "")


def get_same_industry_stocks(code: str) -> List[str]:
    """获取与目标股票同一 CSRC 行业的所有股票代码（不含目标自身）。"""
    code = str(code).zfill(6)
    ind_map = get_stock_industry_map()
    if code not in ind_map:
        return []

    target_ind = ind_map[code].get("industry", "")
    if not target_ind:
        return []

    return [
        c for c, info in ind_map.items()
        if info.get("industry") == target_ind and c != code
    ]


def get_sibling_industry_stocks(code: str, delta: int = 5) -> List[str]:
    """获取同一 CSRC 字母下数字编号相邻行业（±delta）的所有股票。

    例如 C15 的兄弟行业包括 C10-C20 范围内且 C 开头的行业。
    """
    code = str(code).zfill(6)
    ind_map = get_stock_industry_map()
    if code not in ind_map:
        return []

    target_ind = ind_map[code].get("industry", "")
    m = re.match(r"^([A-Z])(\d{2})", target_ind)
    if not m:
        # 无法解析，回退到同行业
        return get_same_industry_stocks(code)

    letter = m.group(1)
    num = int(m.group(2))

    all_industries = set(info.get("industry", "") for info in ind_map.values())
    sibling_industries = set()
    for ind in all_industries:
        m2 = re.match(r"^([A-Z])(\d{2})", ind)
        if m2 and m2.group(1) == letter and abs(int(m2.group(2)) - num) <= delta:
            sibling_industries.add(ind)

    return [
        c for c, info in ind_map.items()
        if info.get("industry") in sibling_industries and c != code
    ]


def get_all_stocks_code(exclude_st: bool = True) -> List[str]:
    """获取全市场股票代码列表（可选排除 ST / 退市）。"""
    ind_map = get_stock_industry_map()
    codes = list(ind_map.keys())
    if exclude_st:
        codes = [
            c for c, info in ind_map.items()
            if "ST" not in info.get("name", "") and "退" not in info.get("name", "")
        ]
    return codes
