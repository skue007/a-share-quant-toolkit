"""
联动选股 (Linkage Stock Picker)

基于五维度联动分析，找出与目标股票（或板块指数）走势联动最紧密的标的。

指标：
  1. 方向一致率 — 同涨同跌占比（最直观）
  2. 收益相关 — 时间加权相关系数
  3. 形态相似 — 累计收益曲线相关度
  4. 联动 R²  — 目标对候选的解释力
  5. 稳定性   — 滚动相关是否持续
"""

import sys
import time
from pathlib import Path

import streamlit as st
import pandas as pd
import numpy as np

_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from tdx_data import get_provider
from linkage_analysis import find_linked_stocks
from industry_data import (
    get_stock_industry_map,
    get_target_industry,
    get_target_name,
    get_same_industry_stocks,
    get_sibling_industry_stocks,
    get_all_stocks_code,
)


# ============================================================
# Helpers
# ============================================================

def _color_for_rate(val: float) -> str:
    """根据方向一致率返回颜色。"""
    if val >= 0.80:
        return "color: #22c55e; font-weight: bold"  # green
    elif val >= 0.70:
        return "color: #eab308"  # yellow
    else:
        return "color: #94a3b8"  # gray


def _color_for_score(val: float) -> str:
    """根据联动得分返回颜色。"""
    if val >= 0.80:
        return "background-color: #166534; color: #fff"
    elif val >= 0.70:
        return "background-color: #1e40af; color: #fff"
    elif val >= 0.60:
        return ""
    else:
        return "color: #94a3b8"


# ============================================================
# Page config
# ============================================================

st.set_page_config(page_title="联动选股 — Linkage Stock Picker", page_icon="🔗", layout="wide")

st.title("🔗 联动选股")
st.caption("找出与目标股票走势联动最紧密的股票 — 基于五维度联动分析")

# ============================================================
# Analysis mode selector
# ============================================================

analysis_mode = st.radio(
    "分析模式",
    options=["个股联动", "板块指数联动"],
    index=0,
    horizontal=True,
    help="个股联动：输入股票代码找联动股票\n板块指数联动：选择板块指数找联动板块",
)

# ============================================================
# Sidebar controls
# ============================================================

# ── 板块指数联动模式：预加载板块列表 ──
_sector_names_all = []
if analysis_mode == "板块指数联动":
    import json as _json, os as _os

    # 优先使用 tdxzs_mapping.json（包含行业板块+概念板块，共 ~500 个）
    # 行业板块: 880400-880507（如 银行 880471、证券 880472）
    # 概念板块: 880508-880978（如 半导体 880548、AI 880661）
    _mapping_path = _os.path.join(_os.path.dirname(__file__), '..', '..', 'data', 'tdxzs_mapping.json')
    _concept_map = {}

    if _os.path.exists(_mapping_path):
        with open(_mapping_path, 'r', encoding='utf-8') as _f:
            _raw_list = _json.load(_f)
        # 过滤行业板块 + 概念板块 (排除地域/风格/技术类板块)
        for _name, _code in _raw_list:
            if _code.isdigit() and 880400 <= int(_code) <= 880978:
                _concept_map[_name] = _code
    else:
        from sector_library.sector_linkage import load_tdx_sector_index_mapping
        _concept_map = load_tdx_sector_index_mapping(code_range=(880400, 880978))

    _sector_names_all = sorted(_concept_map.keys())

