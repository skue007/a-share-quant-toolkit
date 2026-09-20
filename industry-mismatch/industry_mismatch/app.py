"""行业错配检测 — Streamlit 网页界面。

启动方式:
    pip install "industry-mismatch[ui]"
    industry-mismatch ui

或: streamlit run industry_mismatch/app.py

数据源固定为 baostock（免费网络行情），无本地行情文件依赖。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# 兼容「直接以脚本方式运行」（streamlit run app.py / 测试框架），
# 此时没有包上下文，需手动将项目根加入 sys.path 以便绝对导入。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from industry_mismatch import __version__
from industry_mismatch import __version__
from industry_mismatch.core import (
    SIGNAL_NORMAL,
    SIGNAL_STRONG,
    SIGNAL_WATCH,
    DetectionConfig,
    analyze_industry_distribution,
    classify_signal,
    compute_divergence_metrics,
    normalize_daily_df,
    scan_surge_stocks,
)
from industry_mismatch.data_source import BaostockDataSource
from industry_mismatch.industry_data import (
    get_all_stocks_code,
    get_same_industry_stocks,
    get_stock_industry_map,
)
from industry_mismatch.linkage import find_linked_stocks

st.set_page_config(page_title="行业错配检测", page_icon="🔄", layout="wide")

st.title("🔄 行业错配检测")
st.caption(
    "发现「名不副实」的股票——官方行业分类已无法反映其真实业务，"
    "市场按新逻辑定价。通过双范围联动对比（同行业 vs 全市场）自动识别。"
)

# ============================================================
# Sidebar controls
# ============================================================

with st.sidebar:
    st.subheader("📋 扫描设置")

    st.markdown("**🔍 Step 1: 暴涨股筛选**")

    surge_lookback = st.slider(
        "回看天数（交易日）",
        min_value=20, max_value=120, value=60, step=10,
        help="计算过去N个交易日的涨幅。60≈3个月，20≈1个月。",
    )

    surge_min_return = st.slider(
        "涨幅下限",
        min_value=5, max_value=150, value=20, step=5,
        format="%d%%",
        help="涨幅低于此的股票被筛掉。设为较低值(如5%)可扩大候选范围。",
    )

    surge_max_return = st.slider(
        "涨幅上限 ⭐",
        min_value=30, max_value=500, value=120, step=10,
        format="%d%%",
        help="涨幅超过此上限的股票被排除——筛掉已经明牌的「明星股」，聚焦中上位置的潜力股。",
    )

    max_surge_candidates = st.slider(
        "最多分析候选数",
        min_value=10, max_value=100, value=50, step=10,
        help="在符合涨幅区间的股票中，取涨幅最大的前N只做联动对比。",
    )

    st.markdown("---")

    st.markdown("**🔗 Step 2: 双范围联动对比**")

    top_n_linkage = st.slider(
        "联动返回数量",
        min_value=5, max_value=30, value=10, step=5,
        help="每次联动搜索返回的Top N结果。",
    )

    linkage_lookback = st.slider(
        "联动对齐天数（交易日）",
        min_value=40, max_value=500, value=120, step=20,
        help="联动分析时对齐的交易日数。120≈半年。",
    )

    half_life = st.slider(
        "时间权重半衰期（交易日）",
        min_value=20, max_value=120, value=60, step=10,
        help="越小近期权重越大。",
    )

    st.markdown("---")

    st.markdown("**📏 Step 3: 错配判定阈值**")

    divergence_threshold = st.slider(
        "联动偏离度阈值",
        min_value=1.00, max_value=2.00, value=1.15, step=0.05,
        help="全市场Top5平均分 / 同行业Top5平均分。>此值视为潜在错配。",
    )

    escape_threshold = st.slider(
        "行业逃逸率阈值",
        min_value=10, max_value=80, value=40, step=5,
        format="%d%%",
        help="全市场Top结果中非同行业占比。>此值视为强错配。",
    )

    st.markdown("---")

    st.caption("数据源: baostock 网络（免费）")
    st.caption(f"行业分类: baostock CSRC | v{__version__}")

    run_btn = st.button("🔍 开始扫描", type="primary", use_container_width=True)


# ============================================================
# Data helpers
# ============================================================

@st.cache_resource
def _get_data_source():
    return BaostockDataSource()


@st.cache_data(ttl=600, show_spinner=False)
def _get_daily_cached(code: str) -> pd.DataFrame:
    """baostock 日线获取（Streamlit 缓存 10 分钟）。"""
    try:
        df = _get_data_source().get_daily(code)
        return normalize_daily_df(df)
    except Exception:
        return pd.DataFrame()


# ============================================================
# Initial state
# ============================================================

industry_map = get_stock_industry_map()

if not run_btn:
    st.info("👈 在左侧设置参数后，点击「🔍 开始扫描」开始全市场行业错配检测。")
    st.markdown("""
    ### 工作原理

    1. **暴涨筛选** — 扫描全A股，找出近期涨幅超阈值的股票
    2. **双范围联动** — 对每只候选股，分别在同行业和全市场做联动选股
    3. **错配评分** — 若全市场联动显著优于同行业 → 行业错配

    ### 错配信号解读

    信号等级由侧栏两个阈值决定（可手动调节）：
    - 🔴 **强错配**: 偏离度 且 逃逸率 同时超过阈值
    - 🟡 **关注**: 偏离度 或 逃逸率 其中一项超过阈值
    - — **正常**: 两项均未超过阈值

    | 指标 | 含义 |
    |------|------|
    | **联动偏离度** | 全市场Top5得分 / 同行业Top5得分 |
    | **行业逃逸率** | 全市场Top10中非同行业占比 |
    | **替代行业** | 全市场Top10中最集中的非本行业，揭示真实定价逻辑 |
    | **错配得分** | 偏离度×40 + 逃逸率×60，综合参考 |
    """)
    st.stop()


# ============================================================
# Step 1: Scan surge stocks
# ============================================================

st.markdown("---")
st.subheader("📈 Step 1: 短期暴涨股筛选")

all_codes = get_all_stocks_code(exclude_st=True)
st.caption(
    f"全市场候选: {len(all_codes)} 只（已排除ST/退市） | "
    f"回看: {surge_lookback}天 | 涨幅区间: {surge_min_return}% ~ {surge_max_return}%"
)

progress_bar_step1 = st.progress(0)
status_text_step1 = st.empty()

step1_start = time.time()


def _update_progress_step1(current: int, total: int):
    pct = min(current / max(total, 1), 1.0)
    progress_bar_step1.progress(pct)
    status_text_step1.text(f"暴涨扫描中... {current}/{total}")


surge_stocks = scan_surge_stocks(
    get_daily_fn=_get_daily_cached,
    all_codes=all_codes,
    lookback_days=surge_lookback,
    min_return=surge_min_return / 100,
    max_return=surge_max_return / 100,
    industry_map=industry_map,
    progress_callback=_update_progress_step1,
)

step1_elapsed = time.time() - step1_start
progress_bar_step1.progress(1.0)
status_text_step1.text(f"完成: {len(surge_stocks)} 只暴涨股 / 耗时 {step1_elapsed:.1f}s")

if not surge_stocks:
    st.warning(
        f"未找到 {surge_lookback} 日内涨幅在 {surge_min_return}%~{surge_max_return}% "
        f"区间的股票，请扩大涨幅区间或增加回看天数。"
    )
    st.stop()

surge_stocks = surge_stocks[:max_surge_candidates]

surge_display = []
for code, name, ret, industry in surge_stocks:
    surge_display.append({
        "代码": code,
        "名称": name,
        f"{surge_lookback}日涨幅": f"{ret:.1%}",
        "CSRC行业": industry or "未知",
    })

surge_df = pd.DataFrame(surge_display)
surge_df.index = range(1, len(surge_df) + 1)
surge_df.index.name = "排名"

st.dataframe(surge_df, use_container_width=True, height=min(38 + len(surge_df) * 35, 400))
st.caption(f"将对接下来的 {len(surge_stocks)} 只股票进行双范围联动对比")


# ============================================================
# Step 2: Dual-scope linkage comparison
# ============================================================

st.markdown("---")
st.subheader("🔗 Step 2: 双范围联动对比")

st.caption(
    f"对每只候选股，分别在同行业和全市场做联动选股（Top {top_n_linkage}） | "
    f"对齐: {linkage_lookback}天 | 半衰期: {half_life}天"
)

progress_bar_step2 = st.progress(0)
status_text_step2 = st.empty()

step2_start = time.time()

all_divergence_results = []

for idx, (code, name, n_day_return, target_ind) in enumerate(surge_stocks):
    status_text_step2.text(f"联动对比中... [{idx + 1}/{len(surge_stocks)}] {code} {name}")
    progress_bar_step2.progress((idx + 1) / len(surge_stocks))

    same_ind_candidates = get_same_industry_stocks(code)
    full_mkt_candidates = [c for c in all_codes if c != code]

    if not same_ind_candidates:
        continue

    try:
        same_ind_df = find_linked_stocks(
            target_code=code,
            candidate_codes=same_ind_candidates,
            get_daily_fn=_get_daily_cached,
            half_life=half_life,
            top_n=top_n_linkage,
            lookback_days=linkage_lookback,
        )
    except Exception:
        same_ind_df = pd.DataFrame()

    try:
        full_mkt_df = find_linked_stocks(
            target_code=code,
            candidate_codes=full_mkt_candidates,
            get_daily_fn=_get_daily_cached,
            half_life=half_life,
            top_n=top_n_linkage,
            lookback_days=linkage_lookback,
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
        divergence_threshold=divergence_threshold,
        escape_threshold=escape_threshold / 100,
    )

    all_divergence_results.append({
        "code": code,
        "name": name,
        "return": n_day_return,
        "industry": target_ind,
        "metrics": metrics,
        "signal": signal,
        "same_ind_df": same_ind_df,
        "full_mkt_df": full_mkt_df,
    })

step2_elapsed = time.time() - step2_start
progress_bar_step2.progress(1.0)
status_text_step2.text(f"完成: {len(all_divergence_results)} 只分析完毕 / 耗时 {step2_elapsed:.1f}s")

if not all_divergence_results:
    st.warning("所有候选股的联动分析均无有效结果。")
    st.stop()


# ============================================================
# Step 3: Results display
# ============================================================

st.markdown("---")
st.subheader("📊 Step 3: 行业错配检测结果")

all_divergence_results.sort(key=lambda x: x["metrics"]["mismatch_score"], reverse=True)

summary_rows = []
for r in all_divergence_results:
    m = r["metrics"]
    summary_rows.append({
        "代码": r["code"],
        "名称": r["name"],
        "信号": r["signal"],
        f"{surge_lookback}日涨幅": r["return"],
        "官方行业": r["industry"] or "未知",
        "同行业Top5均分": m["avg_same_score"],
        "全市场Top5均分": m["avg_full_score"],
        "偏离度": m["divergence_ratio"],
        "逃逸率": m["escape_rate"],
        "替代行业": m["alt_industry"] or "-",
        "错配得分": m["mismatch_score"],
    })

summary_df = pd.DataFrame(summary_rows)

strong_signals = sum(1 for r in all_divergence_results if r["signal"] == SIGNAL_STRONG)
potential_signals = sum(1 for r in all_divergence_results if r["signal"] == SIGNAL_WATCH)
normal_count = sum(1 for r in all_divergence_results if r["signal"] == SIGNAL_NORMAL)

col1, col2, col3, col4 = st.columns(4)
col1.metric("分析总数", len(all_divergence_results))
col2.metric(f"🔴 强错配 (偏离≥{divergence_threshold:.2f} 且 逃逸≥{escape_threshold}%)", strong_signals)
col3.metric(f"🟡 关注 (满足其一)", potential_signals)
col4.metric("🟢 行业匹配", normal_count)

st.markdown("---")
st.caption("按错配得分降序排列。点击行展开详情。")

display_df = summary_df.copy()
display_df[f"{surge_lookback}日涨幅"] = display_df[f"{surge_lookback}日涨幅"].apply(lambda x: f"{x:.1%}")
display_df["同行业Top5均分"] = display_df["同行业Top5均分"].apply(lambda x: f"{x:.4f}")
display_df["全市场Top5均分"] = display_df["全市场Top5均分"].apply(lambda x: f"{x:.4f}")
display_df["偏离度"] = display_df["偏离度"].apply(lambda x: f"{x:.2f}x")
display_df["逃逸率"] = display_df["逃逸率"].apply(lambda x: f"{x:.0%}")
display_df["错配得分"] = display_df["错配得分"].apply(lambda x: f"{x:.1f}")

display_df.index = range(1, len(display_df) + 1)
display_df.index.name = "排名"


def _highlight_mismatch_row(row):
    signal = row.get("信号", "")
    if signal == SIGNAL_STRONG:
        return ["background-color: #450a0a"] * len(row)
    elif signal == SIGNAL_WATCH:
        return ["background-color: #3b2507"] * len(row)
    return [""] * len(row)


styled_df = display_df.style.apply(_highlight_mismatch_row, axis=1)
st.dataframe(styled_df, use_container_width=True, height=min(38 + len(display_df) * 35, 600))


# ============================================================
# Detail view
# ============================================================

st.markdown("---")
st.subheader("🔬 错配详情")

detail_options = [
    f"{r['code']} {r['name']} — 错配得分 {r['metrics']['mismatch_score']:.1f}"
    for r in all_divergence_results
]

selected_detail = st.selectbox(
    "选择股票查看详细分析",
    options=list(range(len(all_divergence_results))),
    format_func=lambda i: detail_options[i],
)

if selected_detail is not None:
    r = all_divergence_results[selected_detail]
    m = r["metrics"]
    code = r["code"]
    name = r["name"]

    st.markdown("---")
    st.markdown(f"### {code} {name}")

    cols = st.columns(6)
    cols[0].metric("错配得分", f"{m['mismatch_score']:.1f}", delta=r["signal"])
    cols[1].metric("偏离度", f"{m['divergence_ratio']:.2f}x")
    cols[2].metric("逃逸率", f"{m['escape_rate']:.0%}")
    cols[3].metric("替代行业", m['alt_industry'] or "无")
    cols[4].metric("官方行业", r['industry'] or "未知")
    cols[5].metric(f"{surge_lookback}日涨幅", f"{r['return']:.1%}")

    st.markdown("---")

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("#### 🏭 同行业联动 Top5")
        same_df = r["same_ind_df"]
        if not same_df.empty:
            same_show = same_df.head(5)[["code", "total_score", "direction_match", "return_corr", "r_squared"]].copy()
            same_show["total_score"] = same_show["total_score"].apply(lambda x: f"{x:.4f}")
            same_show["direction_match"] = same_show["direction_match"].apply(lambda x: f"{x:.1%}")
            same_show["return_corr"] = same_show["return_corr"].apply(lambda x: f"{x:.4f}")
            same_show["r_squared"] = same_show["r_squared"].apply(lambda x: f"{x:.4f}")
            same_show.index = range(1, len(same_show) + 1)
            st.dataframe(same_show, use_container_width=True)
            st.caption(f"平均得分: {m['avg_same_score']:.4f} | 最佳: {m['best_same_score']:.4f}")
        else:
            st.warning("同行业无联动结果")

    with col_right:
        st.markdown("#### 🌐 全市场联动 Top5")
        full_df = r["full_mkt_df"]
        if not full_df.empty:
            full_show = full_df.head(5)[["code", "total_score", "direction_match", "return_corr", "r_squared"]].copy()
            full_show["total_score"] = full_show["total_score"].apply(lambda x: f"{x:.4f}")
            full_show["direction_match"] = full_show["direction_match"].apply(lambda x: f"{x:.1%}")
            full_show["return_corr"] = full_show["return_corr"].apply(lambda x: f"{x:.4f}")
            full_show["r_squared"] = full_show["r_squared"].apply(lambda x: f"{x:.4f}")
            full_show.index = range(1, len(full_show) + 1)
            st.dataframe(full_show, use_container_width=True)
            st.caption(f"平均得分: {m['avg_full_score']:.4f} | 最佳: {m['best_full_score']:.4f}")
        else:
            st.warning("全市场无联动结果")

    st.markdown("---")
    st.markdown("#### 📊 双范围联动得分对比")

    chart_data = pd.DataFrame({
        "搜索范围": ["同行业", "全市场"],
        "Top5平均得分": [m["avg_same_score"], m["avg_full_score"]],
        "最佳得分": [m["best_same_score"], m["best_full_score"]],
    })

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Top5平均得分",
        x=chart_data["搜索范围"],
        y=chart_data["Top5平均得分"],
        marker_color=["#3b82f6", "#22c55e"],
        text=[f"{v:.4f}" for v in chart_data["Top5平均得分"]],
        textposition="outside",
    ))
    fig.add_trace(go.Bar(
        name="最佳得分",
        x=chart_data["搜索范围"],
        y=chart_data["最佳得分"],
        marker_color=["#1e40af", "#166534"],
        text=[f"{v:.4f}" for v in chart_data["最佳得分"]],
        textposition="outside",
    ))
    fig.update_layout(
        barmode="group",
        height=350,
        margin=dict(t=10, b=10),
        yaxis=dict(range=[0, max(1.0, m["best_full_score"] * 1.15)]),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    col_pie, col_breakdown = st.columns([1, 1])

    with col_pie:
        st.markdown("#### 🥧 全市场Top10行业分布")
        breakdown = m.get("industry_breakdown", [])
        if breakdown:
            pie_data = pd.DataFrame(breakdown, columns=["行业", "数量"])
            pie_fig = px.pie(pie_data, names="行业", values="数量", height=350)
            pie_fig.update_traces(textposition="inside", textinfo="percent+label")
            pie_fig.update_layout(margin=dict(t=10, b=10))
            st.plotly_chart(pie_fig, use_container_width=True)
        else:
            st.info("无行业分布数据")

    with col_breakdown:
        st.markdown("#### 📋 行业分布明细")
        if breakdown:
            breakdown_display = pd.DataFrame(breakdown, columns=["行业", "出现次数"])
            breakdown_display["占比"] = breakdown_display["出现次数"].apply(
                lambda x: f"{x / max(m['same_industry_count'] + m['other_industry_count'], 1):.0%}"
            )
            breakdown_display.index = range(1, len(breakdown_display) + 1)
            st.dataframe(breakdown_display, use_container_width=True)

            st.markdown("---")
            st.caption(
                f"同行业({r['industry']}): {m['same_industry_count']}只 | "
                f"其他行业: {m['other_industry_count']}只 | "
                f"逃逸率: {m['escape_rate']:.0%}"
            )
            if m["alt_industry"]:
                st.caption(
                    f"💡 替代行业: **{m['alt_industry']}** "
                    f"({m['alt_industry_count']}/{m['same_industry_count'] + m['other_industry_count']}只) "
                    f"— 市场可能按此行业逻辑定价"
                )

    if m["alt_industry"] and not r["full_mkt_df"].empty:
        st.markdown("---")
        st.markdown(f"#### 🎯 替代行业「{m['alt_industry']}」中的联动股")

        full_df = r["full_mkt_df"]
        alt_ind_codes = []
        for _, row in full_df.iterrows():
            c = row["code"]
            info = industry_map.get(c, {})
            if info.get("industry", "") == m["alt_industry"]:
                alt_ind_codes.append(c)

        if alt_ind_codes:
            alt_show = full_df[full_df["code"].isin(alt_ind_codes)].head(10)
            display_cols_map = {
                "code": "代码",
                "total_score": "联动得分",
                "direction_match": "方向一致率",
                "return_corr": "收益相关",
                "r_squared": "联动R²",
                "roll_stability": "稳定性",
            }
            alt_display = alt_show[[c for c in display_cols_map if c in alt_show.columns]].rename(
                columns={k: v for k, v in display_cols_map.items() if k in alt_show.columns}
            ).copy()
            alt_display["联动得分"] = alt_display["联动得分"].apply(lambda x: f"{x:.4f}")
            alt_display["方向一致率"] = alt_display["方向一致率"].apply(lambda x: f"{x:.1%}")
            alt_display.index = range(1, len(alt_display) + 1)
            st.dataframe(alt_display, use_container_width=True)
            st.caption(f"替代行业中联动最强的 {len(alt_display)} 只股票")
        else:
            st.info(f"替代行业「{m['alt_industry']}」中的股票未进入全市场Top{top_n_linkage}")


# ============================================================
# Footer
# ============================================================

st.markdown("---")
with st.expander("📖 指标说明 & 使用建议"):
    st.markdown("""
    ### 指标含义

    | 指标 | 公式 | 含义 | 信号判定 |
    |------|------|------|--------|
    | **联动偏离度** | 全市场Top5均分 ÷ 同行业Top5均分 | 全市场找到的联动股是否比同行业更紧密 | 侧栏可调阈值 |
    | **行业逃逸率** | 全市场Top10中非同行业占比 | 最佳联动股是否集中在其他行业 | 侧栏可调阈值 |
    | **替代行业** | 全市场Top10中最集中的非本行业 | 市场实际按哪个行业逻辑定价 | — |
    | **错配得分** | 偏离度×40 + 逃逸率×60 | 综合参考（权重：逃逸率更高） | — |

    ### 使用建议

    1. **高错配得分 + 明确替代行业** → 优先关注，查看个股研报确认业务转型逻辑
    2. **高偏离度 + 低逃逸率** → 联动股仍在同行业，只是本股与行业脱节（可能是独立行情）
    3. **低偏离度 + 高逃逸率** → 同行业联动尚可，但全市场找到了更强的跨行业联动
    4. **低偏离度 + 低逃逸率** → 股票走势与行业一致，无错配

    ### 典型案例

    - **301188 力诺药包**: 官方=玻璃玻纤 → 替代=半导体材料（药用玻璃→半导体基板玻璃）
    - **600226 亨通股份**: 官方=电力 → 替代=PCB/铜箔（子公司电解铜箔业务）
    """)

st.caption(f"数据源: baostock | 行业分类: baostock CSRC | 总耗时: {step1_elapsed + step2_elapsed:.0f}s")
