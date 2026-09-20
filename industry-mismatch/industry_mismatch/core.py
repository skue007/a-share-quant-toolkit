"""行业错配检测 — 核心分析逻辑。

已剥离 Streamlit UI，纯逻辑函数可被 CLI / Web UI / 其它程序复用。
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .data_source import BaostockDataSource, DataSource
from .linkage import find_linked_stocks

# 信号等级
SIGNAL_STRONG = "🔴 强错配"
SIGNAL_WATCH = "🟡 关注"
SIGNAL_NORMAL = "—"

# 错配信号 (供序列化使用，去掉 emoji)
SIGNAL_STRONG_TEXT = "强错配"
SIGNAL_WATCH_TEXT = "关注"
SIGNAL_NORMAL_TEXT = "正常"


# ============================================================
# 数据规范化
# ============================================================

def normalize_daily_df(df: pd.DataFrame) -> pd.DataFrame:
    """统一日线 DataFrame 列名和索引（兼容多种来源）。"""
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index()
        if "date" not in df.columns and "index" in df.columns:
            df = df.rename(columns={"index": "date"})
    column_aliases = {
        "date": ["date", "trade_date", "日期", "time", "datetime"],
        "open": ["open", "开盘"],
        "high": ["high", "最高"],
        "low": ["low", "最低"],
        "close": ["close", "收盘"],
        "volume": ["volume", "成交量", "vol"],
    }
    rename = {}
    for target_col, aliases in column_aliases.items():
        for alias in aliases:
            if alias in df.columns:
                rename[alias] = target_col
                break
    if rename:
        df = df.rename(columns=rename)
    if "close" in df.columns:
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["close"])
        df = df[df["close"] > 0]
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


# ============================================================
# Step 1: 暴涨股扫描
# ============================================================

def scan_surge_stocks(
    get_daily_fn,
    all_codes: List[str],
    lookback_days: int,
    min_return: float,
    max_return: float,
    industry_map: Dict[str, dict],
    progress_callback=None,
) -> List[Tuple[str, str, float, str]]:
    """扫描全市场短期暴涨股（涨幅区间过滤）。

    Args:
        get_daily_fn: 日线获取函数
        all_codes: 全市场股票代码列表
        lookback_days: 回看交易日数
        min_return: 涨幅下限（如 0.20 = 20%）
        max_return: 涨幅上限（如 1.20 = 120%），超过此值的「明星股」被排除
        industry_map: 行业映射
        progress_callback: (current, total) -> None

    Returns:
        [(code, name, n_day_return, industry), ...] 按涨幅降序排列
    """
    results = []
    total = len(all_codes)

    for i, code in enumerate(all_codes):
        if progress_callback and i % 100 == 0:
            progress_callback(i, total)

        try:
            df = get_daily_fn(code)
            if df.empty or "close" not in df.columns:
                continue

            df = df.sort_values("date")
            closes = df["close"].values

            if len(closes) < lookback_days + 1:
                continue

            start_price = closes[-(lookback_days + 1)]
            end_price = closes[-1]
            if start_price <= 0:
                continue

            n_day_return = (end_price - start_price) / start_price

            if min_return <= n_day_return <= max_return:
                info = industry_map.get(code, {})
                name = info.get("name", "")
                industry = info.get("industry", "")
                results.append((code, name, n_day_return, industry))
        except Exception:
            continue

    results.sort(key=lambda x: x[2], reverse=True)
    return results


# ============================================================
# 行业分布分析
# ============================================================

def analyze_industry_distribution(
    linked_codes: List[str],
    target_industry: str,
    industry_map: Dict[str, dict],
) -> Dict:
    """分析联动股票的行业分布。

    Returns:
        {
            "same_industry_count": int,
            "other_industry_count": int,
            "escape_rate": float,
            "top_alt_industry": str | None,
            "alt_industry_count": int,
            "industry_breakdown": [(industry, count), ...],
        }
    """
    if not linked_codes:
        return {
            "same_industry_count": 0,
            "other_industry_count": 0,
            "escape_rate": 0.0,
            "top_alt_industry": None,
            "alt_industry_count": 0,
            "industry_breakdown": [],
        }

    industry_counter = Counter()
    same_count = 0

    for code in linked_codes:
        info = industry_map.get(code, {})
        ind = info.get("industry", "未知")
        industry_counter[ind] += 1
        if ind == target_industry:
            same_count += 1

    other_count = len(linked_codes) - same_count
    escape_rate = other_count / len(linked_codes) if linked_codes else 0.0

    top_alt_industry = None
    alt_industry_count = 0
    for ind, cnt in industry_counter.most_common():
        if ind != target_industry:
            top_alt_industry = ind
            alt_industry_count = cnt
            break

    return {
        "same_industry_count": same_count,
        "other_industry_count": other_count,
        "escape_rate": escape_rate,
        "top_alt_industry": top_alt_industry,
        "alt_industry_count": alt_industry_count,
        "industry_breakdown": industry_counter.most_common(10),
    }


# ============================================================
# 错配指标计算
# ============================================================

def compute_divergence_metrics(
    same_ind_df: pd.DataFrame,
    full_mkt_df: pd.DataFrame,
    target_code: str,
    target_industry: str,
    industry_map: Dict[str, dict],
) -> Dict:
    """计算行业错配指标。

    Args:
        same_ind_df: 同行业联动结果 DataFrame（columns 含 code, total_score）
        full_mkt_df: 全市场联动结果 DataFrame
        target_code: 目标股票代码
        target_industry: 目标股票的 CSRC 行业

    Returns:
        dict with divergence_ratio, escape_rate, alt_industry, mismatch_score, etc.
    """
    top_n = 5
    same_top = same_ind_df.head(top_n) if not same_ind_df.empty else pd.DataFrame()
    full_top = full_mkt_df.head(top_n) if not full_mkt_df.empty else pd.DataFrame()

    avg_same = same_top["total_score"].mean() if not same_top.empty else 0.0
    avg_full = full_top["total_score"].mean() if not full_top.empty else 0.0

    divergence_ratio = avg_full / max(avg_same, 0.01)

    full_top10 = full_mkt_df.head(10) if not full_mkt_df.empty else pd.DataFrame()
    full_top_codes = full_top10["code"].tolist() if not full_top10.empty else []

    distribution = analyze_industry_distribution(
        full_top_codes, target_industry, industry_map
    )

    escape_rate = distribution["escape_rate"]
    alt_industry = distribution["top_alt_industry"]

    # 偏离度映射到 0-40（阈值1.0=0分, 2.0=40分）
    divergence_component = min(max((divergence_ratio - 1.0) * 40, 0), 40)
    # 逃逸率映射到 0-60
    escape_component = escape_rate * 60
    mismatch_score = divergence_component + escape_component

    best_same = same_top["total_score"].max() if not same_top.empty else 0.0
    best_full = full_top["total_score"].max() if not full_top.empty else 0.0

    all_full_codes = full_mkt_df["code"].tolist() if not full_mkt_df.empty else []
    full_same_ind_count = sum(
        1 for c in all_full_codes
        if industry_map.get(c, {}).get("industry", "") == target_industry
    )

    return {
        "avg_same_score": avg_same,
        "avg_full_score": avg_full,
        "divergence_ratio": divergence_ratio,
        "best_same_score": best_same,
        "best_full_score": best_full,
        "escape_rate": escape_rate,
        "alt_industry": alt_industry,
        "alt_industry_count": distribution["alt_industry_count"],
        "mismatch_score": mismatch_score,
        "same_industry_count": distribution["same_industry_count"],
        "other_industry_count": distribution["other_industry_count"],
        "full_same_ind_count": full_same_ind_count,
        "industry_breakdown": distribution["industry_breakdown"],
    }


def classify_signal(
    metrics: Dict,
    divergence_threshold: float = 1.15,
    escape_threshold: float = 0.40,
) -> str:
    """根据用户阈值判定信号等级。

    Returns:
        SIGNAL_STRONG / SIGNAL_WATCH / SIGNAL_NORMAL (含 emoji)
    """
    diverges = metrics["divergence_ratio"] >= divergence_threshold
    escapes = metrics["escape_rate"] >= escape_threshold

    if diverges and escapes:
        return SIGNAL_STRONG
    elif diverges or escapes:
        return SIGNAL_WATCH
    return SIGNAL_NORMAL


# ============================================================
# 结果结构
# ============================================================

@dataclass
class DetectionConfig:
    """检测参数（与 Streamlit 侧栏控件一一对应）。"""

    lookback_days: int = 60          # 暴涨回看交易日
    min_return: float = 0.20         # 涨幅下限 20%
    max_return: float = 1.20         # 涨幅上限 120%
    max_candidates: int = 50         # 最多分析候选数
    top_n_linkage: int = 10          # 联动返回数量
    linkage_lookback: int = 120      # 联动对齐交易日
    half_life: int = 60              # 时间权重半衰期
    divergence_threshold: float = 1.15  # 联动偏离度阈值
    escape_threshold: float = 0.40   # 行业逃逸率阈值


@dataclass
class StockResult:
    """单只股票的错配检测结果（可序列化）。"""

    code: str
    name: str
    return_pct: float
    industry: str
    signal: str
    metrics: Dict = field(default_factory=dict)
    same_industry_top: List[Dict] = field(default_factory=list)
    full_market_top: List[Dict] = field(default_factory=list)


def _df_to_records(df: pd.DataFrame, limit: int = 5) -> List[Dict]:
    """联动结果 DataFrame → 可 JSON 序列化的记录列表。"""
    if df is None or df.empty:
        return []
    cols = ["code", "total_score", "direction_match", "return_corr",
            "r_squared", "beta", "shape_sim", "roll_stability"]
    cols = [c for c in cols if c in df.columns]
    records = []
    for _, row in df.head(limit).iterrows():
        rec = {}
        for c in cols:
            v = row[c]
            rec[c] = float(v) if isinstance(v, (int, float, np.floating)) else str(v)
        records.append(rec)
    return records


# ============================================================
# 检测编排（CLI / UI 共用）
# ============================================================

def run_detection(
    config_: DetectionConfig,
    source: Optional[DataSource] = None,
    progress_callback: Optional[Callable[[str, float, str], None]] = None,
) -> Dict:
    """执行完整行业错配检测流程。

    Args:
        config_: 检测参数
        source: 数据源（默认 baostock）
        progress_callback: (stage, percent, message) -> None，stage ∈ {"step1","step2","done"}

    Returns:
        结果字典:
        {
            "config": {...},
            "surge_stocks": [...],          # 暴涨股列表
            "results": [StockResult...],    # 按错配得分降序
            "stats": {"analyzed": n, "strong": n, "watch": n, "normal": n},
            "elapsed": {...},
            "generated_at": "YYYY-MM-DD HH:MM:SS",
        }
    """
    from datetime import datetime

    src = source or BaostockDataSource()
    industry_map = src.get_stock_industry_map()

    start_total = time.time()

    def _normalize_daily(code: str) -> pd.DataFrame:
        return normalize_daily_df(src.get_daily(code))

    # ── Step 1: 暴涨股扫描 ──
    t0 = time.time()
    # 候选池基于数据源返回的行业映射构建（保证与数据源一致）
    all_codes = [
        c for c, info in industry_map.items()
        if "ST" not in info.get("name", "") and "退" not in info.get("name", "")
    ]

    def _cb_step1(current: int, total: int):
        if progress_callback:
            pct = current / max(total, 1)
            progress_callback("step1", pct, f"暴涨扫描中... {current}/{total}")

    surge_stocks = scan_surge_stocks(
        get_daily_fn=_normalize_daily,
        all_codes=all_codes,
        lookback_days=config_.lookback_days,
        min_return=config_.min_return,
        max_return=config_.max_return,
        industry_map=industry_map,
        progress_callback=_cb_step1,
    )
    step1_elapsed = time.time() - t0

    surge_stocks = surge_stocks[:config_.max_candidates]

    # ── Step 2: 双范围联动对比 ──
    t1 = time.time()
    results: List[StockResult] = []
    total_candidates = len(surge_stocks)

    for idx, (code, name, n_day_return, target_ind) in enumerate(surge_stocks):
        if progress_callback:
            pct = (idx + 1) / max(total_candidates, 1)
            progress_callback(
                "step2", pct,
                f"联动对比中... [{idx + 1}/{total_candidates}] {code} {name}",
            )

        same_ind_candidates = [
            c for c, info in industry_map.items()
            if info.get("industry") == target_ind and c != code
        ]
        full_mkt_candidates = [c for c in all_codes if c != code]

        if not same_ind_candidates:
            continue

        try:
            same_ind_df = find_linked_stocks(
                target_code=code,
                candidate_codes=same_ind_candidates,
                get_daily_fn=_normalize_daily,
                half_life=config_.half_life,
                top_n=config_.top_n_linkage,
                lookback_days=config_.linkage_lookback,
            )
        except Exception:
            same_ind_df = pd.DataFrame()

        try:
            full_mkt_df = find_linked_stocks(
                target_code=code,
                candidate_codes=full_mkt_candidates,
                get_daily_fn=_normalize_daily,
                half_life=config_.half_life,
                top_n=config_.top_n_linkage,
                lookback_days=config_.linkage_lookback,
            )
        except Exception:
            full_mkt_df = pd.DataFrame()

        if same_ind_df.empty and full_mkt_df.empty:
            continue

        metrics = compute_divergence_metrics(
            same_ind_df=same_ind_df,
            full_mkt_df=full_mkt_df,
            target_code=code,
            target_industry=target_ind,
            industry_map=industry_map,
        )
        signal = classify_signal(
            metrics,
            divergence_threshold=config_.divergence_threshold,
            escape_threshold=config_.escape_threshold,
        )

        results.append(StockResult(
            code=code,
            name=name,
            return_pct=round(n_day_return * 100, 2),
            industry=target_ind,
            signal=signal,
            metrics=metrics,
            same_industry_top=_df_to_records(same_ind_df, limit=config_.top_n_linkage),
            full_market_top=_df_to_records(full_mkt_df, limit=config_.top_n_linkage),
        ))

    step2_elapsed = time.time() - t1
    total_elapsed = time.time() - start_total

    results.sort(key=lambda r: r.metrics.get("mismatch_score", 0.0), reverse=True)

    strong = sum(1 for r in results if r.signal == SIGNAL_STRONG)
    watch = sum(1 for r in results if r.signal == SIGNAL_WATCH)
    normal = sum(1 for r in results if r.signal == SIGNAL_NORMAL)

    if progress_callback:
        progress_callback("done", 1.0, "完成")

    return {
        "config": asdict(config_),
        "surge_stocks": [
            {
                "code": c, "name": n,
                "return_pct": round(r * 100, 2),
                "industry": ind,
            }
            for c, n, r, ind in surge_stocks
        ],
        "results": [asdict(r) for r in results],
        "stats": {
            "total_candidates": total_candidates,
            "analyzed": len(results),
            "strong": strong,
            "watch": watch,
            "normal": normal,
        },
        "elapsed": {
            "step1_seconds": round(step1_elapsed, 1),
            "step2_seconds": round(step2_elapsed, 1),
            "total_seconds": round(total_elapsed, 1),
        },
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
