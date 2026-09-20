"""
板块级别联动分析

基于板块成分股等权合成板块收益率，复用 linkage_analysis 五维度引擎
计算板块间联动关系。用于板块联动网络可视化和板块轮动分析。

数据来源：
- 板块-成分股映射: sector_library SQLite 数据库
- 个股日线数据: tdx_data provider（通过调用方注入 get_daily_fn）

注意：networkx 为可选依赖，仅在构建联动网络/联动表格时懒加载。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple, Callable
from collections import defaultdict


# ── 板块收益率合成 ──────────────────────────────────────────

def build_sector_returns_from_stocks(
    sector_names: List[str],
    get_daily_fn: Callable,
    lookback: int = 120,
    min_stocks: int = 3,
    progress_callback: Callable = None,
) -> Dict[str, Dict[str, np.ndarray]]:
    """
    通过成分股等权平均合成板块日收益率 + 合成收盘价。

    对每个板块：
    1. 从 sector_library 获取成分股
    2. 逐股读取日线，提取 pct 和 close
    3. 等权平均所有成分股的日收益率 → 板块收益率
    4. 基于收益率反向合成板块"归一化收盘价"（从1.0开始累计）

    Args:
        sector_names: 板块名称列表
        get_daily_fn: 日线数据获取函数 (code) -> DataFrame
                      返回的 DataFrame 需包含 date, pct, close 列
        lookback: 回溯天数
        min_stocks: 最小有效成分股数（低于此数的板块被跳过）
        progress_callback: 进度回调 (current, total)

    Returns:
        {sector_name: {
            "returns": np.ndarray (newest_first, 小数形式),
            "close": np.ndarray (newest_first, 归一化),
            "stock_count": int,
        }}
    """
    from sector_library import get_db
    db = get_db(auto_init=False)

    result = {}
    total = len(sector_names)

    for idx, name in enumerate(sector_names):
        if progress_callback:
            progress_callback(idx, total)

        stocks = db.get_stocks_in_sector(name)
        if len(stocks) < min_stocks:
            continue

        # 收集所有成分股的日收益率（从 close 计算 pct_change）
        all_returns = []
        all_closes = []
        valid_codes = []

        for code in stocks:
            try:
                df = get_daily_fn(code)
                if df is None or df.empty:
                    continue
                if 'close' not in df.columns:
                    continue

                # 统一处理：DatetimeIndex → date 列
                if isinstance(df.index, pd.DatetimeIndex):
                    df = df.reset_index().rename(columns={'index': 'date'})

                # 按日期排序，取最近 lookback 条
                if 'date' in df.columns:
                    df = df.sort_values('date', ascending=False)
                recent = df.head(lookback)

                if len(recent) < lookback // 2:
                    continue

                # 从 close 计算日收益率
                close_vals = recent['close'].values.astype(np.float64)
                # newest_first: 最新在前，所以 pct = (close[i] - close[i+1]) / close[i+1]
                pct_values = np.zeros(len(close_vals))
                pct_values[:-1] = (close_vals[:-1] - close_vals[1:]) / close_vals[1:]

                if len(pct_values) >= lookback // 2:
                    all_returns.append(pct_values)
                    all_closes.append(close_vals)
                    valid_codes.append(code)
            except Exception:
                continue

        if len(valid_codes) < min_stocks:
            continue

        # 对齐长度（取最短的）
        min_len = min(len(r) for r in all_returns)
        aligned_returns = np.array([r[:min_len] for r in all_returns])
        aligned_closes = np.array([c[:min_len] for c in all_closes])

        # 等权平均（newest_first）
        avg_returns = np.mean(aligned_returns, axis=0)
        avg_close = np.mean(aligned_closes, axis=0)

        result[name] = {
            "returns": avg_returns,
            "close": avg_close,
            "stock_count": len(valid_codes),
        }

    return result


# ── 板块联动网络构建 ────────────────────────────────────────

def build_sector_linkage_network(
    sector_data: Dict[str, Dict[str, np.ndarray]],
    half_life: int = 60,
    min_score: float = 0.70,
    top_k: int = 10,
    compute_rmi: bool = False,
    progress_callback: Callable = None,
) -> nx.Graph:
    """
    全配对计算板块间联动，构建 NetworkX 图。

    委托给 linkage_analysis.compute_linkage_scores 做五维度计算，
    确保与个股联动使用完全一致的算法。

    Args:
        sector_data: build_sector_returns_from_stocks 的输出
        half_life: 加权相关的半衰期
        min_score: 最小 total_score 阈值
        top_k: 每个板块最多保留 K 条最强边
        compute_rmi: 是否计算残差互信息
        progress_callback: 进度回调 (current, total)

    Returns:
        NetworkX Graph，节点=板块名，边含 total_score 等属性
    """
    import networkx as nx  # 懒加载：仅在构建联动网络时依赖

    from linkage_analysis import compute_linkage_scores

    sector_names = list(sector_data.keys())
    n = len(sector_names)
    total_pairs = n * (n - 1) // 2

    # ── 大盘剥离：等权市场平均收益 ──
    market_returns = None
    if n >= 3:
        all_returns = [np.asarray(sector_data[name]["returns"]) for name in sector_names]
        min_common = min(len(r) for r in all_returns)
        if min_common >= 10:
            trimmed = [r[:min_common] for r in all_returns]
            market_returns = np.mean(trimmed, axis=0)

    G = nx.Graph()

    # 添加节点
    for name in sector_names:
        sd = sector_data[name]
        G.add_node(
            name,
            stock_count=sd.get("stock_count", 0),
            label=name,
        )

    # 收集所有边的得分
    all_edges = []
    pair_idx = 0

    for i in range(n):
        name_a = sector_names[i]
        data_a = sector_data[name_a]
        ra = data_a["returns"]
        ca = data_a["close"]

        for j in range(i + 1, n):
            name_b = sector_names[j]
            data_b = sector_data[name_b]
            rb = data_b["returns"]
            cb = data_b["close"]

            # 对齐长度
            min_len = min(len(ra), len(rb), len(ca), len(cb))
            if min_len < 10:
                pair_idx += 1
                continue

            try:
                scores = compute_linkage_scores(
                    target_returns=ra[-min_len:],
                    target_close=ca[-min_len:],
                    candidate_returns=rb[-min_len:],
                    candidate_close=cb[-min_len:],
                    half_life=half_life,
                    compute_rmi=compute_rmi,
                    market_returns=market_returns[-min_len:] if market_returns is not None else None,
                )
            except Exception:
                pair_idx += 1
                continue

            if scores.get("total_score", 0) >= min_score:
                all_edges.append((name_a, name_b, scores))

            pair_idx += 1
            if progress_callback and pair_idx % 50 == 0:
                progress_callback(pair_idx, total_pairs)

    # 按 total_score 排序，每个节点保留 top_k 条边
    all_edges.sort(key=lambda x: x[2]["total_score"], reverse=True)

    node_edge_count = defaultdict(int)
    for name_a, name_b, scores in all_edges:
        if node_edge_count[name_a] >= top_k or node_edge_count[name_b] >= top_k:
            continue

        edge_attrs = {
            "weight": scores["total_score"],
            "direction_match": scores.get("direction_match", 0),
            "return_corr": scores.get("return_corr", 0),
            "shape_sim": scores.get("shape_sim", 0),
            "r_squared": scores.get("r_squared", 0),
            "roll_stability": scores.get("roll_stability", 0),
            "total_score": scores["total_score"],
        }
        # 如果计算了 RMI，也存到边上（补涨检测需要）
        if "rmi" in scores:
            edge_attrs["rmi"] = scores.get("rmi", 0)
            edge_attrs["rmi_score"] = scores.get("rmi_score", 0)
            edge_attrs["mi_actual"] = scores.get("mi_actual", 0)
            edge_attrs["mi_gaussian"] = scores.get("mi_gaussian", 0)

        G.add_edge(name_a, name_b, **edge_attrs)
        node_edge_count[name_a] += 1
        node_edge_count[name_b] += 1

    return G


# ── 板块联动表格 ────────────────────────────────────────────

def build_sector_linkage_table(G: nx.Graph) -> pd.DataFrame:
    """
    从 NetworkX 图中提取板块联动关系表。

    Returns:
        DataFrame with columns: 板块A, 板块B, 联动得分, 方向一致率, 收益相关, 形态相似, R², 稳定性
    """
    rows = []
    for u, v, data in G.edges(data=True):
        rows.append({
            "板块A": u,
            "板块B": v,
            "联动得分": round(data.get("total_score", 0), 4),
            "方向一致率": round(data.get("direction_match", 0), 4),
            "收益相关": round(data.get("return_corr", 0), 4),
            "形态相似": round(data.get("shape_sim", 0), 4),
            "R²": round(data.get("r_squared", 0), 4),
            "稳定性": round(data.get("roll_stability", 0), 4),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("联动得分", ascending=False).reset_index(drop=True)
    return df


# ── 股票-板块关联查询 ───────────────────────────────────────

def get_sector_linkage_for_stock(stock_code: str,
                                 top_n: int = 10) -> List[Tuple[str, float]]:
    """
    获取与某只股票关联最强的板块（基于该股票所属板块的权重排序）。

    Args:
        stock_code: 股票代码
        top_n: 返回前 N 个最相关的板块

    Returns:
        [(板块名, 权重), ...]
    """
    try:
        from sector_library import get_db
        db = get_db(auto_init=False)
        stock_sectors = db.get_sectors_for_stock(stock_code)

        related_sectors = defaultdict(float)
        for sector_name in stock_sectors:
            sector = db.get_sector(sector_name)
            if sector:
                weight = 1.5 if sector['sector_type'] == '概念' else 1.0
                related_sectors[sector_name] += weight

        sorted_sectors = sorted(related_sectors.items(),
                                key=lambda x: x[1], reverse=True)
        return sorted_sectors[:top_n]

    except Exception as e:
        print(f"[WARN] get_sector_linkage_for_stock failed: {e}")
        return []


def get_peer_stocks_in_same_sectors(stock_code: str,
                                    get_daily_fn: Callable = None,
                                    top_n: int = 20,
                                    half_life: int = 60,
                                    lookback: int = 120) -> List[Tuple[str, float, str]]:
    """
    获取与指定股票同板块的联动股。

    1. 查询股票所属板块
    2. 收集同板块的所有成分股
    3. 如果提供了 get_daily_fn，计算联动得分排序

    Args:
        stock_code: 输入股票代码
        get_daily_fn: 日线获取函数（可选，提供则计算联动）
        top_n: 返回数量
        half_life: 加权半衰期
        lookback: 回看窗口

    Returns:
        [(股票代码, 联动得分或0, 所属板块名), ...]
    """
    try:
        from sector_library import get_db
        db = get_db(auto_init=False)

        sectors = db.get_sectors_for_stock(stock_code)
        if not sectors:
            return []

        # 收集同板块的所有股票
        peer_stocks = set()
        stock_sector_map = defaultdict(list)
        for sector_name in sectors:
            sector_stocks = db.get_stocks_in_sector(sector_name)
            for s in sector_stocks:
                if s != stock_code:
                    peer_stocks.add(s)
                    stock_sector_map[s].append(sector_name)

        if not peer_stocks:
            return []

        # 如果提供了日线函数，计算联动得分
        if get_daily_fn:
            from linkage_analysis import compute_linkage_scores

            # 获取目标股票收益率（从 close 计算）
            df_target = get_daily_fn(stock_code)
            if df_target is not None and not df_target.empty:
                if isinstance(df_target.index, pd.DatetimeIndex):
                    df_target = df_target.reset_index().rename(columns={'index': 'date'})
                if 'date' in df_target.columns:
                    df_target = df_target.sort_values('date', ascending=False)
                df_target = df_target.head(lookback)
                target_close = df_target['close'].values.astype(np.float64)
                target_ret = np.zeros(len(target_close))
                target_ret[:-1] = (target_close[:-1] - target_close[1:]) / target_close[1:]

                scored = []
                for peer in peer_stocks:
                    try:
                        df_peer = get_daily_fn(peer)
                        if df_peer is None or df_peer.empty:
                            continue
                        if isinstance(df_peer.index, pd.DatetimeIndex):
                            df_peer = df_peer.reset_index().rename(columns={'index': 'date'})
                        if 'date' in df_peer.columns:
                            df_peer = df_peer.sort_values('date', ascending=False)
                        df_peer = df_peer.head(lookback)
                        peer_close = df_peer['close'].values.astype(np.float64)
                        peer_ret = np.zeros(len(peer_close))
                        peer_ret[:-1] = (peer_close[:-1] - peer_close[1:]) / peer_close[1:]

                        min_len = min(len(target_ret), len(peer_ret))
                        if min_len < 10:
                            continue

                        scores = compute_linkage_scores(
                            target_returns=target_ret[:min_len],
                            target_close=target_close[:min_len],
                            candidate_returns=peer_ret[:min_len],
                            candidate_close=peer_close[:min_len],
                            half_life=half_life,
                        )
                        scored.append((peer, scores["total_score"]))
                    except Exception:
                        continue

                scored.sort(key=lambda x: x[1], reverse=True)
                result = []
                for peer, score in scored[:top_n]:
                    result.append((peer, score, ', '.join(stock_sector_map.get(peer, []))))
                return result

        # 无日线函数时，只按板块权重排序
        result = []
        for peer in list(peer_stocks)[:top_n]:
            result.append((peer, 0.0, ', '.join(stock_sector_map.get(peer, []))))
        return result

    except Exception as e:
        print(f"[WARN] get_peer_stocks_in_same_sectors failed: {e}")
        return []


# ── 板块指数读取（TDX .day 文件） ────────────────────────────

def load_tdx_sector_index_mapping(
    tdx_root: str = None,
    code_range: tuple = None,
    name_blacklist: set = None,
) -> Dict[str, str]:
    """
    从 tdxzs.cfg 加载板块名称→指数代码的映射。

    Args:
        tdx_root: 通达信根目录
        code_range: 指数代码范围过滤，如 (880505, 880978) 只取概念板块。
        name_blacklist: 黑名单板块名（风格/技术类非概念板块）

    Returns:
        {sector_name: index_code}
    """
    import os

    if tdx_root is None:
        tdx_root = os.environ.get('TDX_ROOT', 'D:/program/通达信')

    cfg_path = os.path.join(tdx_root, 'T0002', 'hq_cache', 'tdxzs.cfg')
    if not os.path.exists(cfg_path):
        return {}

    mapping = {}
    with open(cfg_path, 'rb') as f:
        data = f.read()

    text = data.decode('gbk', errors='replace')
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split('|')
        if len(parts) >= 2:
            name = parts[0].strip()
            code = parts[1].strip()
            if code.isdigit() and len(code) == 6 and name:
                if code_range:
                    code_int = int(code)
                    if not (code_range[0] <= code_int <= code_range[1]):
                        continue
                if name_blacklist and name in name_blacklist:
                    continue
                mapping[name] = code

    return mapping


def build_sector_returns_from_index(
    sector_names: List[str],
    index_mapping: Dict[str, str] = None,
    tdx_root: str = None,
    lookback: int = 120,
    min_days: int = 30,
    progress_callback: Callable = None,
) -> Dict[str, Dict[str, np.ndarray]]:
    """
    通过读取 TDX 板块指数 .day 文件，获取板块收益率序列。

    相比成分股合成方案，板块指数由 TDX 官方维护，
    不存在成分股重叠导致的伪相关问题。

    Args:
        sector_names: 板块名称列表（来自 tdxzs.cfg）
        index_mapping: sector_name → index_code 映射（None=自动加载）
        tdx_root: 通达信根目录
        lookback: 回溯天数
        min_days: 最少有效天数（低于此数的板块跳过）
        progress_callback: 进度回调

    Returns:
        {sector_name: {"returns": np.ndarray (newest_first),
                        "close": np.ndarray (newest_first)}}
    """
    import os
    from data_utils import read_tdx_day_fast

    if tdx_root is None:
        tdx_root = os.environ.get('TDX_ROOT', 'D:/program/通达信')

    if index_mapping is None:
        index_mapping = load_tdx_sector_index_mapping(tdx_root)

    result = {}
    total = len(sector_names)
    lday_dir = os.path.join(tdx_root, 'vipdoc', 'sh', 'lday')

    for idx, name in enumerate(sector_names):
        if progress_callback:
            progress_callback(idx, total)

        code = index_mapping.get(name)
        if not code:
            continue

        day_file = os.path.join(lday_dir, f'sh{code}.day')
        if not os.path.exists(day_file):
            continue

        try:
            df = read_tdx_day_fast(day_file)
            if df is None or df.empty or 'close' not in df.columns:
                continue

            # 按日期排序，取最近 lookback 条
            if 'date' in df.columns:
                df = df.sort_values('date', ascending=False)

            recent = df.head(lookback)
            if len(recent) < min_days:
                continue

            close_vals = recent['close'].values.astype(np.float64)

            # 计算日收益率（newest_first）
            returns = np.zeros(len(close_vals))
            returns[:-1] = (close_vals[:-1] - close_vals[1:]) / close_vals[1:]

            result[name] = {
                "returns": returns,
                "close": close_vals,
            }
        except Exception:
            continue

    return result


# ── 板块轮动热力图 ──────────────────────────────────────────

def get_sector_rotation_heatmap(
    sector_data: Dict[str, Dict[str, np.ndarray]],
    window: int = 5,
) -> pd.DataFrame:
    """
    生成板块轮动热力图数据。

    计算每个板块在滑动窗口内的超额收益（相对全板块均值）。

    Args:
        sector_data: build_sector_returns_from_stocks 的输出
        window: 滑动窗口大小（天）

    Returns:
        DataFrame: 行=板块, 列=时间窗口, 值=超额收益
    """
    if not sector_data:
        return pd.DataFrame()

    # 找到公共最小长度
    min_len = min(
        len(sd["returns"])
        for sd in sector_data.values()
    )
    if min_len < window:
        return pd.DataFrame()

    rows = []
    index_names = []
    for name, sd in sector_data.items():
        rets = sd["returns"][:min_len]  # newest_first
        if len(rets) < window:
            continue
        # 滑动窗口累计收益
        rolling = pd.Series(rets).rolling(window).apply(
            lambda x: np.prod(1 + x) - 1
        ).values
        rows.append(rolling)
        index_names.append(name)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, index=index_names)
    # 减去每列均值得到超额收益
    df = df.subtract(df.mean(axis=0), axis=1)
    return df
