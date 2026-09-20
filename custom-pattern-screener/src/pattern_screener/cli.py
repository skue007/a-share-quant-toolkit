"""命令行入口。

用法示例:
    pattern-screener -g 反包
    pattern-screener --config config/pattern_config.json -g 默认形态 --limit 200
    pattern-screener --reference-date 2026-01-15   # 历史回测
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import load_config
from .data import HybridProvider
from .data.config import DataSourceConfig
from .reporting import enrich_names, post_filter_st, dedup, save_results
from .screener import get_stock_list, run_scan, log
from .templates import build_templates

DEFAULT_CONFIG = "config/pattern_config.json"
DEFAULT_YAML = "config/config.yaml"
DEFAULT_OUTPUT = "reports"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pattern-screener",
        description="自定义形态学习选股器: 从示例 K 线学习形态，在全 A 股中匹配",
    )
    parser.add_argument("-g", "--group", default=None,
                        help="形态分组名称（不指定则使用配置中的 active_group）")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG,
                        help=f"形态配置 JSON 路径 (默认: {DEFAULT_CONFIG})")
    parser.add_argument("-y", "--yaml", dest="yaml_path", default=DEFAULT_YAML,
                        help=f"数据源配置 YAML 路径 (默认: {DEFAULT_YAML})")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT,
                        help=f"结果输出目录 (默认: {DEFAULT_OUTPUT})")
    parser.add_argument("--limit", type=int, default=None,
                        help="仅扫描前 N 只股票（演示/调试用，默认全市场）")
    parser.add_argument("--reference-date", default=None,
                        help="回测指定历史日期 YYYY-MM-DD（不传则用最新交易日）")
    parser.add_argument("--max-workers", type=int, default=16,
                        help="并发线程数 (默认: 16)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="输出调试日志")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 项目根目录（用于解析相对路径）
    project_root = Path.cwd()

    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = str(project_root / config_path)

    print("=" * 64)
    print("   自定义形态学习选股器")
    print(f"   版本: {__version__}")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   配置: {config_path}")
    print("=" * 64)
    print()

    # 1. 加载形态配置
    config = load_config(config_path, group_name=args.group)
    log(f"已加载 {len(config['examples'])} 个示例")
    log(f"  阈值={config['similarity_threshold']}  "
        f"权重: 价格={config['price_weight']} 量={config['volume_weight']} "
        f"实体={config['body_weight']} 上影={config['shadow_weight']} "
        f"涨跌={config['return_weight']}")
    log(f"  回溯天数={config['lookback_days']}  最大结果={config['max_results']}")

    # 2. 数据源配置
    yaml_path = args.yaml_path
    if not os.path.isabs(yaml_path):
        yaml_path = str(project_root / yaml_path)
    ds_cfg = DataSourceConfig.load(yaml_path)
    cache_dir = ds_cfg.resolve_cache_dir(str(project_root))

    provider = HybridProvider(
        tdx_root=ds_cfg.tdx_root,
        cache_dir=cache_dir,
        prefer_local=ds_cfg.prefer_local,
        cache_ttl=ds_cfg.cache_ttl,
    )
    if provider.tdx is None:
        log("警告: 未找到本地通达信数据，将使用 akshare 在线数据（需联网）")

    # 3. 构建模板
    templates = build_templates(provider, config)
    log(f"\n模板已构建: 长度={templates['target_len']}天  有效示例={templates['valid_count']}个")

    # 4. 获取股票列表
    log("\n[Phase 3] 获取A股列表...")
    stock_df = get_stock_list(provider, limit=args.limit)
    stocks = stock_df.to_dict("records")
    total = len(stocks)

    if total == 0:
        log("没有通过预筛选的股票，退出。")
        return 1

    # 5. 并发扫描
    all_results = run_scan(provider, stocks, templates, config,
                           max_workers=args.max_workers)

    # 6. 补齐名称 / 过滤 ST / 去重 / 保存
    log("\n[Phase 5] 补齐名称并保存...")
    enrich_names(all_results)
    all_results = post_filter_st(all_results)
    all_results = dedup(all_results)

    output_dir = args.output
    if not os.path.isabs(output_dir):
        output_dir = str(project_root / output_dir)
    saved = save_results(all_results, config, output_dir)

    # Summary
    print()
    print("=" * 64)
    print("   扫描完成")
    print("=" * 64)
    print(f"   扫描股票    : {total} 只")
    print(f"   形态匹配    : {len(all_results)} 条")
    print(f"   报告目录    : {output_dir}")
    print()

    if all_results:
        print("=" * 64)
        print("   Top 10 匹配结果")
        print("=" * 64)
        for i, r in enumerate(all_results[:10]):
            print(f"  {i+1:2d}. {r['code']} {r['name']}   "
                  f"match={r['match_date']}  "
                  f"score={r['score']:.3f}  "
                  f"P={r['price_corr']:.2f} V={r['volume_corr']:.2f} "
                  f"B={r['body_corr']:.2f} S={r['shadow_corr']:.2f} "
                  f"R={r['return_corr']:.2f}")
        print()

    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