with st.sidebar:
    st.subheader("📋 搜索设置")

    if analysis_mode == "板块指数联动":
        # ── 板块指数联动控件 ──
        if not _sector_names_all:
            st.error("无法加载板块指数列表，请检查 TDX 数据目录或 tdxzs_mapping.json")
            st.stop()

        target_sector = st.selectbox(
            "目标板块指数",
            options=_sector_names_all,
            index=None,
            placeholder="选择或搜索板块...",
            help="选择一个概念板块指数作为联动分析的基准",
        )

        # 可选：限制候选板块范围
        st.markdown("---")
        st.caption("可选：限制候选板块范围（默认全部概念板块）")

        custom_candidates = st.checkbox(
            "自定义候选板块",
            value=False,
            help="默认对所有概念板块计算联动。勾选后可手动指定候选板块。",
        )
        if custom_candidates:
            candidate_sectors = st.multiselect(
                "候选板块",
                options=[s for s in _sector_names_all],
                default=None,
                help="手动选择要参与联动计算的板块",
            )
        else:
            candidate_sectors = None  # None = use all
    else:
        # ── 个股联动控件（原有） ──
        target_code = st.text_input(
            "目标股票代码",
            value="",
            placeholder="如: 002815",
            help="输入6位A股代码",
        ).strip()

        search_scope = st.selectbox(
            "搜索范围",
            options=["同行业（CSRC）", "同CSRC大类（相邻子行业）", "同板块（概念板块）", "全市场"],
            index=0,
            help="同行业=同一证监会行业分类；同板块=同一通达信概念板块；全市场=全部A股",
        )

    # ── 共享参数 ──
    top_n = st.slider("返回数量", min_value=5, max_value=30, value=10, step=5)

    lookback_days = st.slider(
        "对齐天数（交易日）",
        min_value=40,
        max_value=500,
        value=120,
        step=20,
        help="仅对比最近N个交易日。120≈半年，252≈一年。越小越聚焦近期。",
    )

    half_life = st.slider(
        "时间权重半衰期（交易日）",
        min_value=20,
        max_value=120,
        value=60,
        step=10,
        help="越小近期权重越大。60 = 60天前数据权重是今天的一半。",
    )

    st.markdown("---")

    if analysis_mode == "个股联动":
        data_source = st.radio(
            "数据源",
            options=["TDX 本地", "baostock 网络"],
            index=0,
            help="TDX=本地通达信数据(快)；baostock=网络证券宝(慢但复权精确)。结果不一致时可切换对比。",
        )

    st.markdown("---")

    run_btn = st.button("🔍 开始搜索", type="primary", use_container_width=True)

    if analysis_mode == "个股联动":
        st.markdown("---")
        st.caption("行业分类: baostock CSRC")


# ============================================================
# Main area
# ============================================================

# ═══════════════════════════════════════════════════════════
# 板块指数联动模式
# ═══════════════════════════════════════════════════════════

