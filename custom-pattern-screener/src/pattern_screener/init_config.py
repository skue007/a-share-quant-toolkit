"""初始化项目配置文件 — 生成示例配置到 ./config/。

pip 安装后通过 `pattern-screener-init-config` 命令调用；
源码方式可直接运行 scripts/init_config.py。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

CONFIG_DIR = Path.cwd() / "config"


def main(argv: list[str] | None = None) -> int:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # 模板位于包内 templates/ 目录
    templates_dir = Path(__file__).resolve().parent / "templates"

    copied = []
    for name in ("pattern_config.json", "config.yaml"):
        src = templates_dir / name
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
