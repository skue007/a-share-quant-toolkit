# 板块-大盘分时共振分析 (sector-resonance)

[中文](#简介) | [English (brief)](#english)

## 简介

在 A 股大盘 **V 型反转日**（大跌后回升翻红），自动筛选出与大盘**分时共振**最强的行业板块 Top5。

数据源为**东方财富免费公开接口，无需注册、无需 token**：

| 数据 | 接口 |
|---|---|
| 分时走势（支持回补约 5 个交易日） | `push2his.eastmoney.com /api/qt/stock/trends2/get` |
| 涨停池（任意历史交易日，带行业板块字段） | `push2ex.eastmoney.com /getTopicZTPool` |
| 行业板块列表 | `push2.eastmoney.com /api/qt/clist/get`（`m:90+t:2`） |

## 共振定义

1. **大盘 V 型反转**
   - 大跌：当日分时最低点相对昨收跌幅 ≤ `-d_down`（默认 -0.8%）
   - 回升至上涨：最低点之后存在某一分钟涨幅重新 ≥ 0（翻红）
   - 最低点距开盘 ≥ `min_decline_min` 分钟（默认 15，排除低开高走）
2. **下跌阶段板块与大盘方向一致**
   - 板块在下跌窗口内涨跌幅 < 0，且分钟收益与大盘的 Pearson 相关系数 > `corr`（默认 0.2）
3. **反转时板块反弹更强 + 明显放量 + 涨停股多**
   - 反弹超额：同一时间窗（最低点 → 首次翻红）板块反弹幅度 > 大盘反弹幅度
   - 放量：回升阶段每分钟均量 / 下跌阶段每分钟均量 > `vol_ratio`（默认 1.2）
   - 涨停股数 ≥ `min_zt`（默认 4，即"涨停 > 3"）
4. **共振强度打分**（对全部合格板块 z-score 后加权，取 Top N 输出）

   ```
   score = 0.60 * z(反弹超额) + 0.25 * z(放量比) + 0.15 * z(涨停数)
   ```

## 安装

需要 Python ≥ 3.10 与系统 `curl` 命令（Windows 10+ / macOS / 主流 Linux 均自带）。
东财会按 TLS 指纹屏蔽 python-requests，因此本工具统一通过 curl 子进程拉取数据。

```bash
# 仅命令行版
pip install sector-resonance

# 含 Streamlit 交互界面
pip install "sector-resonance[ui]"
```

或从源码安装：

```bash
git clone https://github.com/skue007/a-share-quant-toolkit.git
cd a-share-quant-toolkit/sector-market-resonance
pip install .
```

## 使用

### 命令行

```bash
# 分析最近一个有涨停池数据的交易日
sector-resonance

# 指定日期（盘中运行则为实时数据，到当前分钟）
sector-resonance --date 2026-09-01

# 更换大盘指数：上证 1.000001 / 深成指 0.399001 / 创业板指 0.399006 / 沪深300 1.000300 / 中证500 0.399905
sector-resonance --date 2026-09-01 --index 0.399001

# 调整阈值
sector-resonance --date 2026-09-01 --d-down 0.5 --vol-ratio 1.1 --corr 0.1 --min-zt 3

# python -m 方式等价
python -m sector_resonance --date 2026-09-01
```

完整参数：`sector-resonance --help`

### Streamlit 交互界面

```bash
sector-resonance-ui
```

侧边栏可选大盘指数、日期与全部阈值，支持强制刷新（盘中实时），输出指标表格、淘汰明细与分时对比图。

### 作为库调用

```python
from sector_resonance import analyze, plot_chart

res = analyze(date="2026-09-01", index="0.399001")
print(res["top"])          # Top 板块指标列表
print(res["v_reversal"])   # V 型反转检测信息（None 表示当日无 V 型反转）
```

## 输出

- 控制台：Top 板块指标表格（涨停数 / 下跌段涨跌 / 方向相关 / 反弹超额 / 放量比 / 评分）
- `resonance_output/{date}_market_resonance.json`：结构化结果
- `resonance_output/{date}_resonance_chart.png`：大盘与 Top 板块分时对比图
- 严格口径无合格板块时，输出 `{date}_candidates_overview.png` 候选板块概览图（标注淘汰原因）

输出目录默认为当前工作目录下的 `resonance_output/`，可用环境变量 `RESONANCE_OUTPUT_DIR` 覆盖。
同日重复分析会使用本地缓存（`resonance_output/_cache/`），盘中数据不写缓存。

## 示例

见 [`examples/`](examples/)：2026-08-31 / 2026-09-01 两日的 JSON 结果与候选板块概览图
（这两个交易日大盘 V 型反转明显，但涨停密集板块多为独立走强，严格口径下无合格共振板块，
可下调相关性 / 方向门槛观察候选）。

## 已知限制

- 东财 `trends2` 分时接口最多回补约 **5 个交易日**，更早日期无法分析（盘中运行使用实时数据）
- 板块口径为东财行业板块（BKxxxx）；通达信 880xxx 板块指数分时在免费公开源不可得
- 东财对高频请求限流（`rc=102`），默认串行拉取并带退避重试，一次分析约 10~30 秒
- 分时图中文标注依赖系统中文字体（Microsoft YaHei / SimHei / Arial Unicode MS）

## English

Detect sectors that resonate most strongly with the A-share market index on
**intraday V-reversal days** — index plunges intraday then recovers into positive
territory; qualified sectors must (1) follow the index down with correlated minute
returns, (2) rebound harder than the index, with volume expansion and ≥4 limit-up
stocks. Data comes from EastMoney free public APIs (no token), fetched via `curl`
subprocess to bypass TLS-fingerprint blocking. Scored by weighted z-scores of
rebound excess, volume ratio and limit-up count; outputs CLI table, JSON report,
matplotlib chart, and an optional Streamlit UI.

## 免责声明

本项目仅用于量化研究与学习交流，不构成任何投资建议。数据来自公开接口，
准确性以交易所官方为准，使用者需自行承担使用风险。

## License

[MIT](LICENSE)
