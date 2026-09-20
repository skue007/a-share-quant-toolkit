# 自定义形态选股器 (Custom Pattern Screener)

> 从示例 K 线学习形态，在全 A 股中做五维 Pearson 相关性匹配，帮你找出"长得像"的股票。

本项目是一个**独立开源模块**，不依赖任何内部系统：
你只需提供少量"理想形态"的示例（股票代码 + 日期区间），程序会自动学习形态模板，
然后在全市场扫描与之相似的走势，输出候选股票 CSV 报告。

## 特性

- 🧬 **示例驱动**：给几个代码 + 日期区间，自动学习形态，无需手工定义指标规则
- 🎯 **五维特征匹配**：价格曲线 / 成交量 / K线实体 / 上影线 / 日涨跌幅 加权 Pearson 相关
- ⚡ **多组形态分组**：一份配置可同时管理多套形态（如"反包"、"趋势回调"），一键切换
- 🚀 **并发扫描**：全市场 5000+ 只股票多线程扫描，带进度与 ETA
- 🗄️ **多数据源**：本地通达信 (mootdx) 优先，akshare 在线回退，Parquet 缓存加速
- 🧩 **过滤器**：趋势预筛、MA5 涨幅约束、大跌前价格稳定、最低成交额过滤
- 📦 **零私有依赖**：不依赖任何内部系统，`pip install` 即可独立部署

## 原理

1. **模板学习**：对每个示例区间，提取 5 条归一化特征曲线
   - 价格 → 累计收益率曲线（首日 = 0）
   - 成交量 → 相对 20 日均量比
   - 实体 → `|close - open| / (high - low)`
   - 上影 → `(high - max(open, close)) / (high - low)`
   - 涨跌 → 单日涨跌幅序列
2. **重采样平均**：所有示例曲线 `np.interp` 重采样到统一长度后取平均，得到形态模板
3. **全市场匹配**：对每只股票取最近窗口，计算 5 维 Pearson 相关系数，按权重加权求和
4. **阈值筛选**：综合得分超过 `similarity_threshold` 即视为命中，按得分排序输出 Top N

## 安装

要求 Python >= 3.10。

```bash
# 方式一：源码安装（推荐，方便二次开发）
git clone https://github.com/skue007/a-share-quant-toolkit.git
cd a-share-quant-toolkit/custom-pattern-screener
pip install -e .

# 方式二：仅安装依赖后直接运行
pip install -r requirements.txt

# 方式三（仅在线数据源，无本地通达信）
pip install -e ".[online-only]"
```

## 快速开始

### 1. 生成示例配置

```bash
pattern-screener-init-config
# 或源码方式: python scripts/init_config.py
```

会在 `config/` 下生成 `pattern_config.example.json` 和 `config.example.yaml`。

### 2. 配置数据源

**方式 A：本地通达信（推荐，快且稳定）**

```bash
export TDX_ROOT="D:/program/通达信"      # Windows
# export TDX_ROOT="/opt/tdx"              # Linux/macOS 挂载目录
```

> 要求通达信安装目录下存在 `vipdoc/sh/lday`、`vipdoc/sz/lday`（日线数据）。
> 可在通达信中通过"盘后数据下载"更新日线。

**方式 B：纯在线（akshare，无需本地数据）**

不设置 `TDX_ROOT` 即可，程序会自动回退到 akshare 在线接口（需联网，全市场扫描会较慢）。

也可以编辑 `config/config.yaml`：

```yaml
tdx:
  root_dir: "D:/program/通达信"   # 留空 = 纯在线模式
data:
  prefer_local: true
  cache_dir: "data/cache"
  cache_ttl:
    daily: 1
```

### 3. 编辑形态配置

编辑 `config/pattern_config.example.json`（复制为 `pattern_config.json` 更佳），
把 `examples` 换成你想要的形态示例：

```json
{
  "active_group": "默认形态",
  "groups": {
    "默认形态": {
      "examples": [
        { "code": "600519", "start": "2024-04-01", "end": "2024-04-15" },
        { "code": "000858", "start": "2024-04-03", "end": "2024-04-17" }
      ],
      "similarity_threshold": 0.7,
      "max_results": 90,
      "lookback_days": 60,
      "price_weight": 0.4,
      "volume_weight": 0.15,
      "body_weight": 0.1,
      "shadow_weight": 0.05,
      "return_weight": 0.3
    }
  }
}
```

### 4. 运行选股

