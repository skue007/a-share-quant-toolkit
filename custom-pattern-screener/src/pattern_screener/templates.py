"""形态模板学习 — 从用户示例中提取五维归一化曲线并求平均模板。

逻辑与原版 build_templates / fetch_with_padding 一致：
1. 拉取示例股票 K 线（含 volume MA 所需前置天数）
2. 归一化出 5 维特征曲线（价格/量/实体/上影/日涨跌幅）
3. 重采样到统一长度后取平均 → 形态模板
4. 自检每个示例与模板的相关性
"""

from __future__ import annotations

import sys
from datetime import datetime

import numpy as np

from .features import (
    normalize_price,
    normalize_volume,
    resample_curve,
    calc_body_ratio,
    calc_upper_shadow,
    calc_daily_return,
    rolling_mean,
    pearson_corr,
    DIMENSIONS,
    DIM_LABELS,
)

PAD_DAYS = 20  # volume MA 计算所需前置天数


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def fetch_with_padding(provider, code: str, start_str: str, end_str: str):
    """获取K线数据，包含 volume MA 所需的前置20天。

    Returns dict(close/open/high/low/volume/dates/start_idx/end_idx) 或 None。
    """
    start_dt = datetime.strptime(start_str, "%Y-%m-%d")
    end_dt = datetime.strptime(end_str, "%Y-%m-%d")
    fetch_start = f"{start_dt.year - 1}-{start_dt.month:02d}-{start_dt.day:02d}"

    try:
        df = provider.get_daily(code, start=fetch_start, end=end_str)
        if df is None or len(df) < PAD_DAYS + 5:
            return None

        close = df["close"].values.astype(np.float64)
        open_arr = df["open"].values.astype(np.float64)
        high = df["high"].values.astype(np.float64)
        low = df["low"].values.astype(np.float64)
        volume = df["volume"].values.astype(np.float64)
        dates = list(df.index)

        mask = (close > 0) & (volume >= 0) & (high > 0) & (low > 0)
        if mask.sum() < PAD_DAYS + 5:
            return None

        close = close[mask]
        open_arr = open_arr[mask]
        high = high[mask]
        low = low[mask]
        volume = volume[mask]
        dates = [d for i, d in enumerate(dates) if mask[i]]

        # 定位目标日期范围在数据中的位置
        date_strs = [
            str(d.date()) if hasattr(d, 'date') else str(d)[:10]
            for d in dates
        ]
        try:
            start_idx = next(i for i, ds in enumerate(date_strs)
                             if ds >= start_str)
            end_idx = next(i for i, ds in enumerate(reversed(date_strs))
                           if ds <= end_str)
            end_idx = len(date_strs) - 1 - end_idx
        except StopIteration:
            return None

        if end_idx - start_idx < 2:
            return None

        # 确保有足够的 MA 前置数据
        if start_idx < PAD_DAYS:
            return None

        return {
            "close": close, "open": open_arr, "high": high, "low": low,
            "volume": volume,
            "start_idx": start_idx, "end_idx": end_idx,
            "dates": date_strs,
        }
    except Exception:
        return None


def build_templates(provider, config: dict) -> dict:
    """从示例中提取5维归一化曲线，重采样，平均 → 模板"""
    log("[Phase 2] 构建形态模板 (5维: 价格/量/实体/上影/日涨跌幅)...")
    examples = config["examples"]

    # 储存每个示例的5条特征曲线
    curves = {dim: [] for dim in DIMENSIONS}
    lengths = []
    valid_examples = []

    for i, ex in enumerate(examples):
        code, start_str, end_str = ex["code"], ex["start"], ex["end"]
        log(f"  示例 {i+1}: {code}  {start_str} ~ {end_str}")

        data = fetch_with_padding(provider, code, start_str, end_str)
        if data is None:
            log(f"    X 数据不足，跳过")
            continue

        si, ei = data["start_idx"], data["end_idx"]

        # 用示例区间之前的数据计算 volume MA
        vol_full = data["volume"]
        vol_ma = rolling_mean(vol_full, PAD_DAYS)

        # 截取目标区间
        target_close = data["close"][si:ei + 1]
        target_open = data["open"][si:ei + 1]
        target_high = data["high"][si:ei + 1]
        target_low = data["low"][si:ei + 1]
        target_vol = data["volume"][si:ei + 1]
        target_vol_ma = vol_ma[si:ei + 1]

        if len(target_close) != len(target_vol_ma):
            continue

        try:
            price_norm = normalize_price(target_close)
        except (ValueError, ZeroDivisionError):
            log(f"    X 价格归一化失败")
            continue

        vol_norm = normalize_volume(target_vol, target_vol_ma)
        body_raw = calc_body_ratio(target_open, target_high, target_low, target_close)
        shadow_raw = calc_upper_shadow(target_open, target_high, target_low, target_close)
        ret_raw = calc_daily_return(target_close)

        curves["price"].append(price_norm)
        curves["volume"].append(vol_norm)
        curves["body"].append(body_raw)
        curves["shadow"].append(shadow_raw)
        curves["return"].append(ret_raw)
        lengths.append(len(target_close))
        valid_examples.append(ex)

        log(f"    OK 长度={len(target_close)}  "
            f"价格=[{price_norm.min():+.1%},{price_norm.max():+.1%}]  "
            f"实体均值={body_raw.mean():.2f}  上影均值={shadow_raw.mean():.2f}")

    if len(valid_examples) == 0:
        log("错误: 没有有效的示例数据，无法构建模板")
        sys.exit(1)

    # 目标长度 = 中位数 (可被 config.window_days 覆盖)
    target_len = config.get("window_days") or int(np.median(lengths))
    log(f"  目标长度(中位数): {target_len} 天")
    if target_len < 2:
        log("错误: 模板长度至少需要2天")
        sys.exit(1)

    if len(valid_examples) == 1:
        log("  警告: 仅有一个示例，模板精度可能不足")

    if len(lengths) >= 2:
        std_len = float(np.std(lengths))
        if std_len > target_len * 0.5:
            log(f"  警告: 示例长度差异较大 (标准差={std_len:.1f})，模板可能不够精确")

    # 重采样所有5维曲线到统一长度 + 取平均
    templates = {}
    for dim in DIMENSIONS:
        resampled = [resample_curve(c, target_len) for c in curves[dim]]
        templates[dim] = np.mean(resampled, axis=0)
        # keep resampled for self-check
        curves[f"{dim}_resampled"] = resampled

    # 自检: 每个示例5维 vs 模板
    weights = {dim: config[f"{dim}_weight"] for dim in DIMENSIONS}

    log("\n  自检 (各示例 vs 模板):")
    for i in range(len(valid_examples)):
        parts = []
        combined = 0.0
        for dim in DIMENSIONS:
            c = pearson_corr(curves[f"{dim}_resampled"][i], templates[dim])
            parts.append(f"{DIM_LABELS[dim]}_corr={c:.3f}")
            combined += weights[dim] * c
        flag = " !! 低相关!" if combined < 0.7 else ""
        log(f"    {valid_examples[i]['code']}  {'  '.join(parts)}  combined={combined:.3f}{flag}")

    return {
        "price_template": templates["price"],
        "volume_template": templates["volume"],
        "body_template": templates["body"],
        "shadow_template": templates["shadow"],
        "return_template": templates["return"],
        "target_len": target_len,
        "valid_count": len(valid_examples),
    }
