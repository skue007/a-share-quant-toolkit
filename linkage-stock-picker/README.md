# 🔗 联动选股 (Linkage Stock Picker)

> 基于**五维度联动分析**，找出与目标股票（或板块指数）走势联动最紧密的标的。
> 独立可安装运行的模块，基于本地通达信数据或 baostock 网络数据源。

![streamlit](https://img.shields.io/badge/UI-Streamlit-red) ![python](https://img.shields.io/badge/Python-3.9+-blue) ![license](https://img.shields.io/badge/License-MIT-green)

---

## ✨ 功能特性

### 两种分析模式

| 模式 | 说明 |
|------|------|
| **个股联动** | 输入目标股票代码（如 `002815`），在「同行业 / 相邻子行业 / 同概念板块 / 全市场」范围内扫描，返回联动最强的 Top N 股票 |
| **板块指数联动** | 选择目标概念板块指数（8804xx-8809xx，约 500 个行业+概念板块），对全部候选板块计算联动得分，找出联动最紧密的板块 |

### 五维度联动指标

| 指标 | 权重 | 含义 | 优质阈值 |
|------|------|------|---------|
| **方向一致率** | 25% | 同涨同跌的交易日占比 | > 75% |
| **收益相关** | 25% | 时间加权日收益率相关系数（近期权重更大） | > 0.60 |
| **形态相似** | 20% | 累计收益曲线相关度（长期走势形态重合度） | > 0.85 |
| **联动 R²** | 20% | 目标涨跌对候选波动的解释力（含 Beta 弹性） | > 0.30 |
| **稳定性** | 10% | 20 日滚动相关是否持续稳定（排除虚假相关） | > 0.80 |

### 其他能力

- **两阶段扫描**：快筛（等权相关）→ 精算（五维度全量），候选池大时依然高效
- **双数据源**：通达信本地 `.day` 文件（快） / baostock 网络（慢但复权精确），可切换对比
- **归一化叠加图**：目标与候选的 K 线/板块指数走势叠加对比
- **数据源对拍**：同股票 TDX vs baostock 原始数据方向一致率、收益相关对比

---

## 🚀 快速开始

### 1. 环境要求

- Python 3.9+
- 可选：本机安装 [通达信](https://www.tdx.com.cn/) 客户端（本地数据源）

### 2. 安装

```bash
git clone https://github.com/skue007/a-share-quant-toolkit.git
cd a-share-quant-toolkit/linkage-stock-picker

# 建议使用虚拟环境
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 3. 配置数据源

复制 `.env.example` 为 `.env`，填写通达信安装目录：

```ini
# 通达信安装目录（需包含 vipdoc/sh/lday 等子目录）
TDX_ROOT=D:/program/通达信
```

> **没有通达信？** 页面数据源选择「baostock 网络」即可联网获取日线数据（需安装 `baostock`，已包含在 requirements 中，速度较慢）。

### 4. 启动

```bash
streamlit run app.py
# Windows 也可直接双击 run.bat
```

浏览器访问 http://localhost:8501 即可使用。

---

## 📁 项目结构

```
linkage-stock-picker/
├── app.py                    # Streamlit 主页面（两种联动模式）
├── linkage_analysis.py       # ★ 五维度联动算法引擎（纯 numpy/pandas，可独立复用）
├── industry_data.py          # 行业分类与候选池构建（CSRC / 概念板块）
├── tdx_data.py               # 通达信本地日线数据提供者（.day 文件读取）
├── data_utils.py             # TDX .day 向量化解析
├── sector_library/           # 板块库（板块-成分股映射，SQLite）
│   ├── __init__.py
│   ├── database.py           # SQLite 存储层
│   ├── tdx_block_parser.py   # 通达信板块文件解析（block_gn.dat 等）
│   ├── importer.py           # 板块数据导入器（python -m sector_library import）
│   └── sector_linkage.py     # 板块指数联动（build_sector_returns_from_index）
├── data/
│   ├── tdxzs_mapping.json    # 通达信板块指数映射（行业+概念 ~500 个）
│   ├── sector_library.db     # 板块库数据库（544 板块 / 2440 只股票）
│   └── cache/industry_map.json  # CSRC 行业分类缓存
├── .streamlit/config.toml
├── .env.example
├── requirements.txt
└── run.bat / run.sh
```

---

## 🔧 进阶用法

### 重新导入板块数据（可选）

仓库附带了板块库数据库（`data/sector_library.db`，开箱即用）。
若你的通达信版本更新了板块，可重新从本地板块文件导入：

```bash
python -m sector_library import --tdx-root "D:/program/通达信"
```

### 更新行业分类缓存

删除 `data/cache/industry_map.json` 后，首次运行会自动从 baostock 拉取最新 CSRC 行业分类。

### 算法独立使用

`linkage_analysis.py` 无任何 UI 依赖，可直接在脚本/回测中调用：

```python
from linkage_analysis import compute_linkage_scores, find_linked_stocks

# 单对计算
scores = compute_linkage_scores(
    target_returns=t_returns,      # newest_first
    target_close=t_close,
    candidate_returns=c_returns,
    candidate_close=c_close,
    half_life=60,
)
print(scores["total_score"])
```

---

## ⚠️ 免责声明

本项目仅用于**技术研究与学习交流**，不构成任何投资建议。
股市有风险，入市需谨慎。据此操作，风险自负。

数据来源：通达信本地数据、baostock 开放数据。数据版权归各自所有者所有。
