"""
板块库模块 (Sector Library) — 联动选股独立版

为联动选股提供结构化板块数据。
数据来源：通达信本地文件 → SQLite 数据库（data/sector_library.db，随项目附带）。

主要 API:
    from sector_library import get_db, query_stock_sectors, query_sector_stocks

    db = get_db()                                    # 获取数据库连接
    sectors = query_stock_sectors('000977')          # 查询股票所属板块
    stocks = query_sector_stocks('人工智能')          # 查询板块成分股

    # 板块联动分析（板块指数联动模式）
    from sector_library import build_sector_returns_from_index, load_tdx_sector_index_mapping
"""

import os
import sys
from .database import SectorLibraryDB, DEFAULT_DB_PATH
from .importer import ensure_initialized, full_import, incremental_update

# 板块联动分析
from .sector_linkage import (
    build_sector_linkage_network,
    build_sector_returns_from_stocks,
    build_sector_linkage_table,
    get_sector_linkage_for_stock,
    get_peer_stocks_in_same_sectors,
    get_sector_rotation_heatmap,
    load_tdx_sector_index_mapping,
    build_sector_returns_from_index,
)

# 缓存的数据库实例
_db_instance: SectorLibraryDB = None


def get_db(tdx_root: str = None, auto_init: bool = True) -> SectorLibraryDB:
    """
    获取板块库数据库实例（单例模式）

    Args:
        tdx_root: 通达信根目录（仅在首次初始化、且数据库为空时需要）
        auto_init: 如果数据库为空，是否自动初始化（从 TDX 板块文件导入）

    Returns:
        SectorLibraryDB 实例
    """
    global _db_instance
    if _db_instance is None and auto_init:
        if tdx_root is None:
            tdx_root = os.environ.get('TDX_ROOT', 'D:/program/通达信')
        _db_instance = ensure_initialized(tdx_root, DEFAULT_DB_PATH)
    elif _db_instance is None:
        _db_instance = SectorLibraryDB(DEFAULT_DB_PATH)
    return _db_instance


def reset_db():
    """重置数据库实例缓存"""
    global _db_instance
    _db_instance = None


# ── 便捷查询函数 ──────────────────────────────────────────

def query_stock_sectors(stock_code: str) -> list:
    """查询某只股票所属的所有板块名称"""
    db = get_db()
    return db.get_sectors_for_stock(stock_code)


def query_sector_stocks(sector_name: str) -> list:
    """查询某个板块的所有成分股代码"""
    db = get_db()
    return db.get_stocks_in_sector(sector_name)


def query_sector_info(sector_name: str) -> dict:
    """查询板块详细信息"""
    db = get_db()
    sector = db.get_sector(sector_name)
    if sector:
        sector['stocks'] = db.get_stocks_in_sector(sector_name)
    return sector


def search_sectors(keyword: str, sector_type: str = None) -> list:
    """按关键词搜索板块名称"""
    db = get_db()
    all_sectors = db.list_sectors(sector_type=sector_type)
    return [s for s in all_sectors if keyword in s['sector_name']]


def get_all_concept_sectors() -> list:
    """获取所有概念板块"""
    db = get_db()
    return db.list_sectors(sector_type='概念')


def get_statistics() -> dict:
    """获取板块库统计信息"""
    db = get_db()
    return db.get_statistics()


# ── CLI 支持 ──────────────────────────────────────────────

USAGE = """用法:
  python -m sector_library stats                     查看统计
  python -m sector_library search <关键词>            搜索板块
  python -m sector_library stock <股票代码>           查询股票所属板块
  python -m sector_library sector <板块名称>          查询板块成分股
  python -m sector_library import [--tdx-root PATH]  初始化/导入板块数据

环境变量:
  TDX_ROOT   通达信安装根目录（import 时的默认值）
"""


def _pick_tdx_root(args) -> str:
    """解析通达信根目录。

    兼容两种写法：显式的 `--tdx-root <path>`，以及直接给位置参数；
    都没有时回退到环境变量 TDX_ROOT。
    """
    if '--tdx-root' in args:
        i = args.index('--tdx-root')
        if i + 1 >= len(args):
            raise SystemExit("错误: --tdx-root 后面缺少路径")
        return args[i + 1]
    for a in args:
        if not a.startswith('-'):
            return a
    return os.environ.get('TDX_ROOT', 'D:/program/通达信')


def main(argv=None) -> int:
    """板块库命令行入口。argv 为 None 时取 sys.argv[1:]。"""
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ('-h', '--help', 'help'):
        print(USAGE)
        return 0

    cmd, rest = argv[0], argv[1:]

    if cmd == 'stats':
        stats = get_statistics()
        print(f"板块总数: {stats['total_sectors']}")
        print(f"股票总数: {stats['total_stocks']}")
        print(f"映射总数: {stats['total_mappings']}")
        print(f"类型分布: {stats['by_type']}")
        print(f"来源分布: {stats['by_source']}")

    elif cmd == 'search':
        if not rest:
            print("请提供搜索关键词")
            return 1
        for r in search_sectors(rest[0]):
            print(f"  [{r['sector_type']}] {r['sector_name']} ({r['stock_count']} 只)")

    elif cmd == 'stock':
        if not rest:
            print("请提供股票代码")
            return 1
        code = rest[0]
        sectors = query_stock_sectors(code)
        print(f"{code} 所属板块 ({len(sectors)}):")
        for s in sectors:
            print(f"  - {s}")

    elif cmd == 'sector':
        if not rest:
            print("请提供板块名称")
            return 1
        name = rest[0]
        info = query_sector_info(name)
        if not info:
            print(f"未找到板块: {name}")
            return 1
        print(f"板块: {info['sector_name']}")
        print(f"类型: {info['sector_type']}")
        print(f"来源: {info['source']}")
        stocks = info.get('stocks', []) or []
        print(f"成分股 ({len(stocks)} 只):")
        for s in stocks[:20]:
            print(f"  {s}")
        if len(stocks) > 20:
            print(f"  ... 还有 {len(stocks) - 20} 只")

    elif cmd == 'import':
        full_import(_pick_tdx_root(rest))

    else:
        print(f"未知命令: {cmd}")
        print(USAGE)
        return 2

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
