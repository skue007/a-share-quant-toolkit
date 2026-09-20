"""`python -m sector_library` 的入口。

CLI 逻辑本体放在包的 ``__init__.py``（函数 ``main``）里，这里只负责把它转出来，
使 README 中记录的用法可以直接执行：

    python -m sector_library stats
    python -m sector_library search 人工智能
    python -m sector_library stock 000977
    python -m sector_library sector 人工智能
    python -m sector_library import --tdx-root "D:/program/通达信"
"""

from . import main

if __name__ == '__main__':
    raise SystemExit(main())
