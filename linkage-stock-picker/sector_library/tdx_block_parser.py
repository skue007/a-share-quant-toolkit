"""
TDX 板块二进制文件解析器

解析通达信 T0002/hq_cache/ 下的板块定义文件：
- block_gn.dat → 概念板块
- block_fg.dat → 风格板块
- block_zs.dat → 指数板块

文件格式：Registry ver:1.0 二进制树形结构
每个叶子节点 = 一个板块，包含板块名称（GBK编码）和成分股代码列表。

板块条目结构：
    [GBK名称]\0 [uint16 LE 成分股数量] [uint16 LE 0x0002 标记] [成分股代码...]
    每个成分股代码 = 6位ASCII数字 + \0
"""

import struct
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class SectorBlock:
    """单个板块定义"""
    name: str                          # 板块名称
    sector_type: str                   # "概念" | "风格" | "指数"
    stocks: List[str] = field(default_factory=list)  # 成分股代码列表
    parent_category: str = ""          # 上级分类（从树结构中提取，暂未实现）
    index_code: str = ""               # 对应的板块指数代码（如8805xx），需后续关联


class SectorLibrary:
    """板块库——内存中的板块数据集"""

    def __init__(self):
        self.blocks: Dict[str, SectorBlock] = {}          # name → block
        self.stock_to_sectors: Dict[str, List[str]] = {}  # 股票代码 → 板块名列表
        self.sector_to_stocks: Dict[str, List[str]] = {}  # 板块名 → 股票代码列表

    def add_block(self, block: SectorBlock):
        """添加一个板块到库中"""
        self.blocks[block.name] = block
        self.sector_to_stocks[block.name] = list(block.stocks)
        for stock in block.stocks:
            if stock not in self.stock_to_sectors:
                self.stock_to_sectors[stock] = []
            if block.name not in self.stock_to_sectors[stock]:
                self.stock_to_sectors[stock].append(block.name)

    def get_sectors_for_stock(self, stock_code: str) -> List[str]:
        """查询某只股票所属的所有板块"""
        return self.stock_to_sectors.get(stock_code, [])

    def get_stocks_in_sector(self, sector_name: str) -> List[str]:
        """查询某个板块的所有成分股"""
        return self.sector_to_stocks.get(sector_name, [])

    def get_sector(self, name: str) -> Optional[SectorBlock]:
        """按名称获取板块"""
        return self.blocks.get(name)

    @property
    def sector_count(self) -> int:
        return len(self.blocks)

    @property
    def stock_count(self) -> int:
        return len(self.stock_to_sectors)

    def summary(self) -> str:
        """返回板块库摘要"""
        type_counts = {}
        total_stock_entries = 0
        for b in self.blocks.values():
            type_counts[b.sector_type] = type_counts.get(b.sector_type, 0) + 1
            total_stock_entries += len(b.stocks)
        lines = [f"板块总数: {len(self.blocks)}"]
        for t, c in sorted(type_counts.items()):
            lines.append(f"  {t}: {c} 个")
        lines.append(f"覆盖股票: {len(self.stock_to_sectors)} 只")
        lines.append(f"板块-股票关联: {total_stock_entries} 条")
        return "\n".join(lines)


def _clean_name(raw: bytes) -> str:
    """清理板块名称：去除前后控制字符和空白"""
    # 解码 GBK
    try:
        name = raw.decode('gbk', errors='replace')
    except:
        name = raw.decode('gbk', errors='ignore')

    # 去除前导控制字符（\x00-\x1f 除了换行等）
    name = re.sub(r'^[\x00-\x1f]+', '', name)
    # 去除尾部控制字符和空白
    name = name.rstrip('\x00 \t\r\n')
    return name


def _is_valid_stock_code(code: str) -> bool:
    """检查是否为有效的6位A股代码"""
    if len(code) != 6:
        return False
    if not code.isdigit():
        return False
    # 检查代码范围（上海、深圳、北京）
    if code.startswith(('60', '68')):    # 上海
        return True
    if code.startswith(('00', '30')):    # 深圳
        return True
    if code.startswith(('83', '87', '43', '92')):  # 北京/新三板
        return True
    return False


