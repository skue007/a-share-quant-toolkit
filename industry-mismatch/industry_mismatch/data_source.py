"""数据源抽象 — 提供统一的日线行情与行业分类获取。

默认实现: :class:`BaostockDataSource`（免费网络数据，开箱即用）。
如需接入本地通达信(TDX)或其它行情源，实现 :class:`DataSource` 接口即可。
"""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from . import config


class DataSource(ABC):
    """统一数据源接口。"""

    @abstractmethod
    def get_daily(self, code: str) -> pd.DataFrame:
        """获取单只股票日线数据。

        Args:
            code: 6 位股票代码（如 "600519"）

        Returns:
            DataFrame，列: date, open, high, low, close, volume
            （date 为 datetime 类型，升序）；失败返回空 DataFrame。
        """

    @abstractmethod
    def get_stock_industry_map(self, force_refresh: bool = False) -> Dict[str, dict]:
        """获取全市场 股票→行业 映射。

        Returns:
            {code: {"name": str, "industry": str, "industry_csrc": str}, ...}
        """

    def close(self) -> None:
        """释放资源（网络会话等），可选覆写。"""


# ============================================================
# baostock 实现
# ============================================================

class BaostockDataSource(DataSource):
    """基于 baostock 的网络数据源（免费，无需本地行情文件）。

    - 日线: query_history_k_data_plus，前复权
    - 行业: query_stock_industry（CSRC 证监会行业分类），带本地 JSON 缓存
    """

    _session = None
    _session_lock = threading.Lock()

    def __init__(self) -> None:
        self._industry_cache: Optional[Dict[str, dict]] = None

    # ── 会话管理（线程安全、幂等）──

    @classmethod
    def _login(cls):
        if cls._session is not None:
            return cls._session
        with cls._session_lock:
            if cls._session is None:
                import baostock as bs
                lg = bs.login()
                if lg.error_code != "0":
                    raise RuntimeError(f"baostock login failed: {lg.error_msg}")
                cls._session = bs
        return cls._session

    @classmethod
    def _logout(cls) -> None:
        with cls._session_lock:
            if cls._session is not None:
                try:
                    cls._session.logout()
                except Exception:
                    pass
                cls._session = None

    # ── 日线 ──

    def get_daily(self, code: str) -> pd.DataFrame:
        bs = self._login()
        code = str(code).zfill(6)
        bs_code = f"sh.{code}" if code.startswith("6") else f"sz.{code}"
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume,amount,pctChg",
                start_date=config.BAOSTOCK_START_DATE,
                end_date=config.BAOSTOCK_END_DATE,
                frequency="d",
                adjustflag=config.BAOSTOCK_ADJUST,
            )
            if rs.error_code != "0":
                return pd.DataFrame()
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows, columns=rs.fields)
            for col in ["open", "high", "low", "close", "volume", "amount"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["close"])
            df = df[df["close"] > 0]
            return df
        except Exception:
            return pd.DataFrame()

    # ── 行业分类 ──

    def get_stock_industry_map(self, force_refresh: bool = False) -> Dict[str, dict]:
        if not force_refresh and self._industry_cache is not None:
            return self._industry_cache

        result: Dict[str, dict] = {}

        # 1. 用户缓存
        if not force_refresh and config.INDUSTRY_CACHE_FILE.exists():
            try:
                result = json.loads(config.INDUSTRY_CACHE_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                result = {}

        # 2. 内置缓存（随包分发，公开数据）
        if not result and not force_refresh and config.BUILTIN_INDUSTRY_CACHE.exists():
            try:
                result = json.loads(config.BUILTIN_INDUSTRY_CACHE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                result = {}

        # 3. 在线拉取（baostock）
        if force_refresh or not result:
            fetched = self._fetch_industry_from_baostock()
            if fetched:
                result = fetched
                try:
                    config.ensure_dirs()
                    config.INDUSTRY_CACHE_FILE.write_text(
                        json.dumps(result, ensure_ascii=False), encoding="utf-8"
                    )
                except OSError:
                    pass

        self._industry_cache = result
        return result

    def _fetch_industry_from_baostock(self) -> Optional[Dict[str, dict]]:
        try:
            bs = self._login()
            rs = bs.query_stock_industry("")
            if rs.error_code != "0":
                return None
            result: Dict[str, dict] = {}
            while rs.next():
                row = rs.get_row_data()
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
            return result if result else None
        except ImportError:
            print("[industry_data] baostock not installed, industry feature disabled")
            return None
        except Exception as e:
            print(f"[industry_data] baostock error: {e}")
            return None

    def close(self) -> None:
        self._logout()
