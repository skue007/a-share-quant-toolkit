"""industry-mismatch — A股行业错配检测工具。

发现「名不副实」的股票：官方行业分类已无法反映其真实业务，
市场按新逻辑定价。通过双范围联动对比（同行业 vs 全市场）自动识别。

核心流程:
  1. 扫描全市场短期暴涨股
  2. 对每只暴涨股，分别在同行业和全市场范围内做联动选股
  3. 若全市场联动得分显著优于同行业 → 判定为行业错配

示例:
  - 301188 力诺药包: 官方=玻璃玻纤，实际=半导体基板材料
  - 600226 亨通股份: 官方=电力，实际=PCB上游电解铜箔

数据源: baostock (免费网络行情 + CSRC 行业分类)
"""

__version__ = "1.0.0"

from .data_source import BaostockDataSource, DataSource
from .industry_data import (
    get_all_stocks_code,
    get_same_industry_stocks,
    get_sibling_industry_stocks,
    get_stock_industry_map,
    get_target_industry,
    get_target_name,
)
from .linkage import find_linked_stocks
from .core import (
    DetectionConfig,
    analyze_industry_distribution,
    compute_divergence_metrics,
    run_detection,
    scan_surge_stocks,
)

__all__ = [
    "__version__",
    "BaostockDataSource",
    "DataSource",
    "get_all_stocks_code",
    "get_same_industry_stocks",
    "get_sibling_industry_stocks",
    "get_stock_industry_map",
    "get_target_industry",
    "get_target_name",
    "find_linked_stocks",
    "analyze_industry_distribution",
    "compute_divergence_metrics",
    "run_detection",
    "scan_surge_stocks",
    "DetectionConfig",
]
