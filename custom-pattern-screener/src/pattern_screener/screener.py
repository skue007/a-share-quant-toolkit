"""全市场形态扫描 — 单股五维滑动窗口匹配 + 并发执行。

逻辑与原版 screen_stock / get_stock_list 一致，额外支持:
- limit 参数: 限制扫描股票数量（演示/调试用，默认 None = 全市场）
"""

from __future__ import annotations

import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from .features import (
    normalize_price,
    normalize_volume,
    calc_body_ratio,
    calc_upper_shadow,
    calc_daily_return,
    rolling_mean,
    pearson_corr,
    DIMENSIONS,
)

# 仅扫描 A 股主板/创业板/科创板
VALID_A_SHARE_PREFIXES = [
    "000", "001", "002", "003",
    "600", "601", "603", "605",
    "688", "300", "301",
]
NAME_BLACKLIST = ["ST", "退"]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def screen_stock(provider, row: dict, templates: dict, config: dict,
                 reference_date: str | None = None, pad_days: int = 20) -> dict | None:
    """对单只股票做5维滑动窗口扫描，返回最佳（最晚）匹配。

    reference_date: 可选，回测时指定历史日期（格式 'YYYY-MM-DD'），不传则用今天。
    """
    code = row["code"]
    name = row.get("name", "")

    target_len = templates["target_len"]
    lookback = config["lookback_days"]
    threshold = config["similarity_threshold"]

    weights = {dim: config[f"{dim}_weight"] for dim in DIMENSIONS}

    try:
        ref_dt = datetime.strptime(reference_date, "%Y-%m-%d") if reference_date else datetime.now()
        end_date = ref_dt.strftime("%Y-%m-%d")
        start_date = f"{ref_dt.year - 1}-01-01"
        df = provider.get_daily(code, start=start_date, end=end_date)
        if df is None or len(df) < target_len + pad_days:
            return None
    except Exception:
        return None

    close = df["close"].values.astype(np.float64)
    open_arr = df["open"].values.astype(np.float64)
    high = df["high"].values.astype(np.float64)
    low = df["low"].values.astype(np.float64)
    volume = df["volume"].values.astype(np.float64)
    amount = df["amount"].values.astype(np.float64) if "amount" in df.columns else None
    dates = list(df.index)
    date_strs = [
        str(d.date()) if hasattr(d, 'date') else str(d)[:10]
        for d in dates
    ]

    mask = (close > 0) & (volume >= 0) & (high > 0) & (low > 0)
    if mask.sum() < target_len + pad_days:
        return None
    close = close[mask]
    open_arr = open_arr[mask]
    high = high[mask]
    low = low[mask]
    volume = volume[mask]
    if amount is not None:
        amount = amount[mask]
    date_strs = [d for i, d in enumerate(date_strs) if mask[i]]

    # 截取最近 lookback_days + pad_days
    total_needed = min(lookback + pad_days, len(close))
    close = close[-total_needed:]
    open_arr = open_arr[-total_needed:]
    high = high[-total_needed:]
    low = low[-total_needed:]
    volume = volume[-total_needed:]
    if amount is not None:
        amount = amount[-total_needed:]
    date_strs = date_strs[-total_needed:]

    n = len(close)
    if n < target_len + pad_days:
        return None

    # 预计算 volume MA
    vol_ma = rolling_mean(volume, pad_days)

    # 只检查最后一个窗口（窗口结束日 = 最新交易日）
    i = n - target_len
    if i < pad_days - 1:
        return None

    # ── Trend pre-filter (configurable per group) ──
    tf = config.get("trend_filter", {})
    if tf.get("enabled"):
        min_ret = tf.get("min_return_20d", 0.10)
        require_ma = tf.get("require_ma5_above_ma10", True)
        if i >= 20:
            ret_20d = (close[i] / close[i - 20]) - 1.0
            if ret_20d < min_ret:
                return None
            if require_ma:
                ma5_win = rolling_mean(close[:i + target_len], 5)[i:]
                ma10_win = rolling_mean(close[:i + target_len], 10)[i:]
                for j in range(target_len):
                    if not np.isnan(ma5_win[j]) and not np.isnan(ma10_win[j]):
                        if ma5_win[j] <= ma10_win[j]:
                            return None
                    else:
                        return None
        else:
            return None

    win_close = close[i:i + target_len]
    if win_close.min() <= 0:
        return None
    if np.isnan(vol_ma[i:i + target_len]).any():
        return None

    try:
        price_norm = normalize_price(win_close)
    except (ValueError, ZeroDivisionError):
        return None

    # ── MA5 growth constraint ──
    ma5_growth = config.get("ma5_max_growth", 99.0)
    if ma5_growth < 99.0:
        ma5_full = rolling_mean(close[:i + target_len], 5)
        ma5_peak = ma5_full[-2]   # day before match (pre-crash peak)
        ma5_4d = ma5_full[-6] if len(ma5_full) >= 6 else np.nan  # 4 days before peak
        if not np.isnan(ma5_peak) and not np.isnan(ma5_4d):
            if ma5_4d > 0 and (ma5_peak / ma5_4d - 1.0) > ma5_growth:
                return None

    # ── Pre-crash price stability ──
    pre_crash_max_drop = config.get("pre_crash_max_drop", 0.99)
    if pre_crash_max_drop < 0.99 and i >= 4:
        close_before = close[i + target_len - 2]   # 大阴线前1天
        close_4d_ago = close[i + target_len - 5]   # 大阴线前4天
        if close_4d_ago > 0:
            drop = close_before / close_4d_ago - 1.0
            if drop < -pre_crash_max_drop:
                return None

    # ── Minimum trading amount filter (亿元) ──
    min_amount = config.get("min_amount", 0)
    if min_amount > 0 and amount is not None:
        # 窗口最后一天的成交额
        last_amount = amount[i + target_len - 1]
        if last_amount < min_amount * 1e8:
            return None

    vol_norm = normalize_volume(volume[i:i + target_len], vol_ma[i:i + target_len])
    body_raw = calc_body_ratio(open_arr[i:i + target_len], high[i:i + target_len],
                                low[i:i + target_len], win_close)
    shadow_raw = calc_upper_shadow(open_arr[i:i + target_len], high[i:i + target_len],
                                    low[i:i + target_len], win_close)
    ret_raw = calc_daily_return(win_close)

    pc = pearson_corr(price_norm, templates["price_template"])
    vc = pearson_corr(vol_norm, templates["volume_template"])
    bc = pearson_corr(body_raw, templates["body_template"])
    sc = pearson_corr(shadow_raw, templates["shadow_template"])
    rc = pearson_corr(ret_raw, templates["return_template"])

    combined = (weights["price"] * pc + weights["volume"] * vc +
                 weights["body"] * bc + weights["shadow"] * sc +
                 weights["return"] * rc)

    if combined <= threshold:
        return None

    return {
        "code": code,
        "name": name,
        "match_date": date_strs[-1],
        "score": round(combined, 4),
        "price_corr": round(pc, 4),
        "volume_corr": round(vc, 4),
        "body_corr": round(bc, 4),
        "shadow_corr": round(sc, 4),
        "return_corr": round(rc, 4),
        "current_price": f"{close[-1]:.2f}",
        "amount_亿": round(amount[i + target_len - 1] / 1e8, 2) if amount is not None else 0,
        "group_name": config.get("_group_name", ""),
    }


