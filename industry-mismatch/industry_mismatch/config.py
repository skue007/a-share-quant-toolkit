"""行业错配检测 — 全局配置。"""

from __future__ import annotations

import os
from pathlib import Path

# ── 路径 ──────────────────────────────────────────────
PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent

# 随包分发的内置数据（只读，公开的 baostock CSRC 行业分类缓存）
BUILTIN_DATA_DIR = PACKAGE_ROOT / "data"

# 用户数据目录（缓存、输出），默认 ~/.industry_mismatch
USER_DATA_DIR = Path(
    os.getenv("INDUSTRY_MISMATCH_DATA_DIR", str(Path.home() / ".industry_mismatch"))
)
CACHE_DIR = USER_DATA_DIR / "cache"
OUTPUT_DIR = USER_DATA_DIR / "output"

# 行业分类缓存文件（用户目录优先，未命中回退内置 data/industry_map.json）
INDUSTRY_CACHE_FILE = CACHE_DIR / "industry_map.json"
BUILTIN_INDUSTRY_CACHE = BUILTIN_DATA_DIR / "industry_map.json"

# ── baostock ──────────────────────────────────────────
# 日线查询起始日期（足够覆盖 120 天联动回看）
BAOSTOCK_START_DATE = os.getenv("BAOSTOCK_START_DATE", "2024-01-01")
BAOSTOCK_END_DATE = os.getenv("BAOSTOCK_END_DATE", "2099-12-31")
# 2 = 前复权
BAOSTOCK_ADJUST = "2"


def ensure_dirs() -> None:
    """确保缓存/输出目录存在（首次运行调用一次）。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
