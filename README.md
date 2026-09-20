# A 股量化分析工具箱 (a-share-quant-toolkit)

四个面向 A 股的独立量化分析工具，专注「**联动 / 共振 / 形态**」这一条主线：
不是预测涨跌，而是从「谁跟谁一起动」里找出市场正在定价的真实逻辑。

> ⚠️ 本项目仅用于**量化研究与技术学习交流**，不构成任何投资建议。
> 详见文末[免责声明](#免责声明)。

---

## 工具箱一览

| 工具 | 一句话说明 | 界面 | 数据源 |
|------|-----------|------|--------|
| [**industry-mismatch**](industry-mismatch/) | 找出「名不副实」的股票——官方行业分类已跟不上其真实业务，市场按新逻辑给它定价 | CLI + Streamlit | baostock（免费） |
| [**linkage-stock-picker**](linkage-stock-picker/) | 输入一只股票或板块指数，找出与它走势联动最紧密的标的 | Streamlit | 通达信本地 / baostock |
| [**custom-pattern-screener**](custom-pattern-screener/) | 给几个「理想形态」的示例 K 线，在全市场找出长得像的股票 | CLI | 通达信本地 / akshare |
| [**sector-market-resonance**](sector-market-resonance/) | 在大盘 V 型反转日，找出与大盘分时共振最强的行业板块 Top5 | CLI + Streamlit | 东方财富（免费） |

**全部数据源均为免费公开接口，无需申请任何 API Key 或 Token。**
这是刻意的设计选择：不把使用者绑定在任何一个需要付费/注册的数据服务上。

---

## 共用核心：五维度联动分析

`industry-mismatch`、`linkage-stock-picker`、`custom-pattern-screener` 三个工具共享同一套
「两只标的像不像」的度量思路。单看相关系数很容易被趋势和 Beta 骗到，所以拆成五个维度加权：

| 维度 | 权重 | 它回答的问题 |
|------|------|-------------|
| 方向一致率 | 25% | 是不是**同一天**同涨同跌（最直观，也最不容易被趋势污染） |
| 时间加权收益相关 | 25% | 日收益率的 Pearson 相关，近期数据权重更大 |
| 累计收益形态相似 | 20% | 归一化后累计收益曲线的重合度（看「形状」而非「幅度」） |
| 联动 R² + Beta | 20% | 目标对候选波动的解释力，含弹性系数 |
| 滚动相关稳定性 | 10% | 20 日滚动相关是否持续稳定（用来排除偶然的虚假相关） |

> 之所以要拆维度，是因为**单边 t 值会被 Beta 绑架**。举例：两只股票都跟着大盘涨，
> 单看收益相关会得出「高度联动」，但方向一致率可能只有 55%，形态也完全不像。
> 五个维度一起看，才压得住这类假阳性。

`linkage_analysis.py` / `linkage.py` 是纯 numpy + pandas 实现，无任何 UI 依赖，
可直接在回测脚本里调用。

---

## 快速开始

```bash
git clone https://github.com/skue007/a-share-quant-toolkit.git
cd a-share-quant-toolkit
```

每个工具都是**独立的 Python 包**，按需安装即可，互不依赖。

要求 Python ≥ 3.10。

### 1. 行业错配检测

```bash
cd industry-mismatch

# 装成命令行工具（含网页界面）
pip install ".[ui]"

# 全市场扫描
industry-mismatch scan --out report.json

# 网页界面
industry-mismatch ui     # http://localhost:8501
```

无需任何配置——行业分类缓存内置于包中，行情走 baostock 免费接口。

### 2. 联动选股

```bash
cd linkage-stock-picker
pip install -r requirements.txt

# 配置通达信目录（可选，不配则用 baostock 联网）
cp .env.example .env && vi .env

streamlit run app.py         # Windows 也可双击 run.bat
```

### 3. 自定义形态选股

```bash
cd custom-pattern-screener
pip install -e .

# 生成示例配置
pattern-screener-init-config

# 编辑 config/pattern_config.json 填入你的示例 K 线区间，然后：
pattern-screener -g 反包

# 小规模调试
pattern-screener --limit 200
```

### 4. 板块分时共振

```bash
cd sector-market-resonance
pip install ".[ui]"

# 分析最近一个有涨停池数据的交易日
sector-resonance

# 指定日期与指数
sector-resonance --date 2026-09-01 --index 0.399001

# 交互界面
sector-resonance-ui
```

> 需要系统自带 `curl`（Windows 10+ / macOS / 主流 Linux 均有）。
> 东方财富会按 TLS 指纹屏蔽 `python-requests`，因此该工具统一通过 `curl` 子进程取数。

---

## 目录结构

```
a-share-quant-toolkit/
├── industry-mismatch/          # 行业错配检测（Python 包 + CLI + Streamlit）
│   ├── industry_mismatch/      #   包源码（core / linkage / data_source / cli / app）
│   ├── tests/                  #   单元测试
│   └── pyproject.toml
├── linkage-stock-picker/       # 联动选股（Streamlit 应用）
│   ├── app.py                  #   主页面
│   ├── linkage_analysis.py     #   五维度算法引擎（纯 numpy/pandas，可独立复用）
│   ├── sector_library/         #   板块库（SQLite）
│   └── data/                   #   板块指数映射 + 板块库 + 行业分类缓存
├── custom-pattern-screener/    # 自定义形态选股（Python 包 + CLI）
│   ├── src/pattern_screener/   #   包源码（features / templates / screener / data）
│   ├── tests/
│   └── pyproject.toml
└── sector-market-resonance/    # 板块分时共振（Python 包 + CLI + Streamlit）
    ├── src/sector_resonance/   #   包源码（core / app / ui_entry）
    ├── examples/               #   历史结果示例
    └── pyproject.toml
```

---

## 数据源说明

| 数据 | 来源 | 备注 |
|------|------|------|
| 日线行情（前复权） | baostock / akshare / 本地通达信 | 三方按优先级自动回退 |
| CSRC 行业分类 | baostock | 缓存随包分发，首次运行无需等待 |
| 通达信板块指数映射 | 通达信本地文件 | 约 500 个行业+概念板块 |
| 分时走势、涨停池、行业板块列表 | 东方财富公开接口 | 通过 `curl` 调用 |

各数据源版权归其各自所有者。本项目不对数据准确性做任何担保。

**已知限制**

- 东财 `trends2` 分时接口最多回补约 **5 个交易日**，更早日期无法分析
- 东财对高频请求限流（返回 `rc=102`），默认串行拉取并带指数退避重试，单次分析约 10~30 秒
- 通达信板块指数（880xxx）的分时数据在免费公开源不可得，板块共振口径为东财行业板块（BKxxxx）
- akshare 在线全市场扫描较慢，建议配置本地通达信数据

---

## 常见问题

**Q：一定要装通达信吗？**
不用。三个涉及日线数据的工具都支持纯在线模式（baostock / akshare）自动回退，只是全市场扫描会慢一些。
配置本地通达信只是加速。

**Q：为什么不用 `requests` 调东财接口？**
东财按 TLS 指纹屏蔽了 `python-requests` 的默认握手，返回空响应。改用 `curl` 子进程可以绕开。

**Q：扫描报错 `rc=102`？**
东财限流。等几分钟再试，或降低并发（`--workers 1` 是默认值，已经是最保守的设置）。

**Q：没有命中结果？**
阈值定得太严，或者当天样本确实不支持。放宽阈值（如相关系数 0.2 → 0.1、涨停数 4 → 3）先看候选池长什么样。

---

## 开发与测试

```bash
# 行业错配检测
cd industry-mismatch && pip install ".[dev]" && pytest

# 自定义形态选股
cd custom-pattern-screener && pip install ".[dev]" && pytest

# 板块分时共振（无测试用例，用真实日期冒烟）
cd sector-market-resonance && pip install . && sector-resonance --date 2026-09-01
```

当前测试状态：

| 工具 | 单元测试 | CLI 冒烟 |
|------|---------|---------|
| industry-mismatch | ✅ 16 passed | ✅ |
| custom-pattern-screener | ✅ 19 passed | ✅ |
| linkage-stock-picker | — （无测试用例） | ✅ Streamlit 正常启动 |
| sector-market-resonance | — （无测试用例） | ✅ 涨停池/板块列表拉取正常 |

---

## 免责声明

本项目仅用于**量化研究与技术学习交流**，不构成任何投资建议。

股市有风险，入市需谨慎。使用者基于本工具做出的任何投资决策，风险自负；
因使用本项目产生的任何直接或间接损失，作者不承担任何责任。

数据来源于公开接口，准确性以交易所官方为准。

## License

[MIT](LICENSE) © 2026 skue007
