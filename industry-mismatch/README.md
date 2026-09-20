# industry-mismatch

A股**行业错配检测**工具：自动发现「名不副实」的股票——官方行业分类已无法反映其真实业务，市场正按新逻辑定价。

> 一个独立可安装的 Python 包，无需依赖任何其它内部系统或本地行情文件。

## ✨ 特性

- **双范围联动对比**：对每只短期暴涨股，分别在同行业和全市场范围内做联动选股，若全市场联动显著优于同行业 → 判定为行业错配
- **五维度联动分析**：方向一致率、时间加权收益相关、累计收益形态相似、联动 R²+Beta、滚动稳定性
- **CSRC 官方行业分类**：内置全市场行业映射缓存（5530 只股票 / 84 个行业），开箱即用
- **双界面**：命令行扫描（输出 JSON/Markdown 报告）+ Streamlit 网页交互界面
- **零本地数据依赖**：数据源为 baostock 免费网络行情，无需通达信等本地行情文件
- **可扩展数据源**：实现 `DataSource` 接口即可接入 TDX / akshare / 自研数据

## 📦 安装

要求 Python ≥ 3.10。

```bash
# 方式一：从本地源码安装
pip install .

# 方式二：含网页界面（推荐）
pip install ".[ui]"

# 方式三：仅核心库（无 Streamlit）
pip install .
```

## 🚀 快速开始

### 命令行扫描

```bash
# 全市场扫描，输出 JSON 报告（写入 ~/.industry_mismatch/output/）
industry-mismatch scan

# 调整参数 + 输出 Markdown 报告
industry-mismatch scan \
  --lookback 60 --min-return 0.2 --max-return 1.2 \
  --max-candidates 30 --top-n 10 --format md --out report.md

# 输出到指定 JSON 文件
industry-mismatch scan --out my_report.json
```

### 网页界面

```bash
industry-mismatch ui
# 浏览器访问 http://localhost:8501
```

### 作为库使用

```python
from industry_mismatch import DetectionConfig, run_detection

result = run_detection(DetectionConfig(max_candidates=20))
print(result["stats"])  # 扫描统计
print(result["results"])  # 错配结果（按得分降序）
```

## ⚙️ 参数说明

| 参数 | 默认 | 含义 |
|------|------|------|
| `--lookback` | 60 | 暴涨回看交易日（60≈3个月） |
| `--min-return` | 0.20 | 涨幅下限（20%），低于此值被筛掉 |
| `--max-return` | 1.20 | 涨幅上限（120%），排除已明牌的明星股 |
| `--max-candidates` | 50 | 候选股中取涨幅最大的前 N 只做联动对比 |
| `--top-n` | 10 | 每次联动搜索返回的 Top N 结果 |
| `--linkage-lookback` | 120 | 联动分析对齐的交易日数（120≈半年） |
| `--half-life` | 60 | 时间权重半衰期（越小近期权重越大） |
| `--divergence-threshold` | 1.15 | 联动偏离度阈值（全市场Top5均分÷同行业Top5均分） |
| `--escape-threshold` | 0.40 | 行业逃逸率阈值（全市场Top10中非同行业占比） |

## 📊 输出解读

| 指标 | 公式 | 含义 |
|------|------|------|
| **联动偏离度** | 全市场Top5均分 ÷ 同行业Top5均分 | 全市场找到的联动股是否比同行业更紧密 |
| **行业逃逸率** | 全市场Top10中非同行业占比 | 最佳联动股是否集中在其他行业 |
| **替代行业** | 全市场Top10中最集中的非本行业 | 市场实际按哪个行业逻辑定价 |
| **错配得分** | 偏离度×40 + 逃逸率×60 | 综合参考（逃逸率权重更高） |

信号判定：
- 🔴 **强错配**：偏离度 且 逃逸率 同时超过阈值
- 🟡 **关注**：满足其中一项
- — **正常**：均未超过

## 🏗️ 项目结构

```
industry-mismatch/
├── industry_mismatch/
│   ├── __init__.py         # 包导出
│   ├── config.py           # 配置（路径、baostock 参数）
│   ├── data_source.py      # 数据源抽象 + baostock 实现
│   ├── industry_data.py    # 行业分类与候选池
│   ├── linkage.py          # 五维度联动分析引擎
│   ├── core.py             # 错配检测核心逻辑
│   ├── cli.py              # 命令行入口
│   └── app.py              # Streamlit 网页界面
├── data/
│   └── industry_map.json   # 内置行业分类缓存（公开数据）
├── tests/                  # 单元测试
└── pyproject.toml          # pip 安装配置
```

## 🧩 扩展数据源

默认数据源为 baostock（免费网络行情）。如需使用本地通达信数据或其它行情源：

```python
from industry_mismatch import DataSource, run_detection
from industry_mismatch.core import DetectionConfig

class MyTdxSource(DataSource):
    def get_daily(self, code): ...      # 返回 date/open/high/low/close/volume DataFrame
    def get_stock_industry_map(self, force_refresh=False): ...

run_detection(DetectionConfig(), source=MyTdxSource())
```

## 📄 数据说明

- 行业分类：**baostock CSRC 证监会行业分类**（`data/industry_map.json`，随包分发，首次使用无需等待在线拉取；`force_refresh` 可重新拉取）
- 行情数据：运行时通过 baostock 免费接口获取，日线前复权
- 缓存目录：`~/.industry_mismatch/`（可用环境变量 `INDUSTRY_MISMATCH_DATA_DIR` 修改）

## ⚠️ 免责声明

本项目仅用于**量化研究与技术交流**，不构成任何投资建议。股市有风险，投资需谨慎。基于本工具做出的任何投资决策，风险自负。

## 📃 License

[MIT](LICENSE)
