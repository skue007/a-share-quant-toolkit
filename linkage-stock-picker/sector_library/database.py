"""
板块库 SQLite 存储层

提供板块定义、板块-股票映射、板块指数日线的 CRUD 操作。
与 theme_cycle/database.py 保持一致的 SQLite 使用模式。

数据库文件: data/sector_library.db
"""

import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from contextlib import contextmanager


DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'sector_library.db')


class SectorLibraryDB:
    """板块库数据库管理"""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_tables()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_tables(self):
        """创建所有表（如果不存在）"""
        with self._conn() as conn:
            conn.executescript("""
                -- 板块定义表
                CREATE TABLE IF NOT EXISTS sector_definitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sector_name TEXT NOT NULL UNIQUE,
                    sector_type TEXT NOT NULL DEFAULT '概念',
                    source TEXT NOT NULL DEFAULT 'tdx',
                    parent_category TEXT DEFAULT '',
                    stock_count INTEGER DEFAULT 0,
                    index_code TEXT DEFAULT '',
                    is_monitored INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    updated_at TEXT DEFAULT (datetime('now','localtime'))
                );

                -- 板块-股票映射表
                CREATE TABLE IF NOT EXISTS sector_stocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sector_name TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT DEFAULT '',
                    source TEXT DEFAULT 'tdx',
                    added_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(sector_name, stock_code)
                );

                -- 板块指数日线数据缓存
                CREATE TABLE IF NOT EXISTS sector_index_daily (
                    sector_name TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL,
                    amount REAL, volume REAL,
                    PRIMARY KEY (sector_name, date)
                );

                -- 手动修改审计日志
                CREATE TABLE IF NOT EXISTS sector_edit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sector_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    old_value TEXT DEFAULT '',
                    new_value TEXT DEFAULT '',
                    edited_at TEXT DEFAULT (datetime('now','localtime'))
                );

                -- 自定义板块分组
                CREATE TABLE IF NOT EXISTS sector_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_name TEXT NOT NULL,
                    sector_name TEXT NOT NULL,
                    sort_order INTEGER DEFAULT 0,
                    UNIQUE(group_name, sector_name)
                );

                -- 索引
                CREATE INDEX IF NOT EXISTS idx_sector_stocks_code
                    ON sector_stocks(stock_code);
                CREATE INDEX IF NOT EXISTS idx_sector_stocks_sector
                    ON sector_stocks(sector_name);
                CREATE INDEX IF NOT EXISTS idx_sector_def_type
                    ON sector_definitions(sector_type);
                CREATE INDEX IF NOT EXISTS idx_sector_def_source
                    ON sector_definitions(source);
                CREATE INDEX IF NOT EXISTS idx_edit_log_sector
                    ON sector_edit_log(sector_name);
            """)

    # ── 板块定义 CRUD ──────────────────────────────────────

    def insert_sector(self, sector_name: str, sector_type: str = '概念',
                      source: str = 'tdx', parent_category: str = '',
                      stock_count: int = 0, index_code: str = '',
                      is_monitored: int = 1) -> bool:
        """插入新板块定义"""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self._conn() as conn:
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO sector_definitions
                        (sector_name, sector_type, source, parent_category,
                         stock_count, index_code, is_monitored, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (sector_name, sector_type, source, parent_category,
                      stock_count, index_code, is_monitored, now))
                return True
            except sqlite3.IntegrityError:
                return False

    def update_sector(self, sector_name: str, **kwargs) -> bool:
        """更新板块定义字段"""
        allowed = {'sector_type', 'source', 'parent_category',
                   'stock_count', 'index_code', 'is_monitored'}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        set_clause = ', '.join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [sector_name]

        with self._conn() as conn:
            conn.execute(
                f"UPDATE sector_definitions SET {set_clause} WHERE sector_name=?",
                values
            )
            return conn.total_changes > 0

    def rename_sector(self, old_name: str, new_name: str) -> bool:
        """重命名板块（级联更新所有关联表）"""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self._conn() as conn:
            # 检查新名称是否已存在
            existing = conn.execute(
                "SELECT 1 FROM sector_definitions WHERE sector_name=?",
                (new_name,)
            ).fetchone()
            if existing:
                return False

            conn.execute(
                "UPDATE sector_definitions SET sector_name=?, source=CASE WHEN source='tdx' THEN 'tdx_edited' ELSE source END, updated_at=? WHERE sector_name=?",
                (new_name, now, old_name)
            )
            conn.execute(
                "UPDATE sector_stocks SET sector_name=? WHERE sector_name=?",
                (new_name, old_name)
            )
            conn.execute(
                "UPDATE sector_index_daily SET sector_name=? WHERE sector_name=?",
                (new_name, old_name)
            )
            conn.execute(
                "UPDATE sector_groups SET sector_name=? WHERE sector_name=?",
                (new_name, old_name)
            )
            # Log edit within same connection
            conn.execute("""
                INSERT INTO sector_edit_log (sector_name, action, old_value, new_value)
                VALUES (?, 'edit_name', ?, ?)
            """, (old_name, old_name, new_name))
            return True

    def delete_sector(self, sector_name: str) -> bool:
        """删除板块及其所有关联数据"""
        with self._conn() as conn:
            conn.execute("DELETE FROM sector_definitions WHERE sector_name=?", (sector_name,))
            conn.execute("DELETE FROM sector_stocks WHERE sector_name=?", (sector_name,))
            conn.execute("DELETE FROM sector_index_daily WHERE sector_name=?", (sector_name,))
            conn.execute("DELETE FROM sector_groups WHERE sector_name=?", (sector_name,))
            self._log_edit(sector_name, 'delete', sector_name, '', conn=conn)
            return True

    def get_sector(self, sector_name: str) -> Optional[Dict]:
        """获取单个板块定义"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sector_definitions WHERE sector_name=?",
                (sector_name,)
            ).fetchone()
            return dict(row) if row else None

    def list_sectors(self, sector_type: str = None, source: str = None,
                     monitored_only: bool = False) -> List[Dict]:
        """列出板块定义"""
        conditions = []
        params = []
        if sector_type:
            conditions.append("sector_type=?")
            params.append(sector_type)
        if source:
            conditions.append("source=?")
            params.append(source)
        if monitored_only:
            conditions.append("is_monitored=1")

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM sector_definitions {where} ORDER BY stock_count DESC",
                params
            ).fetchall()
            return [dict(r) for r in rows]

    # ── 板块-股票映射 CRUD ─────────────────────────────────

    def insert_stock_to_sector(self, sector_name: str, stock_code: str,
                               stock_name: str = '', source: str = 'tdx') -> bool:
        """添加股票到板块"""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self._conn() as conn:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO sector_stocks (sector_name, stock_code, stock_name, source, added_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (sector_name, stock_code, stock_name, source, now))
                return True
            except sqlite3.IntegrityError:
                return False

    def remove_stock_from_sector(self, sector_name: str, stock_code: str) -> bool:
        """从板块移除股票"""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM sector_stocks WHERE sector_name=? AND stock_code=?",
                (sector_name, stock_code)
            )
            self._log_edit(sector_name, 'remove_stock', stock_code, '', conn=conn)
            return True

    def get_stocks_in_sector(self, sector_name: str) -> List[str]:
        """获取板块的所有成分股代码"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT stock_code FROM sector_stocks WHERE sector_name=? ORDER BY stock_code",
                (sector_name,)
            ).fetchall()
            return [r['stock_code'] for r in rows]

    def get_sectors_for_stock(self, stock_code: str) -> List[str]:
        """获取股票所属的所有板块"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT sector_name FROM sector_stocks WHERE stock_code=?",
                (stock_code,)
            ).fetchall()
            return [r['sector_name'] for r in rows]

    def bulk_insert_stocks(self, sector_name: str, stock_codes: List[str],
                           source: str = 'tdx') -> int:
        """批量添加股票到板块"""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        count = 0
        with self._conn() as conn:
            for code in stock_codes:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO sector_stocks (sector_name, stock_code, source, added_at) VALUES (?, ?, ?, ?)",
                        (sector_name, code, source, now)
                    )
                    count += 1
                except sqlite3.IntegrityError:
                    pass
        return count

    def clear_sector_stocks(self, sector_name: str) -> int:
        """清空板块的所有股票"""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM sector_stocks WHERE sector_name=?", (sector_name,)
            )
            return cursor.rowcount

    # ── 批量导入 ────────────────────────────────────────────

    def import_from_library(self, library: 'SectorLibrary', source: str = 'tdx'):
        """从内存 SectorLibrary 批量导入到数据库"""
        from .tdx_block_parser import SectorLibrary
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with self._conn() as conn:
            for name, block in library.blocks.items():
                # 插入板块定义
                conn.execute("""
                    INSERT OR REPLACE INTO sector_definitions
                        (sector_name, sector_type, source, stock_count, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (name, block.sector_type, source, len(block.stocks), now))

                # 批量插入股票
                for code in block.stocks:
                    conn.execute("""
                        INSERT OR IGNORE INTO sector_stocks
                            (sector_name, stock_code, source, added_at)
                        VALUES (?, ?, ?, ?)
                    """, (name, code, source, now))

            # 更新 stock_count
            conn.executescript("""
                UPDATE sector_definitions SET stock_count = (
                    SELECT COUNT(*) FROM sector_stocks
                    WHERE sector_stocks.sector_name = sector_definitions.sector_name
                );
            """)

    def get_statistics(self) -> Dict:
        """获取库统计信息"""
        with self._conn() as conn:
            total_sectors = conn.execute(
                "SELECT COUNT(*) FROM sector_definitions"
            ).fetchone()[0]
            total_stocks = conn.execute(
                "SELECT COUNT(DISTINCT stock_code) FROM sector_stocks"
            ).fetchone()[0]
            total_mappings = conn.execute(
                "SELECT COUNT(*) FROM sector_stocks"
            ).fetchone()[0]
            by_type = conn.execute("""
                SELECT sector_type, COUNT(*) as cnt
                FROM sector_definitions
                GROUP BY sector_type
            """).fetchall()
            by_source = conn.execute("""
                SELECT source, COUNT(*) as cnt
                FROM sector_definitions
                GROUP BY source
            """).fetchall()

        return {
            'total_sectors': total_sectors,
            'total_stocks': total_stocks,
            'total_mappings': total_mappings,
            'by_type': {r['sector_type']: r['cnt'] for r in by_type},
            'by_source': {r['source']: r['cnt'] for r in by_source},
        }

    # ── 分组管理 ────────────────────────────────────────────

    def create_group(self, group_name: str, sector_names: List[str] = None):
        """创建板块分组"""
        with self._conn() as conn:
            if sector_names:
                for i, name in enumerate(sector_names):
                    conn.execute(
                        "INSERT OR REPLACE INTO sector_groups (group_name, sector_name, sort_order) VALUES (?, ?, ?)",
                        (group_name, name, i)
                    )

    def get_group(self, group_name: str) -> List[str]:
        """获取分组中的板块名称"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT sector_name FROM sector_groups WHERE group_name=? ORDER BY sort_order",
                (group_name,)
            ).fetchall()
            return [r['sector_name'] for r in rows]

    def list_groups(self) -> List[str]:
        """列出所有分组名"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT group_name FROM sector_groups ORDER BY group_name"
            ).fetchall()
            return [r['group_name'] for r in rows]

    def delete_group(self, group_name: str):
        """删除分组"""
        with self._conn() as conn:
            conn.execute("DELETE FROM sector_groups WHERE group_name=?", (group_name,))

    # ── 审计日志 ────────────────────────────────────────────

    def _log_edit(self, sector_name: str, action: str,
                  old_value: str = '', new_value: str = '', conn=None):
        """写入编辑日志（可传入已有连接以复用事务）"""
        if conn is not None:
            conn.execute("""
                INSERT INTO sector_edit_log (sector_name, action, old_value, new_value)
                VALUES (?, ?, ?, ?)
            """, (sector_name, action, old_value, new_value))
        else:
            with self._conn() as conn:
                conn.execute("""
                    INSERT INTO sector_edit_log (sector_name, action, old_value, new_value)
                    VALUES (?, ?, ?, ?)
                """, (sector_name, action, old_value, new_value))

    def get_edit_log(self, sector_name: str = None, limit: int = 100) -> List[Dict]:
        """获取编辑日志"""
        with self._conn() as conn:
            if sector_name:
                rows = conn.execute(
                    "SELECT * FROM sector_edit_log WHERE sector_name=? ORDER BY edited_at DESC LIMIT ?",
                    (sector_name, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sector_edit_log ORDER BY edited_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    # ── 板块指数日线 ────────────────────────────────────────

    def insert_index_daily(self, sector_name: str, date: str,
                           open: float, high: float, low: float,
                           close: float, amount: float, volume: float):
        """插入板块指数日线数据"""
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO sector_index_daily
                    (sector_name, date, open, high, low, close, amount, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (sector_name, date, open, high, low, close, amount, volume))

    def get_index_daily(self, sector_name: str, start_date: str = None,
                        end_date: str = None) -> List[Dict]:
        """获取板块指数日线数据"""
        conditions = ["sector_name=?"]
        params = [sector_name]
        if start_date:
            conditions.append("date>=?")
            params.append(start_date)
        if end_date:
            conditions.append("date<=?")
            params.append(end_date)

        where = "WHERE " + " AND ".join(conditions)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM sector_index_daily {where} ORDER BY date",
                params
            ).fetchall()
            return [dict(r) for r in rows]