```bash
# 使用默认分组
pattern-screener

# 指定形态分组
pattern-screener -g 反包

# 自定义配置与输出目录
pattern-screener -c config/pattern_config.json -o reports

# 演示模式：只扫前 200 只（调试用）
pattern-screener --limit 200

# 历史回测：以指定日期为截止日扫描
pattern-screener --reference-date 2024-12-31

# 源码方式运行
python -m pattern_screener -g 反包
```

### 5. 查看结果

CSV 报告输出到 `reports/custom_pattern_<分组>_<日期>.csv`：

| 字段 | 说明 |
|------|------|
| code / name | 股票代码 / 名称 |
| match_date | 匹配日期（最新交易日） |
| score | 五维加权综合得分（越高越像） |
| price_corr / volume_corr / body_corr / shadow_corr / return_corr | 各维 Pearson 相关系数 |
| current_price | 当前收盘价 |
| amount_亿 | 匹配日成交额（亿元） |

## 形态分组配置说明

| 参数 | 默认 | 说明 |
|------|------|------|
| `examples` | 必填 | 示例列表：`[{code, start, end}]`，至少 1 条，建议 2~5 条 |
| `similarity_threshold` | 0.85 | 综合得分阈值，越高越严格 |
| `max_results` | 200 | 输出结果上限 |
| `lookback_days` | 120 | 每只股票的扫描回溯窗口（天） |
| `price_weight` | 0.35 | 价格维度权重（五维权重自动归一化） |
| `volume_weight` | 0.15 | 成交量维度权重 |
| `body_weight` | 0.15 | K线实体维度权重 |
| `shadow_weight` | 0.15 | 上影线维度权重 |
| `return_weight` | 0.20 | 日涨跌幅维度权重 |
| `window_days` | null | 模板长度，null = 取示例长度的中位数 |
| `trend_filter.enabled` | false | 是否启用趋势预筛（近 20 日涨幅下限 + MA5>MA10） |
| `ma5_max_growth` | 99.0 | MA5 峰值相对 4 日前涨幅上限（过滤短期暴涨） |
| `pre_crash_max_drop` | 0.99 | 匹配日前 4 日最大回撤上限（过滤大跌后反弹） |
| `min_amount` | 0.0 | 匹配日最低成交额（亿元），过滤流动性差的股票 |

## 数据源架构

```
┌─────────────────────────────────────────────────┐
│  HybridProvider                                 │
│  1. Parquet 缓存 (fresh → 直接读)               │
│  2. 本地通达信 TDXReader (mootdx, 前复权)       │
│  3. akshare 在线 OnlineReader (自动回退)        │
└─────────────────────────────────────────────────┘
```

- 所有成功读取的数据都会写回 Parquet 缓存，二次运行显著加速
- 缓存有效期默认 1 天，过期后若 TDX 源文件更新则自动失效重读

## 目录结构

```
custom-pattern-screener/
├── src/pattern_screener/
│   ├── cli.py            # 命令行入口
│   ├── config.py         # 形态分组配置加载与校验
│   ├── features.py       # 特征工程纯函数（可独立测试）
│   ├── templates.py      # 形态模板学习
│   ├── screener.py       # 全市场并发扫描
│   ├── reporting.py      # 名称补齐 + CSV 输出
│   ├── init_config.py    # 初始化示例配置
│   ├── templates/        # 内置示例配置模板
│   └── data/             # 数据层：HybridProvider 等
├── scripts/init_config.py
├── tests/                # 单元测试
├── config/               # 你的本地配置（不入库）
├── reports/              # 扫描结果（不入库）
├── requirements.txt
└── pyproject.toml
```

## 开发与测试

```bash
pip install -e ".[dev]"
pytest                       # 运行单元测试
python -m pattern_screener --limit 100   # 小规模冒烟测试
```

## 常见问题

**Q: 扫描太慢？**
- 优先配置本地通达信（akshare 在线全市场较慢）
- 调低 `lookback_days`；`--limit` 只用于调试

**Q: 提示找不到通达信数据？**
确认 `TDX_ROOT` 指向的目录下有 `vipdoc/sh/lday` 且已下载日线；
否则程序会回退到 akshare 在线模式。

**Q: 没有命中结果？**
- 降低 `similarity_threshold`（如 0.7 → 0.5）
- 检查示例区间是否太特殊（如停牌、一字板）
- 运行时会打印"自检"：若示例与模板 combined < 0.7，说明示例之间差异过大

**Q: 名称显示为空？**
新浪行情接口可能暂时不可用，不影响结果数据，可自行映射。

## 免责声明

本项目仅供技术学习与研究交流，不构成任何投资建议。股市有风险，投资需谨慎。
使用者因使用本项目产生的任何直接或间接损失，作者不承担任何责任。

## License

[MIT](LICENSE)
