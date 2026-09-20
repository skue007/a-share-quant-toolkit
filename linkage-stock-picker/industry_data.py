"""
行业分类与候选池构建。

数据来源优先级:
  1. TDX 本地板块文件（快，但需要解析 .dat 格式）
  2. baostock CSRC 行业分类（慢，但一次调用全部缓存）
  3. JSON 缓存文件（离线回退）
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

CACHE_DIR = Path(__file__).resolve().parent / "data" / "cache"
INDUSTRY_CACHE = CACHE_DIR / "industry_map.json"


# ============================================================
# 行业分类获取
# ============================================================

def get_stock_industry_map(force_refresh: bool = False) -> Dict[str, dict]:
    """
    获取全市场股票→行业映射。

    Returns:
        {code: {"name": str, "industry": str, "industry_csrc": str}, ...}

    优先读 JSON 缓存；缓存不存在或 force_refresh 时从 baostock 拉取。
    """
    # 1. 缓存优先
    if not force_refresh and INDUSTRY_CACHE.exists():
        try:
            with open(INDUSTRY_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # 2. 从 baostock 获取
    result = _fetch_from_baostock()
    if result:
        # 写入缓存
        os.makedirs(str(CACHE_DIR), exist_ok=True)
        try:
            with open(INDUSTRY_CACHE, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
        except OSError:
            pass

    return result or {}


def _fetch_from_baostock() -> Optional[Dict[str, dict]]:
    """从 baostock 获取全市场行业分类。"""
    try:
        import baostock as bs

        lg = bs.login()
        if lg.error_code != "0":
            print(f"[industry_data] baostock login failed: {lg.error_msg}")
            return None

        rs = bs.query_stock_industry("")
        if rs.error_code != "0":
            bs.logout()
            return None

        result = {}
        while rs.next():
            row = rs.get_row_data()
            # row: [update_date, code, code_name, industry, industryClassification]
            if len(row) < 4:
                continue
            bs_code = row[1]  # sh.600519
            code = bs_code.replace("sh.", "").replace("sz.", "").zfill(6)
            name = row[2] if len(row) > 2 else ""
            industry = row[3] if len(row) > 3 else ""
            industry_csrc = row[4] if len(row) > 4 else ""
            result[code] = {
                "name": name,
                "industry": industry,
                "industry_csrc": industry_csrc,
            }

        bs.logout()
        return result if result else None

    except ImportError:
        print("[industry_data] baostock not installed, industry feature disabled")
        return None
    except Exception as e:
        print(f"[industry_data] baostock error: {e}")
        return None


# ============================================================
# 候选池构建
# ============================================================

def get_target_industry(code: str) -> str:
    """获取目标股票的 CSRC 行业名称。"""
    code = code.zfill(6)
    ind_map = get_stock_industry_map()
    info = ind_map.get(code, {})
    return info.get("industry", "")


def get_target_name(code: str) -> str:
    """获取目标股票名称。"""
    code = code.zfill(6)
    ind_map = get_stock_industry_map()
    info = ind_map.get(code, {})
    return info.get("name", "")


def get_same_industry_stocks(code: str) -> List[str]:
    """
    获取与目标股票同一 CSRC 行业的所有股票代码。

    Args:
        code: 6位股票代码

    Returns:
        同行业股票代码列表（不包含目标自身）
    """
    code = code.zfill(6)
    ind_map = get_stock_industry_map()
    if code not in ind_map:
        return []

    target_ind = ind_map[code].get("industry", "")
    if not target_ind:
        return []

    result = [
        c for c, info in ind_map.items()
        if info.get("industry") == target_ind and c != code
    ]
    return result


def get_sibling_industry_stocks(code: str, delta: int = 5) -> List[str]:
    """
    获取同一 CSRC 字母下数字编号相邻行业（±delta）的所有股票。

    例如 C15 的兄弟行业包括 C10-C20 范围内且 C 开头的行业。
    """
    code = code.zfill(6)
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

    # 找出符合条件的行业
    all_industries = set(info.get("industry", "") for info in ind_map.values())
    sibling_industries = set()
    for ind in all_industries:
        m2 = re.match(r"^([A-Z])(\d{2})", ind)
        if m2 and m2.group(1) == letter and abs(int(m2.group(2)) - num) <= delta:
            sibling_industries.add(ind)

    result = [
        c for c, info in ind_map.items()
        if info.get("industry") in sibling_industries and c != code
    ]
    return result


def get_all_stocks_code(exclude_st: bool = True) -> List[str]:
    """获取全市场股票代码列表。"""
    ind_map = get_stock_industry_map()
    codes = list(ind_map.keys())
    if exclude_st:
        codes = [
            c for c, info in ind_map.items()
            if "ST" not in info.get("name", "") and "退" not in info.get("name", "")
        ]
    return codes
