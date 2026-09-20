"""
板块库数据导入器

从 TDX 本地文件导入板块数据到 SQLite 数据库。
支持全量导入和增量更新。
"""

import os
import sys
import shutil
import hashlib
from datetime import datetime
from typing import Dict, Optional

from .tdx_block_parser import (
    parse_all_tdx_blocks,
    get_tdx_hq_cache_path,
    SectorLibrary,
)
from .database import SectorLibraryDB, DEFAULT_DB_PATH


def compute_file_hash(filepath: str) -> str:
    """计算文件 MD5 哈希，用于检测变更"""
    if not os.path.exists(filepath):
        return ""
    hasher = hashlib.md5()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def get_file_hashes(tdx_hq_cache: str) -> Dict[str, str]:
    """获取所有板块文件的哈希值（含 infoharbor_block.dat）"""
    files = ['block_gn.dat', 'block_fg.dat', 'block_zs.dat', 'infoharbor_block.dat']
    return {f: compute_file_hash(os.path.join(tdx_hq_cache, f)) for f in files}


def full_import(tdx_root: str, db_path: str = DEFAULT_DB_PATH,
                source: str = 'tdx') -> SectorLibraryDB:
    """
    全量导入：解析 TDX 板块文件 → 清空数据库 → 重新导入

    Args:
        tdx_root: 通达信安装根目录
        db_path: SQLite 数据库路径
        source: 数据来源标记 ('tdx')

    Returns:
        SectorLibraryDB 实例
    """
    print(f"[导入] 开始全量导入板块数据...")
    print(f"  TDX 路径: {tdx_root}")
    print(f"  数据库: {db_path}")

    cache = get_tdx_hq_cache_path(tdx_root)

    # 保护一：通达信目录不存在时直接中止，不碰数据库
    if not os.path.isdir(cache):
        raise SystemExit(
            f"[导入] 中止: 未找到通达信数据目录\n"
            f"  期望路径: {cache}\n"
            f"  请用 --tdx-root 指定通达信安装根目录（该目录下应存在 T0002/hq_cache/）。\n"
            f"  本次未对数据库做任何写入。"
        )

    library = parse_all_tdx_blocks(cache)
    print(f"  解析完成: {library.summary()}")

    # 保护二：解析不到任何板块时中止。
    # 否则 --tdx-root 写错（例如指到一个空目录）会先清空现有板块库，
    # 再导入 0 条数据，等于把用户的板块库毁掉。
    if library.sector_count == 0:
        raise SystemExit(
            "[导入] 中止: 未从通达信板块文件中解析到任何板块。\n"
            f"  通达信目录: {tdx_root}\n"
            "  为避免清空现有板块库，本次未对数据库做任何写入。\n"
            "  请确认该目录确为通达信安装根目录，且 T0002/hq_cache/ 下有 block_gn.dat 等板块文件。"
        )

    db = SectorLibraryDB(db_path)

    # 保护三：清库前先备份一份，出问题可回滚
    backup_path = db_path + '.bak'
    try:
        shutil.copy2(db_path, backup_path)
        print(f"  已备份原库: {backup_path}")
    except OSError as exc:
        print(f"  !! 备份失败({exc})，继续导入前请自行确认 {db_path} 可恢复")

    # 清空 TDX 来源的旧数据
    with db._conn() as conn:
        conn.execute("DELETE FROM sector_stocks WHERE source='tdx'")
        conn.execute("DELETE FROM sector_definitions WHERE source='tdx'")

    # 导入
    db.import_from_library(library, source=source)

    # 保存文件哈希用于后续增量检测
    hashes = get_file_hashes(cache)
    _save_import_meta(db_path, hashes, library)

    stats = db.get_statistics()
    print(f"[导入] 完成! 共 {stats['total_sectors']} 个板块, "
          f"{stats['total_stocks']} 只股票, {stats['total_mappings']} 条映射")

    return db


def incremental_update(tdx_root: str, db_path: str = DEFAULT_DB_PATH) -> Optional[SectorLibraryDB]:
    """
    增量更新：检测 TDX 文件变更，仅在有变化时更新

    冲突处理策略：
    - source='tdx' 且未手动修改 → 直接覆盖
    - source='tdx_edited' → 保留手动修改，合并新增成分股
    - source='custom' → 完全跳过

    Returns:
        SectorLibraryDB 如果有更新，否则 None
    """
    cache = get_tdx_hq_cache_path(tdx_root)
    new_hashes = get_file_hashes(cache)
    old_meta = _load_import_meta(db_path)

    # 检查是否有变化
    old_hashes = old_meta.get('file_hashes', {})
    changed_files = [
        f for f in new_hashes
        if new_hashes[f] != old_hashes.get(f, '')
    ]

    if not changed_files:
        print("[更新] TDX 板块文件无变化，跳过更新")
        return None

    print(f"[更新] 检测到 {len(changed_files)} 个文件变更: {changed_files}")

    # 解析新数据
    library = parse_all_tdx_blocks(cache)

    # 增量合并到数据库
    db = SectorLibraryDB(db_path)
    _merge_sectors(db, library)

    _save_import_meta(db_path, new_hashes, library)
    print(f"[更新] 增量合并完成")

    return db