def get_stock_list(provider, limit: int | None = None):
    """获取全A股列表并预筛选。

    limit: 限制返回数量（按代码排序后取前 N 只），用于演示/调试。
    """
    df = provider.get_stock_list()
    log(f"  原始标的: {len(df)} 只")

    df = df[df["code"].str[:3].isin(VALID_A_SHARE_PREFIXES)]
    log(f"  过滤非A股后: {len(df)} 只")

    for kw in NAME_BLACKLIST:
        df = df[~df["name"].str.contains(kw, na=False)]
    log(f"  排除ST/退市后: {len(df)} 只")

    df = df.sort_values("code").reset_index(drop=True)

    if limit and limit > 0:
        df = df.head(limit)
        log(f"  演示模式: 仅扫描前 {len(df)} 只 (--limit {limit})")

    return df


def run_scan(provider, stocks: list[dict], templates: dict, config: dict,
             max_workers: int = 16) -> list[dict]:
    """并发扫描全部股票，返回命中的匹配列表。"""
    total = len(stocks)
    if total == 0:
        log("没有通过预筛选的股票，退出。")
        return []

    log(f"[Phase 4] 扫描 {total} 只股票 (并发={max_workers})...")
    all_results = []
    completed = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(screen_stock, provider, row, templates, config): row
            for row in stocks
        }

        for future in as_completed(futures):
            completed += 1
            try:
                match = future.result()
                if match:
                    all_results.append(match)
            except Exception:
                pass

            if completed % 200 == 0 or completed == total:
                pct = round(completed / total * 100)
                elapsed = round(time.time() - start_time)
                rate = completed / elapsed if elapsed > 0 else 0
                eta = round((total - completed) / rate) if rate > 0 else 0
                log(f"  进度: {completed}/{total} ({pct}%) | "
                    f"命中: {len(all_results)} | {elapsed}s | ETA: {eta}s")

    return all_results
