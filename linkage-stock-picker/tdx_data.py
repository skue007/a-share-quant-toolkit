"""TDX 本地日线数据提供者 — 联动选股独立版。

直接通过 data_utils.read_tdx_day_fast 读取通达信本地 .day 文件。

TDX 数据目录通过环境变量 TDX_ROOT 配置（见 .env.example），
默认尝试常见安装路径，找不到时提示用户配置。

接口与页面代码约定：
    get_provider() -> TdxDailyProvider
    provider.get_daily(code) -> DataFrame(date, open, high, low, close, amount, volume)
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from data_utils import read_tdx_day_fast

# 加载项目根目录 .env（可选，用于配置 TDX_ROOT）
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

# ── 常见通达信安装路径（按顺序探测） ──────────────────────────
_COMMON_TDX_ROOTS = [
    r"D:/program/通达信",
    r"D:/软件/通达信",
    r"G:/软件/通达信",
    r"C:/new_tdx",
    r"C:/zd_通达信",
    r"D:/tdx",
]

TDX_ROOT = Path(os.getenv("TDX_ROOT", "") or "").resolve()


def _detect_tdx_root() -> Path:
    """探测 TDX 根目录：优先环境变量 TDX_ROOT，其次常见路径。"""
    if TDX_ROOT.exists():
        return TDX_ROOT
    for root in _COMMON_TDX_ROOTS:
        p = Path(root)
        if (p / "vipdoc").exists():
            return p
    return TDX_ROOT  # 未找到则返回原值，由调用方报错


class TdxDailyProvider:
    """通达信本地日线数据提供者（轻量实现）。"""

    def __init__(self, tdx_root: str | Path | None = None):
        self.tdx_root = Path(tdx_root) if tdx_root else _detect_tdx_root()

    # ── 内部工具 ────────────────────────────────────────────

    def _resolve_day_file(self, symbol: str) -> Path | None:
        """根据股票代码定位 TDX .day 文件路径。"""
        symbol = str(symbol).strip().lower()
        if symbol.startswith(("sh", "sz", "bj")):
            market, code = symbol[:2], symbol[2:]
        elif symbol.startswith("6"):
            market, code = "sh", symbol
        elif symbol.startswith(("0", "3")):
            market, code = "sz", symbol
        elif symbol.startswith(("4", "8", "9")):
            market, code = "bj", symbol
        else:
            return None
        if not code.isdigit() or len(code) != 6:
            return None
        return self.tdx_root / "vipdoc" / market / "lday" / f"{market}{code}.day"

    # ── 主接口 ──────────────────────────────────────────────

    def get_daily(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        adjust: str | None = None,
    ) -> pd.DataFrame:
        """读取单只股票日线数据。

        Args:
            symbol: 股票代码（如 "600519" / "sh600519"）
            start: 起始日期 "YYYY-MM-DD"（可选）
            end:   截止日期 "YYYY-MM-DD"（可选）
            adjust: 复权方式（原系统支持 qfq/hfq；本地 .day 为不复权数据，
                    这里保留参数以兼容接口，但不做复权）

        Returns:
            DataFrame(date, open, high, low, close, amount, volume)，升序排列
        """
        day_file = self._resolve_day_file(symbol)
        if day_file is None or not day_file.exists():
            return pd.DataFrame()

        df = read_tdx_day_fast(day_file)
        if df.empty:
            return df

        if start:
            df = df[df["date"] >= pd.Timestamp(start)]
        if end:
            df = df[df["date"] <= pd.Timestamp(end)]
        return df.reset_index(drop=True)

    def check(self) -> tuple[bool, str]:
        """检查 TDX 数据目录是否可用。"""
        if not self.tdx_root.exists():
            return False, f"TDX 目录不存在: {self.tdx_root}"
        vipdoc = self.tdx_root / "vipdoc"
        if not vipdoc.exists():
            return False, f"TDX 目录缺少 vipdoc 子目录: {self.tdx_root}"
        return True, f"TDX 数据目录: {self.tdx_root}"


# ── Streamlit 单例 ──────────────────────────────────────────

def get_provider() -> TdxDailyProvider:
    """返回缓存的 TdxDailyProvider 单例（Streamlit session）。"""
    if "linkage_tdx_provider" not in st.session_state:
        st.session_state["linkage_tdx_provider"] = TdxDailyProvider()
    return st.session_state["linkage_tdx_provider"]
