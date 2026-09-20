"""
联动分析引擎 — 五维度衡量两只股票的联动强度。

指标：
  1. 方向一致率 — 同涨同跌的交易日占比（最直观）
  2. 日收益率加权相关 — 时间加权 Pearson r（经典联动指标）
  3. 累计收益曲线相关 — 归一化价格形态相似度
  4. 联动 R² + Beta — 目标对候选的解释力
  5. 滚动相关稳定性 — 联动是否持续稳定

完全基于 TDX 本地数据（通过 tdx_data 获取），毫秒级单股读取。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ============================================================
# 底层数学工具
# ============================================================

def _decay_weights(n: int, half_life: int = 60) -> np.ndarray:
    """指数衰减权重。w[0]=1.0（最新），w[-1]=最小（最旧）。已归一化。"""
    if n <= 0:
        return np.array([], dtype=np.float64)
    lam = np.log(2) / half_life
    w = np.exp(-lam * np.arange(n, dtype=np.float64))
    return w / w.sum()


def _weighted_corr(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    """加权 Pearson 相关系数。"""
    if len(x) < 3 or len(x) != len(y) or len(x) != len(w):
        return 0.0
    w_sum = w.sum()
    if w_sum == 0:
        return 0.0
    xm = np.average(x, weights=w)
    ym = np.average(y, weights=w)
    xd, yd = x - xm, y - ym
    cov = np.sum(w * xd * yd)
    denom = np.sqrt(np.sum(w * xd * xd) * np.sum(w * yd * yd))
    if denom < 1e-15:
        return 0.0
    return float(np.clip(cov / denom, -1.0, 1.0))


def _simple_corr(x: np.ndarray, y: np.ndarray) -> float:
    """等权 Pearson r（快筛用）。"""
    if len(x) < 3 or np.std(x) < 1e-15 or np.std(y) < 1e-15:
        return 0.0
    return float(np.clip(np.corrcoef(x, y)[0, 1], -1.0, 1.0))


# ============================================================
# 五维度联动指标
# ============================================================

def direction_match_rate(
    returns_a: np.ndarray,
    returns_b: np.ndarray,
    threshold: float = 0.001,
) -> float:
    """
    方向一致率：同涨同跌的交易日占比。

    排除微幅波动（|ret| < threshold），只看有明显涨跌的交易日。
    0.80 = 80% 的交易日同向。
    """
    if len(returns_a) != len(returns_b) or len(returns_a) < 10:
        return 0.0
    mask_a = np.abs(returns_a) > threshold
    mask_b = np.abs(returns_b) > threshold
    mask = mask_a & mask_b
    if mask.sum() < 10:
        return 0.0
    matches = (np.sign(returns_a[mask]) == np.sign(returns_b[mask])).sum()
    return float(matches / len(returns_a[mask]))


def weighted_return_correlation(
    returns_a: np.ndarray,
    returns_b: np.ndarray,
    half_life: int = 60,
    max_window: int = 120,
) -> float:
    """
    日收益率时间加权相关系数。

    固定使用最近 max_window 天（默认120），确保不同长度数据的权重分布一致。
    短于 max_window 的用实际长度。
    """
    n = min(len(returns_a), len(returns_b), max_window)
    if n < 20:
        return 0.0
    w = _decay_weights(n, half_life)
    return _weighted_corr(returns_a[-n:], returns_b[-n:], w)


def cumulative_return_similarity(
    close_a: np.ndarray,
    close_b: np.ndarray,
) -> float:
    """
    累计收益曲线相关度。

    两只股票都从 1.0 起步计算累计收益，然后计算 Pearson 相关系数。
    映射到 [0, 1]：0.5 = 不相关，1.0 = 完全同步。
    """
    n = min(len(close_a), len(close_b))
    if n < 20:
        return 0.0
    a, b = close_a[-n:], close_b[-n:]
    ra = np.diff(a) / a[:-1]
    rb = np.diff(b) / b[:-1]
    if np.std(ra) < 1e-10 or np.std(rb) < 1e-10:
        return 0.0
    cum_a = np.cumprod(1.0 + np.nan_to_num(ra, nan=0.0))
    cum_b = np.cumprod(1.0 + np.nan_to_num(rb, nan=0.0))
    corr = np.corrcoef(cum_a, cum_b)[0, 1]
    if np.isnan(corr):
        return 0.0
    return float(np.clip((corr + 1.0) / 2.0, 0.0, 1.0))


def co_movement_beta_r2(
    returns_a: np.ndarray,
    returns_b: np.ndarray,
) -> tuple[float, float]:
    """
    联动强度 OLS：returns_b = α + β × returns_a + ε。

    Returns:
        beta: 联动弹性（1.0 = 目标涨1%，候选涨1%）
        r2:   拟合优度（0~1，目标对候选的解释力）
    """
    if len(returns_a) != len(returns_b) or len(returns_a) < 20:
        return 0.0, 0.0
    x, y = returns_a, returns_b
    var_x = np.var(x)
    if var_x < 1e-15:
        return 0.0, 0.0
    beta = np.cov(x, y)[0, 1] / var_x
    y_pred = np.mean(y) + beta * (x - np.mean(x))
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = max(0.0, min(1.0, 1.0 - ss_res / ss_tot)) if ss_tot > 1e-15 else 0.0
    return float(beta), float(r2)


# ============================================================
# 残差互信息 (RMI) — 捕捉线性相关之外的隐藏关系
# ============================================================

def residual_mutual_information(
    returns_a: np.ndarray,
    returns_b: np.ndarray,
    bins: int = 20,
) -> dict:
    """
    计算残差互信息 (Residual Mutual Information)。

    RMI = 实际互信息 - 高斯基准互信息

    高斯基准: MI_gauss = -0.5 * ln(1 - corr²)
    实际 MI: 基于二维直方图的经验互信息

    RMI > 0 表示存在线性相关之外的隐藏关系（非线性依赖）。
    RMI ≈ 0 表示关系完全可以由线性相关解释。

    Returns:
        dict with:
        - mi_actual:  实际经验互信息
        - mi_gaussian: 高斯基准互信息
        - rmi:        残差互信息 (actual - gaussian)
        - rmi_score:  归一化到 [0, 1] 的 RMI 得分
    """
    n = min(len(returns_a), len(returns_b))
    if n < 30:
        return {"mi_actual": 0.0, "mi_gaussian": 0.0, "rmi": 0.0, "rmi_score": 0.0}

    # 计算相关系数
    corr = np.corrcoef(returns_a[-n:], returns_b[-n:])[0, 1]
    if np.isnan(corr):
        corr = 0.0
    corr = np.clip(corr, -0.999, 0.999)

    # 高斯基准互信息
    mi_gaussian = -0.5 * np.log(max(1e-10, 1.0 - corr * corr))

    # 经验互信息（二维直方图法）
    a = returns_a[-n:]
    b = returns_b[-n:]

    # 自适应 bins：数据越多 bins 越多
    actual_bins = max(10, min(bins, int(np.sqrt(n))))

    # 2D histogram
    hist_2d, _, _ = np.histogram2d(a, b, bins=actual_bins)
    # 避免 log(0)
    hist_2d = hist_2d / n  # 联合概率

    # 边际概率
    p_a = np.sum(hist_2d, axis=1)
    p_b = np.sum(hist_2d, axis=0)

    # 互信息: I(X;Y) = Σ p(x,y) * log(p(x,y) / (p(x)*p(y)))
    mi_actual = 0.0
    for i in range(actual_bins):
        for j in range(actual_bins):
            if hist_2d[i, j] > 0 and p_a[i] > 0 and p_b[j] > 0:
                mi_actual += hist_2d[i, j] * np.log(
                    hist_2d[i, j] / (p_a[i] * p_b[j])
                )

    # 残差互信息
    rmi = max(0.0, mi_actual - mi_gaussian)

    # 归一化得分：RMI 除以总 MI，得到非线性成分占比
    rmi_score = rmi / max(0.001, mi_actual) if mi_actual > 0.001 else 0.0

    return {
        "mi_actual": round(float(mi_actual), 6),
        "mi_gaussian": round(float(mi_gaussian), 6),
        "rmi": round(float(rmi), 6),
        "rmi_score": round(float(rmi_score), 6),
    }


# ============================================================
# 综合评分（支持关联模式 + 残差互信息模式）
# ============================================================

def compute_combined_score(scores: dict, mode: str = "correlation") -> float:
    """
    根据模式计算最终联动得分。

    mode="correlation": 纯五维度联动（默认）
    mode="rmi": 五维度联动 + 残差互信息加成
    """
    total = (
        DEFAULT_WEIGHTS["direction_match"] * scores["direction_match"]
        + DEFAULT_WEIGHTS["return_corr"] * ((scores["return_corr"] + 1.0) / 2.0)
        + DEFAULT_WEIGHTS["shape_sim"] * scores["shape_sim"]
        + DEFAULT_WEIGHTS["r_squared"] * scores["r_squared"]
        + DEFAULT_WEIGHTS["roll_stability"] * scores["roll_stability"]
    )

    if mode == "rmi" and "rmi_score" in scores:
        # RMI 加成：隐藏关系额外加分，最高 +0.15
        rmi_bonus = min(0.15, scores["rmi_score"] * 0.15)
        total = total * (1.0 + rmi_bonus)

    return total


def rolling_correlation_stability(
    returns_a: np.ndarray,
    returns_b: np.ndarray,
    window: int = 20,
) -> tuple[float, float]:
    """
    滚动相关系数的均值和稳定性。

    Returns:
        mean:     滚动窗口平均相关系数
        stability: 1 - std(rolling_corrs)（越接近 1 越稳定）
    """
    if len(returns_a) != len(returns_b) or len(returns_a) < window:
        return 0.0, 0.0
    n = len(returns_a)
    cors = []
    for i in range(n - window + 1):
        if np.std(returns_a[i:i+window]) < 1e-10 or np.std(returns_b[i:i+window]) < 1e-10:
            continue
        c = np.corrcoef(returns_a[i:i+window], returns_b[i:i+window])[0, 1]
        if not np.isnan(c):
            cors.append(c)
    if not cors:
        return 0.0, 0.0
    arr = np.array(cors)
    return float(arr.mean()), float(max(0.0, 1.0 - min(arr.std(), 1.0)))


# ============================================================
# 综合评分
# ============================================================

# 五维度默认权重
DEFAULT_WEIGHTS = {
    "direction_match": 0.25,
    "return_corr": 0.25,
    "shape_sim": 0.20,
    "r_squared": 0.20,
    "roll_stability": 0.10,
}


def compute_linkage_scores(
    target_returns: np.ndarray,
    target_close: np.ndarray,
    candidate_returns: np.ndarray,
    candidate_close: np.ndarray,
    half_life: int = 60,
    compute_rmi: bool = False,
    market_returns: np.ndarray | None = None,
) -> dict:
    """
    计算单只候选股票的全部五维度联动指标。
    调用方需确保数据 newest_first（最新在 index=0），与 _decay_weights 对齐。
    可选计算残差互信息 (RMI)。

    Args:
        market_returns: 可选，等权市场平均收益（与 returns 同样长度/方向）。
                        传入后将对板块收益做大盘剥离：resid = return - market。
                        避免大盘共振被误判为板块联动。
    """
    tr = np.asarray(target_returns)
    cr = np.asarray(candidate_returns)
    tc = np.asarray(target_close)
    cc = np.asarray(candidate_close)

    # ── 大盘剥离（残差联动）──
    if market_returns is not None:
        mr = np.asarray(market_returns)
        min_len = min(len(tr), len(cr), len(mr))
        tr = tr[:min_len] - mr[:min_len]
        cr = cr[:min_len] - mr[:min_len]
        tc = tc[:min_len]
        cc = cc[:min_len]

    scores = {}

    # 1. 方向一致率（顺序无关）
    scores["direction_match"] = direction_match_rate(tr, cr)

    # 2. 加权收益相关（依赖正确顺序：最新在前）
    scores["return_corr"] = weighted_return_correlation(tr, cr, half_life)

    # 3. 累计收益形态相似度（顺序无关，corrcoef 不依赖顺序）
    scores["shape_sim"] = cumulative_return_similarity(tc, cc)

    # 4. 联动 Beta + R²（顺序无关）
    beta, r2 = co_movement_beta_r2(tr, cr)
    scores["beta"] = beta
    scores["r_squared"] = r2

    # 5. 滚动相关稳定性（顺序无关）
    roll_mean, roll_stability = rolling_correlation_stability(tr, cr)
    scores["roll_mean"] = roll_mean
    scores["roll_stability"] = roll_stability

    # 6. 残差互信息（可选）
    if compute_rmi:
        rmi_result = residual_mutual_information(tr, cr)
        scores.update(rmi_result)

    # 综合得分
    total = (
        DEFAULT_WEIGHTS["direction_match"] * scores["direction_match"]
        + DEFAULT_WEIGHTS["return_corr"] * ((scores["return_corr"] + 1.0) / 2.0)
        + DEFAULT_WEIGHTS["shape_sim"] * scores["shape_sim"]
        + DEFAULT_WEIGHTS["r_squared"] * scores["r_squared"]
        + DEFAULT_WEIGHTS["roll_stability"] * scores["roll_stability"]
    )
    scores["total_score"] = total

    return scores


# ============================================================
# 批量扫描
# ============================================================

def find_linked_stocks(
    target_code: str,
    candidate_codes: list[str],
    get_daily_fn,
    half_life: int = 60,
    top_n: int = 20,
    quick_keep_ratio: float = 0.25,
    lookback_days: int = 120,
    compute_rmi: bool = False,
    progress_callback=None,
    detrend: bool = False,
) -> pd.DataFrame:
    """
    批量扫描候选池，返回联动得分最高的 top_n 只股票。

    Args:
        target_code:      目标股票代码（如 "002815"）
        candidate_codes:  候选股票代码列表
        get_daily_fn:     日线获取函数，签名 (code) -> DataFrame(columns=date,open,high,low,close,volume)
        half_life:        时间加权半衰期
        top_n:            返回前 N 只
        quick_keep_ratio: 快筛保留比例
        progress_callback: 可选，(current, total) -> None
        detrend:          是否做大盘剥离（等权平均所有候选收益），避免大盘共振伪联动

    Returns:
        DataFrame，列: code, name, total_score, direction_match, return_corr,
                      shape_sim, r_squared, beta, roll_stability
    """
    # ── 目标股票数据 ──
    t_df = get_daily_fn(target_code)
    if t_df.empty or "close" not in t_df.columns or "date" not in t_df.columns:
        return pd.DataFrame()
    t_df = t_df.sort_values("date")  # 确保日期升序

    n_candidates = len(candidate_codes)

    # ── Phase 1: 快筛 ──
    quick_scores = []
    for i, code in enumerate(candidate_codes):
        if progress_callback and i % 50 == 0:
            progress_callback(i, n_candidates, "快筛中...")

        s_df = get_daily_fn(code)
        if s_df.empty or "close" not in s_df.columns or "date" not in s_df.columns:
            continue
        s_df = s_df.sort_values("date")

        # 对齐共同日期
        common = t_df[["date"]].merge(s_df[["date"]], on="date", how="inner")
        if len(common) < 20:
            continue

        # 目标股最近 N 天的交易日，候选股在这些天里有多少重叠
        t_recent_dates = t_df["date"].iloc[-lookback_days:]
        overlap_dates = set(t_recent_dates) & set(s_df["date"])
        overlap_count = len(overlap_dates)

        # 重叠太少则跳过
        min_overlap = max(20, lookback_days // 4)  # 至少 25% 或 20 天
        if overlap_count < min_overlap:
            continue

        t_aligned = t_df[t_df["date"].isin(overlap_dates)].sort_values("date")
        s_aligned = s_df[s_df["date"].isin(overlap_dates)].sort_values("date")

        t_ret = t_aligned["close"].pct_change().dropna().values
        s_ret = s_aligned["close"].pct_change().dropna().values
        t_ret = np.nan_to_num(t_ret, nan=0.0)
        s_ret = np.nan_to_num(s_ret, nan=0.0)

        # 快筛：最近 60 天
        ml = min(60, len(t_ret))
        dm_q = direction_match_rate(t_ret[-ml:], s_ret[-ml:])
        sc_q = _simple_corr(t_ret[-ml:], s_ret[-ml:])
        # 重叠越高越可靠，融入快筛得分
        overlap_bonus = overlap_count / lookback_days
        quick_scores.append((code, dm_q * 0.4 + max(0.0, sc_q) * 0.4 + overlap_bonus * 0.2,
                             t_aligned, s_aligned, overlap_count))

    if not quick_scores:
        return pd.DataFrame()

    quick_scores.sort(key=lambda x: x[1], reverse=True)
    keep_n = max(top_n * 2, int(len(quick_scores) * quick_keep_ratio))
    keep_entries = quick_scores[:keep_n]

    # ── Phase 2: 精算 ──
    full_results = []

    # 大盘剥离：计算候选池等权平均收益
    market_returns = None
    if detrend and keep_entries:
        all_rets = []
        min_len = float("inf")
        for _, _, t_aligned, s_aligned, _ in keep_entries:
            t_close = t_aligned["close"].values
            s_close = s_aligned["close"].values
            t_r = np.diff(t_close) / t_close[:-1]
            s_r = np.diff(s_close) / s_close[:-1]
            avg_r = (np.nan_to_num(t_r, nan=0.0) + np.nan_to_num(s_r, nan=0.0)) / 2.0
            all_rets.append(avg_r[::-1])  # newest-first
            min_len = min(min_len, len(avg_r))
        if all_rets and min_len >= 20:
            # Equal-weight market average
            trimmed = [r[:min_len] for r in all_rets]
            market_returns = np.mean(trimmed, axis=0)

    for i, (code, _, t_aligned, s_aligned, common_days) in enumerate(keep_entries):
        if progress_callback and i % 10 == 0:
            progress_callback(i, len(keep_entries), "精算中...")

        t_close = t_aligned["close"].values
        s_close = s_aligned["close"].values
        t_ret = np.diff(t_close) / t_close[:-1]
        s_ret = np.diff(s_close) / s_close[:-1]
        t_ret = np.nan_to_num(t_ret, nan=0.0)
        s_ret = np.nan_to_num(s_ret, nan=0.0)

        # 旧→新 反转为 新→旧，确保 decay_weights 最大权重落在最新数据
        scores = compute_linkage_scores(
            t_ret[::-1], t_close[::-1],
            s_ret[::-1], s_close[::-1],
            half_life=half_life,
            compute_rmi=compute_rmi,
            market_returns=market_returns,
        )
        scores["code"] = code
        scores["common_days"] = common_days
        # 重叠率加分：重叠越多越可靠
        overlap_ratio = common_days / lookback_days if lookback_days else 0
        scores["total_score"] = scores["total_score"] * (0.7 + 0.3 * overlap_ratio)
        full_results.append(scores)

    if not full_results:
        return pd.DataFrame()

    df = pd.DataFrame(full_results)
    df = df.sort_values("total_score", ascending=False).head(top_n)
    return df.reset_index(drop=True)
