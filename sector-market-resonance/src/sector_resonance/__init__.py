# -*- coding: utf-8 -*-
"""sector-resonance — A股板块-大盘分时共振分析。

数据源：东方财富免费接口（无需鉴权）。
核心逻辑见 core.py；Streamlit 交互界面见 app.py（`sector-resonance-ui` 启动）。
"""

from sector_resonance.core import (
    DEFAULT_INDEX,
    INDEX_ALIAS,
    analyze,
    board_metrics,
    detect_v_reversal,
    fetch_board_list,
    fetch_trends,
    fetch_zt_pool,
    parse_trends,
    plot_candidates_chart,
    plot_chart,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_INDEX",
    "INDEX_ALIAS",
    "analyze",
    "board_metrics",
    "detect_v_reversal",
    "fetch_board_list",
    "fetch_trends",
    "fetch_zt_pool",
    "parse_trends",
    "plot_candidates_chart",
    "plot_chart",
    "__version__",
]
