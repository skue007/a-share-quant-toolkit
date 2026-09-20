"""
板块-大盘分时共振分析 — Streamlit 交互界面

找出当日与大盘共振最强的板块 Top5（V 型反转日）：

  1. 大盘指数大跌（最低点跌幅 ≥ 阈值）后回升至上涨（涨幅翻红）——V 型反转
  2. 大盘下跌阶段，板块跟随下跌（下跌阶段涨跌 < 0 且分钟收益与大盘相关）
  3. 大盘反转时板块反弹幅度更大 + 明显放量 + 板块涨停股数 > 3

数据源：东方财富免费接口（分时 trends2 + 涨停池 getTopicZTPool），需联网；
历史分时最多回补约 5 个交易日，盘中运行使用实时数据。

启动方式: `sector-resonance-ui`（等价于 streamlit run 本文件）
"""

from __future__ import annotations

from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from sector_resonance.core import (  # noqa: E402
    INDEX_ALIAS,
    analyze,
    plot_candidates_chart,
    plot_chart,
)

st.set_page_config(page_title="板块大盘分时共振", page_icon="📈", layout="wide")

# ╔══════════════════════════ 侧边栏参数 ══════════════════════════
with st.sidebar:
    st.subheader("⚙️ 参数")
    name2secid = {v: k for k, v in INDEX_ALIAS.items()}
    sel_name = st.selectbox("大盘指数", list(INDEX_ALIAS.values()), index=0,
                            help="V 型反转以所选指数为基准；08-31/09-01 的 V 型主要在深市（深成指/创业板指）")
    index_secid = name2secid[sel_name]

    dt = st.date_input("分析日期", value=datetime.now().date(),
                       help="默认今天（盘中实时）；历史日最多回补约 5 个交易日")
    date_str = dt.isoformat()

    with st.expander("共振判定阈值", expanded=False):
        d_down = st.slider("大盘大跌阈值 %", 0.3, 3.0, 0.8, 0.1,
                           help="最低点相对昨收跌幅需 ≤ -该值")
        vol_ratio = st.slider("放量比阈值", 1.0, 3.0, 1.2, 0.05,
                              help="回升阶段每分钟均量 / 下跌阶段每分钟均量")
        corr = st.slider("下跌阶段相关性阈值", 0.0, 0.6, 0.2, 0.05,
                         help="下跌窗口内板块与大盘分钟收益的 Pearson 相关")
        min_zt = st.number_input("板块涨停股数下限", 1, 20, 4, 1,
                                 help="条件 3c '涨停>3'，即 ≥4")
        min_decline = st.number_input("最低点距开盘分钟数", 0, 120, 15, 1,
                                      help="排除低开高走（开盘即最低）")
        top_n = st.slider("Top N", 1, 10, 5, 1)

    force = st.checkbox("强制刷新（盘中实时）", value=False,
                        help="盘中数据每分钟变化，勾选后忽略缓存重新拉取")
    run = st.button("🚀 运行共振分析", type="primary")
    st.caption("数据源：东方财富（需联网）。串行请求约 10~30 秒，避免触发限流。")

st.title("📈 板块-大盘分时共振分析")
st.caption("共振定义：**1)** 大盘大跌后回升至上涨（V 型反转）　**2)** 下跌阶段板块跟随下跌（方向一致）　"
           "**3)** 反转时板块反弹幅度更大 + 明显放量 + 涨停股数 > 3")

if not run:
    st.info("在左侧设置参数后点击「运行共振分析」。")
    st.stop()

# ╔══════════════════════════ 运行分析（带会话缓存） ══════════════════════════
is_today = date_str == datetime.now().strftime("%Y-%m-%d")
cache_key = f"res27_{date_str}_{index_secid}_{d_down}_{vol_ratio}_{corr}_{min_zt}_{min_decline}_{top_n}"
logs: list[str] = []

if (not force) and (cache_key in st.session_state) and not (is_today and force):
    res = st.session_state[cache_key]
    st.success("✅ 已使用缓存结果（同参数历史日重复运行不再请求接口）")
else:
    with st.spinner("拉取分时数据（串行请求，约 10~30 秒）..."):
        try:
            res = analyze(
                date=date_str, index=index_secid,
                d_down=d_down, vol_ratio=vol_ratio, corr=corr,
                min_zt=min_zt, top_n=top_n, min_decline_min=min_decline,
                log_fn=lambda msg: logs.append(str(msg)),
            )
            st.session_state[cache_key] = res
        except Exception as e:  # noqa: BLE001
            st.error(f"分析失败: {e}")
            st.stop()

if logs:
    with st.expander("运行日志", expanded=False):
        st.code("\n".join(logs))

# ╔══════════════════════════ 大盘 V 型反转 ══════════════════════════
st.subheader("① 大盘 V 型反转检测")
v = res["v_reversal"]
idx = res["idx"]
if v is None:
    st.warning(f"**{date_str} {res['index_name']}** 无 V 型反转（需最低跌幅 ≥{d_down}% 且回升翻红、"
               f"最低点距开盘 ≥{min_decline} 分钟）——无共振板块。"
               f"当日：开盘 {idx['pct'].iloc[0]:+.2f}% / 最低 {idx['pct'].min():+.2f}% / "
               f"收盘 {idx['pct'].iloc[-1]:+.2f}%")
    st.stop()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("下跌起点", v["peak_time"])
c2.metric("最低点", f"{v['low_time']}  {v['min_pct']:.2f}%")
c3.metric("首次翻红", v["rec_time"])
c4.metric("收盘", f"{v['end_pct']:+.2f}%")
c5.metric("涨停池 / 候选", f"{res['zt_total']} / {len(res['candidates'])}")

# ╔══════════════════════════ 淘汰明细 ══════════════════════════
failed = res["failed"]
zt_map = {full: z for _, z, _, full in res["candidates"]}
if failed:
    st.subheader("② 候选板块淘汰明细（涨停≥下限）")
    df_fail = pd.DataFrame(
        [{"板块": full, "涨停数": zt_map.get(full, 0), "淘汰原因": reason}
         for full, reason in failed]
    )
    st.dataframe(df_fail, width="stretch", hide_index=True)

top = res["top"]
if not top:
    st.subheader("③ 共振结果")
    st.error("**严格口径下无合格共振板块**——近期 V 型日的涨停密集板块多为独立走强"
             "（大盘跌时反而上涨），未跟随大盘下跌。可下调相关性/方向门槛或改用其他大盘指数。")
    fig = plot_candidates_chart(idx, res["candidates"], res["curves"],
                                dict(failed), date_str, res["index_name"], v, None)
    st.pyplot(fig)
    st.stop()

# ╔══════════════════════════ 合格板块 ══════════════════════════
st.subheader(f"③ 与大盘共振最强板块 Top{len(top)}")
df_top = pd.DataFrame([
    {
        "排名": i, "板块": m["name"], "涨停数": m["zt_count"],
        "下跌段涨跌%": m["decl_ret_pct"], "方向相关": m["decl_corr"],
        "大盘反弹%": m["idx_amp_pct"], "板块反弹%": m["bd_amp_pct"],
        "反弹超额%": m["amp_excess_pct"], "放量比": m["vol_ratio"],
        "全天相关": m["full_corr"], "评分": m["score"],
    }
    for i, m in enumerate(top, 1)
])
st.dataframe(df_top, width="stretch", hide_index=True)

st.subheader("④ 分时对比图（涨跌幅 % 相对昨收）")
fig = plot_chart(idx, top, res["curves"], date_str, res["index_name"], v, None)
st.pyplot(fig)
