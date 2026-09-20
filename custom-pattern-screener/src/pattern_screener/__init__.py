"""自定义形态选股器 (Custom Pattern Screener)

从用户提供的示例（股票代码 + 日期区间）中学习 K 线形态模板，
在全 A 股中做五维（价格/量/实体/上影/涨跌）Pearson 相关性匹配。

数据源: 本地通达信 (mootdx) 优先，akshare 在线回退，Parquet 缓存加速。

GitHub 开源项目，与原有私有系统完全解耦，可独立安装部署。
"""

__version__ = "0.1.0"