if analysis_mode == "板块指数联动":
    if not target_sector:
        st.info("👈 在左侧选择目标板块指数，点击「开始搜索」")
        st.stop()

    if not run_btn:
        st.info("选择目标板块后，点击左侧「🔍 开始搜索」")
        st.stop()

    # ── 确定候选板块列表 ──
    if candidate_sectors:
        _scan_sectors = [target_sector] + [s for s in candidate_sectors if s != target_sector]
    else:
        _scan_sectors = [target_sector] + [s for s in _sector_names_all if s != target_sector]

    st.info(f"目标板块: **{target_sector}** | 候选板块: {len(_scan_sectors) - 1} 个")

    # ── Step 1: 加载板块指数数据（带缓存 + 进度条） ──
    from sector_library.sector_linkage import build_sector_returns_from_index
    from linkage_analysis import compute_linkage_scores

    @st.cache_data(ttl=600, show_spinner=False)
    def _load_sector_data_cached(sector_tuple: tuple, lookback: int) -> dict:
        """缓存板块指数数据的加载（参数需 hashable）。"""
        sector_list = list(sector_tuple)
        return build_sector_returns_from_index(
            sector_list,
            _concept_map,
            lookback=lookback,
            min_days=30,
        )

    progress_bar = st.progress(0)
    status_text = st.empty()

    # 加载（首次触发缓存，后续命中缓存）
    with st.spinner(f"正在读取板块指数数据（首次较慢 ~30s，后续使用缓存）... 共 {len(_scan_sectors)} 个板块"):
        sector_data = _load_sector_data_cached(tuple(sorted(_scan_sectors)), lookback_days)
    progress_bar.progress(1.0)

    valid_count = len(sector_data)
    status_text.text(f"有效板块指数: {valid_count} / {len(_scan_sectors)}")

    if target_sector not in sector_data:
        st.error(f"目标板块「{target_sector}」指数数据不足（需至少 30 个交易日），请选择其他板块")
        st.stop()

    target_data = sector_data[target_sector]
    target_ret = target_data["returns"]
    target_close = target_data["close"]

    # ── Step 2: 计算目标 vs 所有候选板块的联动得分 ──
    scan_start = time.time()
    progress_bar2 = st.progress(0)
    status_text2 = st.empty()

    candidates = [s for s in _scan_sectors if s != target_sector and s in sector_data]
    results = []
    total_candidates = len(candidates)

    for idx, cand_name in enumerate(candidates):
        cand_data = sector_data[cand_name]
        try:
            scores = compute_linkage_scores(
                target_returns=target_ret,
                target_close=target_close,
                candidate_returns=cand_data["returns"],
                candidate_close=cand_data["close"],
                half_life=half_life,
                compute_rmi=False,
            )
            scores["sector_name"] = cand_name
            results.append(scores)
        except Exception:
            pass

        if (idx + 1) % 50 == 0 or idx == total_candidates - 1:
            progress_bar2.progress((idx + 1) / max(total_candidates, 1))
            status_text2.text(f"联动计算中... {idx + 1}/{total_candidates}")

    scan_elapsed = time.time() - scan_start
    status_text2.text(f"完成: {len(results)} 条结果 / 耗时 {scan_elapsed:.1f}s")

    # ── 排序取 Top N ──
    results.sort(key=lambda x: x["total_score"], reverse=True)
    results = results[:top_n]

    st.markdown("---")

    if not results:
        st.warning("未找到有效的联动板块，请尝试增大回溯天数")
        st.stop()

    # ── Step 3: 结果表格 ──
    st.subheader(f"📊 板块联动搜索结果 (Top {len(results)})")

    # 构建 DataFrame
    df = pd.DataFrame(results)
    df.rename(columns={"sector_name": "板块名称"}, inplace=True)

    display_cols_map = {
        "板块名称": "板块名称",
        "total_score": "联动得分",
        "direction_match": "方向一致率",
        "return_corr": "收益相关",
        "shape_sim": "形态相似",
        "r_squared": "联动R2",
        "beta": "Beta",
        "roll_stability": "稳定性",
    }
    _display_keys = [k for k in display_cols_map if k in df.columns]
    display_df = df[_display_keys].rename(columns={k: display_cols_map[k] for k in _display_keys}).copy()

    # 格式化
    for col in ["联动得分", "收益相关", "形态相似", "联动R2", "稳定性"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(lambda x: f"{x:.4f}")
    if "方向一致率" in display_df.columns:
        display_df["方向一致率"] = display_df["方向一致率"].apply(lambda x: f"{x:.1%}")
    if "Beta" in display_df.columns:
        display_df["Beta"] = display_df["Beta"].apply(lambda x: f"{x:.3f}")

    display_df.index = range(1, len(display_df) + 1)
    display_df.index.name = "排名"

    st.dataframe(
        display_df,
        use_container_width=True,
        height=min(38 + len(display_df) * 35, 500),
    )

    # ── Step 4: 归一化叠加图 ──
    st.markdown("---")
    st.subheader("📈 板块指数叠加对比")

    _result_sectors = [r["sector_name"] for r in results]
    compare_sectors = st.multiselect(
        "选择要对比的板块（可选多个）",
        options=[target_sector] + _result_sectors,
        default=[target_sector] + _result_sectors[:min(3, len(_result_sectors))],
        help="选择后下方显示归一化价格叠加图",
    )

    if compare_sectors:
        chart_data = pd.DataFrame()
        for sec_name in compare_sectors:
            if sec_name in sector_data:
                sec_close = sector_data[sec_name]["close"]
                if len(sec_close) > 0:
                    # newest_first → reverse to chronological order for chart
                    norm = sec_close[::-1] / sec_close[::-1][0]
                    chart_data[sec_name] = norm

        if not chart_data.empty and len(chart_data.columns) >= 2:
            st.line_chart(
                chart_data,
                height=400,
                use_container_width=True,
            )
            st.caption("归一化价格曲线（起始=1.0）— 曲线越贴合说明联动越强")

    # ── Step 5: 指标说明 ──
    with st.expander("📖 指标说明"):
        st.markdown("""
        | 指标 | 权重 | 含义 | 优质阈值 |
        |------|------|------|---------|
        | **方向一致率** | 25% | 同涨同跌的交易日占比。80%=每5天有4天同向 | > 75% |
        | **收益相关** | 25% | 时间加权日收益率相关系数，近期权重更大 | > 0.60 |
        | **形态相似** | 20% | 累计收益曲线相关度，反映长期走势形态重合度 | > 0.85 |
        | **联动 R²** | 20% | 目标涨跌能解释候选多少比例波动，越高联动越紧密 | > 0.30 |
        | **稳定性** | 10% | 20日滚动相关是否持续稳定，排除虚假相关 | > 0.80 |
        | **Beta** | — | 联动弹性。1.0=目标涨1%候选涨1% | ~1.0 |
        """)

    st.stop()  # 板块模式到此结束，不执行后续个股逻辑


# ═══════════════════════════════════════════════════════════
# 个股联动模式（原有逻辑）
# ═══════════════════════════════════════════════════════════

if not target_code:
    st.info("👈 在左侧输入目标股票代码，点击「开始搜索」")
    st.stop()

# Ensure code format
target_code = target_code.zfill(6)
industry_map = get_stock_industry_map()

# Show target info
target_name = get_target_name(target_code)
target_ind = get_target_industry(target_code)

# 查询所属板块
target_sectors = []
try:
    from sector_library import query_stock_sectors
    target_sectors = query_stock_sectors(target_code)
except Exception:
    pass

col1, col2, col3, col4 = st.columns(4)
col1.metric("目标股票", f"{target_code} {target_name}")
col2.metric("CSRC行业", target_ind or "未知")
col3.metric("搜索范围", search_scope)
col4.metric("所属板块", len(target_sectors))

if target_sectors:
    # 只显示概念板块
    concept_sectors = [s for s in target_sectors if s in [
        x['sector_name'] for x in (
            _get_sector_db().list_sectors(sector_type='概念') if _get_sector_db() else []
        )
    ]] if '_get_sector_db' in dir() else target_sectors[:8]
    if not concept_sectors:
        concept_sectors = target_sectors[:8]
    with st.expander(f"📊 所属板块详情 ({len(target_sectors)} 个板块)", expanded=False):
        cols = st.columns(4)
        for i, s in enumerate(concept_sectors[:16]):
            cols[i % 4].caption(f"• {s}")

st.markdown("---")

if not run_btn:
    st.info("设置好参数后，点击左侧「🔍 开始搜索」")
    st.stop()


# ============================================================
# Build candidate pool
# ============================================================

with st.spinner("正在构建候选池..."):
    if search_scope == "同行业（CSRC）":
        candidates = get_same_industry_stocks(target_code)
    elif search_scope == "同CSRC大类（相邻子行业）":
        candidates = get_sibling_industry_stocks(target_code, delta=5)
    elif search_scope == "同板块（概念板块）":
        # 使用板块库获取同板块股票
        try:
            from sector_library import get_peer_stocks_in_same_sectors
            # 先获取同板块股票（不计算联动，仅获取候选池）
            from sector_library import get_db
            db = get_db(auto_init=False)
            stock_sectors = db.get_sectors_for_stock(target_code)
            candidates_set = set()
            for sector_name in stock_sectors:
                sector_stocks = db.get_stocks_in_sector(sector_name)
                for s in sector_stocks:
                    if s != target_code:
                        candidates_set.add(s)
            candidates = list(candidates_set)
            if not candidates:
                st.warning(f"{target_code} 未找到所属板块，回退到全市场搜索")
                candidates = get_all_stocks_code(exclude_st=True)
        except Exception as e:
            st.warning(f"板块库查询失败: {e}，回退到全市场搜索")
            candidates = get_all_stocks_code(exclude_st=True)
    else:
        candidates = get_all_stocks_code(exclude_st=True)

    if not candidates:
        st.error("候选池为空，请检查股票代码或扩大搜索范围")
        st.stop()

    # Deduplicate & remove target
    candidates = list(set(candidates))
    if target_code in candidates:
        candidates.remove(target_code)

    st.info(f"候选池: {len(candidates)} 只股票")


# ============================================================
# Utility functions (must be defined before use)
# ============================================================

_bs_session = None
_daily_provider = None


def _normalize_daily_df(df: pd.DataFrame) -> pd.DataFrame:
    """统一日线 DataFrame 列名和索引。"""
    if df is None or df.empty:
        return pd.DataFrame()
    # DatetimeIndex → date 列
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
    for target, aliases in column_aliases.items():
        for alias in aliases:
            if alias in df.columns:
                rename[alias] = target
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


@st.cache_data(ttl=600, show_spinner=False)
def _get_daily_tdx_cached(code: str) -> pd.DataFrame:
    """TDX 日线获取（Streamlit 缓存 10 分钟）。"""
    try:
        df = _daily_provider.get_daily(code)
        return _normalize_daily_df(df)
    except Exception:
        return pd.DataFrame()


def _init_baostock():
    """初始化 baostock 会话（幂等）。"""
    global _bs_session
    if _bs_session is None:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock login failed: {lg.error_msg}")
        _bs_session = bs


def _close_baostock():
    global _bs_session
    if _bs_session is not None:
        _bs_session.logout()
        _bs_session = None


def _fetch_baostock_one(code: str) -> pd.DataFrame:
    """baostock 单只股票查询（需已登录）。"""
    if _bs_session is None:
        _init_baostock()
    bs_code = f"sh.{code}" if code.startswith("6") else f"sz.{code}"
    rs = _bs_session.query_history_k_data_plus(
        bs_code, "date,open,high,low,close,volume,amount,pctChg",
        start_date="2024-01-01", end_date="2099-12-31",
        frequency="d", adjustflag="2",
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


@st.cache_data(ttl=600, show_spinner=False)
def _get_daily_baostock_cached(code: str) -> pd.DataFrame:
    """baostock 日线获取（Streamlit 缓存 10 分钟）。"""
    return _fetch_baostock_one(code)


def _get_daily(code: str) -> pd.DataFrame:
    """获取日线数据，根据用户选择使用 TDX 或 baostock。"""
    if _use_baostock:
        return _get_daily_baostock_cached(code)
    return _get_daily_tdx_cached(code)


# ============================================================
# Data source setup — 根据用户选择初始化
# ============================================================

_use_baostock = (data_source == "baostock 网络")

if not _use_baostock:
    try:
        _daily_provider = get_provider()
        _test_df = _daily_provider.get_daily(target_code)
        if _test_df is not None and not _test_df.empty:
            if "close" not in _test_df.columns and "收盘" in _test_df.columns:
                _test_df = _test_df.rename(columns={"收盘": "close"})
            if "close" not in _test_df.columns:
                st.error(f"TDX 数据缺少 close 列: {list(_test_df.columns)[:8]}")
                st.stop()
        else:
            st.error("TDX 返回空数据，请检查通达信数据目录或切换到 baostock")
            st.stop()
    except Exception as e:
        st.error(f"TDX 初始化失败: {e}")
        st.stop()
else:
    try:
        _init_baostock()
    except Exception as e:
        st.error(f"baostock 初始化失败: {e}")
        st.stop()

    # baostock 扫 657 只约需 40 分钟，耐心等待
    st.info(f"baostock 模式全量扫描 {len(candidates)} 只，预计 {len(candidates) * 4 // 60} 分钟")


# ============================================================
# Progress tracking
# ============================================================

progress_bar = st.progress(0)
status_text = st.empty()


def _update_progress(current: int, total: int, phase: str = ""):
    pct = min(current / max(total, 1), 1.0)
    progress_bar.progress(pct)
    status_text.text(f"{phase} {current}/{total}")


# ============================================================
# Run scan
# ============================================================

# 数据源提示
st.caption(f"数据源: {data_source} | 候选池: {len(candidates)} 只 | 对齐: {lookback_days}天 | 半衰期: {half_life}天")

start_time = time.time()
df = pd.DataFrame()
scan_error = None

with st.spinner(f"正在扫描 {len(candidates)} 只候选股票..."):
    try:
        df = find_linked_stocks(
            target_code=target_code,
            candidate_codes=candidates,
            get_daily_fn=_get_daily,
            half_life=half_life,
            top_n=top_n,
            lookback_days=lookback_days,
            progress_callback=_update_progress,
        )
    except Exception as e:
        scan_error = str(e)
        import traceback
        scan_error += "\n" + traceback.format_exc()

elapsed = time.time() - start_time
progress_bar.progress(1.0)

if _use_baostock:
    _close_baostock()

if scan_error:
    st.error("扫描过程出错")
    with st.expander("🔧 错误详情"):
        st.code(scan_error)
else:
    status_text.text(f"完成: {len(df)} 条结果 / 耗时 {elapsed:.1f}s | 数据源: {data_source}")

st.markdown("---")


# ============================================================
# Display results
# ============================================================

if df.empty:
    st.warning("未找到满足条件的联动股票，请尝试扩大搜索范围")
    st.stop()

st.subheader(f"📊 搜索结果 (Top {len(df)})")

# Build display dataframe
display_cols = {
    "code": "代码",
    "total_score": "联动得分",
    "direction_match": "方向一致率",
    "return_corr": "收益相关",
    "shape_sim": "形态相似",
    "r_squared": "联动R2",
    "beta": "Beta",
    "roll_stability": "稳定性",
    "common_days": "共同天数",
}

# Build display (skip common_days if not in results — backward compat)
_display_keys = [k for k in display_cols if k in df.columns]
display_df = df[_display_keys].rename(columns={k: display_cols[k] for k in _display_keys}).copy()

# Add stock names
name_map = {k: v.get("name", "") for k, v in industry_map.items()}
display_df.insert(1, "名称", df["code"].map(lambda c: name_map.get(c, "")))

# Format
display_df["联动得分"] = display_df["联动得分"].apply(lambda x: f"{x:.4f}")
display_df["方向一致率"] = display_df["方向一致率"].apply(lambda x: f"{x:.1%}")
display_df["收益相关"] = display_df["收益相关"].apply(lambda x: f"{x:.4f}")
display_df["形态相似"] = display_df["形态相似"].apply(lambda x: f"{x:.4f}")
display_df["联动R2"] = display_df["联动R2"].apply(lambda x: f"{x:.4f}")
display_df["Beta"] = display_df["Beta"].apply(lambda x: f"{x:.3f}")
display_df["稳定性"] = display_df["稳定性"].apply(lambda x: f"{x:.4f}")

# Reset index starting from 1
display_df.index = range(1, len(display_df) + 1)
display_df.index.name = "排名"

st.dataframe(
    display_df,
    use_container_width=True,
    height=min(38 + len(display_df) * 35, 500),
)


# ============================================================
# Expand — chart overlay
# ============================================================

st.markdown("---")
st.subheader("📈 K线叠加对比")

compare_codes = st.multiselect(
    "选择要对比的股票（可选多个）",
    options=df["code"].tolist(),
    default=df["code"].tolist()[:3],
    format_func=lambda c: f"{c} {name_map.get(c, '')}",
    help="选择后下方显示归一化价格叠加图",
)

if compare_codes:
    # Fetch target data (already normalized with "date" column)
    t_df = _get_daily(target_code)
    if not t_df.empty and "date" in t_df.columns:
        t_close = t_df[["date", "close"]].copy()

        # Normalize: all start from 1.0
        chart_data = pd.DataFrame()
        chart_data["date"] = t_close["date"]
        chart_data[target_code] = t_close["close"] / t_close["close"].iloc[0]

        for code in compare_codes:
            try:
                c_df = _get_daily(code)
                if c_df.empty or "date" not in c_df.columns:
                    continue
                c_tmp = c_df[["date", "close"]].copy()
                merged = chart_data[["date"]].merge(
                    c_tmp.rename(columns={"close": code}),
                    on="date", how="inner",
                )
                if not merged.empty:
                    chart_data[code] = merged[code] / merged[code].iloc[0]
            except Exception:
                continue

        if len(chart_data.columns) > 2:  # more than just date + target
            chart_data = chart_data.set_index("date")
            st.line_chart(
                chart_data,
                height=400,
                use_container_width=True,
            )
            st.caption("归一化价格曲线（起始=1.0）— 曲线越贴合说明联动越强")


# ============================================================
# TDX vs baostock comparison
# ============================================================

st.markdown("---")
st.subheader("🔬 数据源对比")

compare_code = st.text_input(
    "输入股票代码对比 TDX vs baostock 原始数据",
    value=target_code,
    placeholder="如: 603328",
    max_chars=6,
)

if st.button("对比数据"):
    # TDX
    tdx_df = _get_daily_tdx_cached(compare_code) if _daily_provider else pd.DataFrame()
    # baostock (ensure session)
    try:
        _init_baostock()
        bs_df = _fetch_baostock_one(compare_code)
    except Exception:
        bs_df = pd.DataFrame()

    c1, c2 = st.columns(2)
    with c1:
        st.metric("TDX 行数", len(tdx_df) if not tdx_df.empty else 0)
        if not tdx_df.empty:
            st.caption(f"日期: {tdx_df['date'].min()} ~ {tdx_df['date'].max()}" if 'date' in tdx_df.columns else "")
            st.dataframe(tdx_df.tail(5), use_container_width=True)
        else:
            st.warning("TDX 无数据")
    with c2:
        st.metric("baostock 行数", len(bs_df) if not bs_df.empty else 0)
        if not bs_df.empty:
            st.caption(f"日期: {bs_df['date'].min()} ~ {bs_df['date'].max()}" if 'date' in bs_df.columns else "")
            st.dataframe(bs_df.tail(5), use_container_width=True)
        else:
            st.warning("baostock 无数据")

    # Compare daily returns for overlapping dates
    if not tdx_df.empty and not bs_df.empty and "close" in tdx_df.columns and "close" in bs_df.columns:
        tdx_df_d = tdx_df[["date", "close"]].copy()
        bs_df_d = bs_df[["date", "close"]].copy()
        tdx_df_d["source"] = "TDX"
        bs_df_d["source"] = "BS"

        merged = tdx_df_d.merge(bs_df_d, on="date", suffixes=("_tdx", "_bs"), how="inner")
        if len(merged) > 5:
            merged["ret_tdx"] = merged["close_tdx"].pct_change()
            merged["ret_bs"] = merged["close_bs"].pct_change()
            merged["ret_diff"] = (merged["ret_tdx"] - merged["ret_bs"]).abs()
            merged["sign_match"] = (np.sign(merged["ret_tdx"]) == np.sign(merged["ret_bs"]))

            st.metric("重叠交易日", len(merged))
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("平均日收益差异", f"{merged['ret_diff'].mean():.6f}")
            col_b.metric("方向一致率", f"{merged['sign_match'].sum()/max(1,len(merged)-1):.1%}")
            col_c.metric("收益相关", f"{merged['ret_tdx'].corr(merged['ret_bs']):.4f}" if len(merged) > 20 else "N/A")

# ============================================================
# Legend
# ============================================================

with st.expander("📖 指标说明"):
    st.markdown("""
    | 指标 | 权重 | 含义 | 优质阈值 |
    |------|------|------|---------|
    | **方向一致率** | 25% | 同涨同跌的交易日占比。80%=每5天有4天同向 | > 75% |
    | **收益相关** | 25% | 时间加权日收益率相关系数，近期权重更大 | > 0.60 |
    | **形态相似** | 20% | 累计收益曲线相关度，反映长期走势形态重合度 | > 0.85 |
    | **联动 R²** | 20% | 目标涨跌能解释候选多少比例波动，越高联动越紧密 | > 0.30 |
    | **稳定性** | 10% | 20日滚动相关是否持续稳定，排除虚假相关 | > 0.80 |
    | **Beta** | — | 联动弹性。1.0=目标涨1%候选涨1% | ~1.0 |
    """)