def _merge_sectors(db: SectorLibraryDB, new_library: SectorLibrary):
    """
    增量合并策略：
    - source='tdx': 覆盖更新
    - source='tdx_edited': 保留手动修改，仅追加 TDX 新增的成分股
    - source='custom': 不处理
    """
    existing_sectors = {s['sector_name']: s for s in db.list_sectors()}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    with db._conn() as conn:
        for name, block in new_library.blocks.items():
            existing = existing_sectors.get(name)

            if existing and existing['source'] == 'custom':
                # 手动创建的板块，不触碰
                continue

            if existing and existing['source'] == 'tdx_edited':
                # 手动修改过的板块，只添加新增的成分股
                current_stocks = set(db.get_stocks_in_sector(name))
                new_stocks = set(block.stocks) - current_stocks
                if new_stocks:
                    for code in new_stocks:
                        conn.execute(
                            "INSERT OR IGNORE INTO sector_stocks (sector_name, stock_code, source, added_at) VALUES (?, ?, 'tdx', ?)",
                            (name, code, now)
                        )
                    conn.execute(
                        "UPDATE sector_definitions SET stock_count=(SELECT COUNT(*) FROM sector_stocks WHERE sector_name=?), updated_at=? WHERE sector_name=?",
                        (name, now, name)
                    )
                continue

            # source='tdx' 或新板块：直接覆盖
            conn.execute("""
                INSERT OR REPLACE INTO sector_definitions
                    (sector_name, sector_type, source, stock_count, updated_at)
                VALUES (?, ?, 'tdx', ?, ?)
            """, (name, block.sector_type, len(block.stocks), now))

            # 清除旧股票并重新导入
            conn.execute("DELETE FROM sector_stocks WHERE sector_name=? AND source='tdx'", (name,))
            for code in block.stocks:
                conn.execute(
                    "INSERT OR IGNORE INTO sector_stocks (sector_name, stock_code, source, added_at) VALUES (?, ?, 'tdx', ?)",
                    (name, code, now)
                )

    # 更新 stock_count
    with db._conn() as conn:
        conn.executescript("""
            UPDATE sector_definitions SET stock_count = (
                SELECT COUNT(*) FROM sector_stocks
                WHERE sector_stocks.sector_name = sector_definitions.sector_name
            );
        """)


def _save_import_meta(db_path: str, file_hashes: Dict[str, str],
                      library: SectorLibrary):
    """保存导入元数据"""
    # 将元数据存在数据库目录下的 JSON 文件
    meta_path = os.path.join(os.path.dirname(db_path), 'sector_import_meta.json')
    import json
    meta = {
        'file_hashes': file_hashes,
        'last_import': datetime.now().isoformat(),
        'sector_count': library.sector_count,
        'stock_count': library.stock_count,
    }
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _load_import_meta(db_path: str) -> Dict:
    """加载导入元数据"""
    import json
    meta_path = os.path.join(os.path.dirname(db_path), 'sector_import_meta.json')
    if os.path.exists(meta_path):
        with open(meta_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def ensure_initialized(tdx_root: str = None,
                       db_path: str = DEFAULT_DB_PATH) -> SectorLibraryDB:
    """
    确保板块库已初始化。
    如果数据库不存在或为空，执行全量导入。
    """
    db = SectorLibraryDB(db_path)
    stats = db.get_statistics()

    if stats['total_sectors'] == 0:
        if not tdx_root:
            # 尝试从环境变量或 .env 获取
            tdx_root = os.environ.get('TDX_ROOT', 'D:/program/通达信')
        print(f"[初始化] 板块库为空，执行首次导入...")
        return full_import(tdx_root, db_path)
    else:
        print(f"[初始化] 板块库已存在: {stats['total_sectors']} 个板块")
        return db


# ── 命令行入口 ──────────────────────────────────────────

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='板块库数据导入')
    parser.add_argument('--tdx-root', default='D:/program/通达信', help='通达信根目录')
    parser.add_argument('--db', default=DEFAULT_DB_PATH, help='数据库路径')
    parser.add_argument('--full', action='store_true', help='强制全量导入')
    parser.add_argument('--update', action='store_true', help='增量更新')

    args = parser.parse_args()

    if args.full:
        full_import(args.tdx_root, args.db)
    elif args.update:
        result = incremental_update(args.tdx_root, args.db)
        if result is None:
            print("无需更新")
    else:
        db = ensure_initialized(args.tdx_root, args.db)
        stats = db.get_statistics()
        print(f"\n板块库状态:")
        print(f"  板块总数: {stats['total_sectors']}")
        print(f"  股票总数: {stats['total_stocks']}")
        print(f"  映射总数: {stats['total_mappings']}")
        print(f"  类型分布: {stats['by_type']}")
        print(f"  来源分布: {stats['by_source']}")