def parse_tdx_block_file(filepath: str, sector_type: str) -> List[SectorBlock]:
    """
    解析单个 TDX 板块 .dat 文件

    Args:
        filepath: .dat 文件路径
        sector_type: 板块类型标签 ("概念" / "风格" / "指数")

    Returns:
        板块对象列表
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"板块文件不存在: {filepath}")

    with open(filepath, 'rb') as f:
        data = f.read()

    if len(data) < 64:
        raise ValueError(f"文件过小: {len(data)} 字节")

    # 验证文件头
    header = data[:32]
    if b'Registry ver:1.0' not in header:
        raise ValueError(f"不支持的文件格式，期望 Registry ver:1.0，实际: {header[:32]}")

    sectors: List[SectorBlock] = []
    pos = 0

    while pos < len(data) - 15:
        # 搜索标记序列: [uint16 count] [uint16 0x0002]
        if data[pos:pos+2] == b'\x02\x00':
            if pos >= 2:
                count = struct.unpack_from('<H', data, pos-2)[0]

                # 验证：标记后应该是成分股代码（6位数字+\0）
                stock_start = pos + 2
                if stock_start + 7 > len(data):
                    pos += 1
                    continue

                test = data[stock_start:stock_start+7]
                if not (test[:6].isdigit() and test[6] == 0):
                    pos += 1
                    continue

                # 找到了板块条目，提取名称
                # 名称在 count 之前，以 \0 结尾
                name_null = pos - 3  # count(2字节) 前的一个字节应该是 \0
                if name_null <= 0 or data[name_null] != 0:
                    # 尝试 name_null-1（某些条目可能在名称和count之间有多余字节）
                    name_null = pos - 4
                    if name_null <= 0 or data[name_null] != 0:
                        pos += 1
                        continue

                # 向前扫描找到名称起始位置
                name_start = name_null - 1
                while name_start > 0 and data[name_start] != 0:
                    name_start -= 1
                name_start += 1

                if name_start >= name_null:
                    pos += 1
                    continue

                name_bytes = data[name_start:name_null]
                name = _clean_name(name_bytes)

                # 过滤无效名称
                if len(name) < 2:
                    pos += 1
                    continue

                # 检查是否有中文字符或合理的ASCII名称（如"AI人工智能"）
                has_chinese = any(b >= 0x80 for b in name_bytes)
                is_ascii_name = all(0x20 <= b < 0x7f for b in name_bytes)
                if not (has_chinese or is_ascii_name):
                    pos += 1
                    continue

                # 提取成分股代码
                stocks = []
                s = stock_start
                while s < len(data) - 7:
                    sc_raw = data[s:s+7]
                    if sc_raw[6] != 0:
                        break
                    code = sc_raw[:6].decode('ascii', errors='ignore')
                    if code.isdigit() and len(code) == 6:
                        stocks.append(code)
                        s += 7
                    else:
                        break

                if stocks:
                    # 验证数量一致性（允许少量偏差，因为可能有不规则代码）
                    if abs(len(stocks) - count) <= 5 or count == len(stocks):
                        block = SectorBlock(
                            name=name,
                            sector_type=sector_type,
                            stocks=stocks,
                        )
                        sectors.append(block)

                # 跳过已处理的股票代码区域
                pos = s
                continue

        pos += 1

    return sectors


def parse_infoharbor_block_file(filepath: str) -> SectorLibrary:
    """Parse infoharbor_block.dat — TDX new-format concept/style/index block file.

    Format (text-based, GBK encoding):
        #GN_板块名,成分股数量,板块指数代码,创建日期,最后更新日期,,
        0#股票代码1,0#股票代码2,0#股票代码3,...

    Prefixes:
        #GN_ → 概念板块 (concept)
        #FG_ → 风格板块 (style)
        #ZS_ → 指数板块 (index)
    """
    if not os.path.exists(filepath):
        return SectorLibrary()

    PREFIX_TYPE = {
        "#GN_": "概念",
        "#FG_": "风格",
        "#ZS_": "指数",
    }

    library = SectorLibrary()
    lines = []

    with open(filepath, "r", encoding="gbk", errors="ignore") as f:
        lines = [l.strip() for l in f if l.strip()]

    i = 0
    while i < len(lines):
        line = lines[i]

        # Check if this is a header line
        header_match = None
        sector_type = None
        for prefix, stype in PREFIX_TYPE.items():
            if line.startswith(prefix):
                header_match = line
                sector_type = stype
                break

        if header_match is None:
            i += 1
            continue

        # Parse header: #GN_板块名,成分股数量,板块指数代码,创建日期,最后更新日期,,
        parts = header_match.split(",")
        sector_name = parts[0]
        # Strip prefix to get clean name
        for prefix in PREFIX_TYPE:
            if sector_name.startswith(prefix):
                sector_name = sector_name[len(prefix):]
                break

        index_code = parts[2] if len(parts) > 2 else ""
        stock_count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

        # Parse stock codes on the next line
        stocks = []
        if i + 1 < len(lines):
            stock_line = lines[i + 1]
            # Format: 0#000001,0#000002,...
            for token in stock_line.split(","):
                token = token.strip()
                if token.startswith("0#") and len(token) >= 8:
                    code = token[2:8]  # Extract 6-digit code
                    if code.isdigit() and len(code) == 6:
                        stocks.append(code)

        block = SectorBlock(
            name=sector_name,
            sector_type=sector_type,
            stocks=stocks,
            index_code=index_code,
        )
        library.add_block(block)

        # Skip header + stock line
        i += 2

    return library


def parse_all_tdx_blocks(tdx_hq_cache: str) -> SectorLibrary:
    """Parse all TDX block files and build sector library.

    Priority: infoharbor_block.dat (new format, actively updated) takes
    precedence. Falls back to legacy binary .dat files as supplement.
    """
    library = SectorLibrary()

    # ── Primary: infoharbor_block.dat (TDX new format, updated daily) ──
    ih_path = os.path.join(tdx_hq_cache, "infoharbor_block.dat")
    if os.path.exists(ih_path):
        try:
            ih_library = parse_infoharbor_block_file(ih_path)
            for block in ih_library.blocks.values():
                library.add_block(block)
            gn_count = sum(1 for b in ih_library.blocks.values() if b.sector_type == "概念")
            fg_count = sum(1 for b in ih_library.blocks.values() if b.sector_type == "风格")
            zs_count = sum(1 for b in ih_library.blocks.values() if b.sector_type == "指数")
            print(f"[OK] 解析 infoharbor_block.dat: {gn_count} 概念 + {fg_count} 风格 + {zs_count} 指数 = {len(ih_library.blocks)} 板块")
        except Exception as e:
            print(f"[ERROR] 解析 infoharbor_block.dat 失败: {e}")

    # ── Legacy: binary .dat files (supplement for any missing sectors) ──
    file_configs = [
        ('block_gn.dat', '概念'),
        ('block_fg.dat', '风格'),
        ('block_zs.dat', '指数'),
    ]

    for filename, sector_type in file_configs:
        filepath = os.path.join(tdx_hq_cache, filename)
        if not os.path.exists(filepath):
            continue
        try:
            sectors = parse_tdx_block_file(filepath, sector_type)
            added = 0
            for block in sectors:
                if block.name not in library.blocks:
                    library.add_block(block)
                    added += 1
            if added > 0:
                print(f"[OK] 补充 {filename}: +{added} 个{sector_type}板块")
        except Exception as e:
            print(f"[ERROR] 解析 {filename} 失败: {e}")

    return library


def get_tdx_hq_cache_path(tdx_root: str) -> str:
    """获取 TDX 板块缓存目录"""
    return os.path.join(tdx_root, 'T0002', 'hq_cache')


# ── 便捷函数 ────────────────────────────────────────────

def load_sector_library(tdx_root: str) -> SectorLibrary:
    """从 TDX 安装目录加载板块库"""
    cache_path = get_tdx_hq_cache_path(tdx_root)
    return parse_all_tdx_blocks(cache_path)


# ── 命令行入口 ──────────────────────────────────────────

if __name__ == '__main__':
    import sys
    import os

    # 尝试从 .env 读取 TDX_ROOT
    tdx_root = os.environ.get('TDX_ROOT', 'D:/program/通达信')
    if len(sys.argv) > 1:
        tdx_root = sys.argv[1]

    print(f"TDX 路径: {tdx_root}")
    cache = get_tdx_hq_cache_path(tdx_root)
    print(f"板块缓存: {cache}")

    lib = load_sector_library(tdx_root)
    print(f"\n{lib.summary()}")

    # 打印前 20 个概念板块
    gn_blocks = [b for b in lib.blocks.values() if b.sector_type == '概念']
    print(f"\n前 20 个概念板块:")
    for b in gn_blocks[:20]:
        stocks_preview = ', '.join(b.stocks[:5])
        print(f"  {b.name:<20s} ({len(b.stocks):3d} 只) → {stocks_preview}...")

    # 测试查询
    print(f"\n查询 000977（浪潮信息）所属板块:")
    sectors = lib.get_sectors_for_stock('000977')
    for s in sectors:
        print(f"  - {s}")

    print(f"\n查询 '机器人概念' 板块股票数:")
    stocks = lib.get_stocks_in_sector('机器人概念')
    print(f"  {len(stocks)} 只")
