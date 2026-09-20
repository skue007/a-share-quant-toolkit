#!/usr/bin/env python3
"""初始化项目配置文件 — 从包内模板生成示例配置。

用法:
    python scripts/init_config.py
    # 或安装后:
    pattern-screener-init-config
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "src" / "pattern_screener" / "templates"
CONFIG_DIR = PROJECT_ROOT / "config"


def main() -> int:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if not TEMPLATES_DIR.exists():
        # 已 pip 安装时模板位于包内
        import pattern_screener
        TEMPLATES_DIR = Path(pattern_screener.__file__).resolve().parent / "templates"

    copied = []
    for name in ("pattern_config.json", "config.yaml"):
        src = TEMPLATES_DIR / name
        dst = CONFIG_DIR / name.replace(".json", ".example.json").replace(".yaml", ".example.yaml")
        if src.exists():
            shutil.copyfile(src, dst)
            copied.append(dst)

    print("已生成示例配置:")
    for p in copied:
        print(f"  - {p}")
    print()
    print("下一步:")
    print(f"  1. 编辑 {CONFIG_DIR / 'pattern_config.example.json'} 中的 examples（股票代码+日期区间）")
    print(f"  2. 若使用本地通达信，设置环境变量 TDX_ROOT 或编辑 {CONFIG_DIR / 'config.example.yaml'}")
    print("  3. 运行: pattern-screener -c config/pattern_config.example.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
