# -*- coding: utf-8 -*-
"""`sector-resonance-ui` 命令入口：以 streamlit run 启动交互界面。"""

import sys
from pathlib import Path


def main() -> None:
    try:
        from streamlit.web import cli as stcli
    except ImportError:
        print("未安装 Streamlit。请先执行:  pip install 'sector-resonance[ui]'")
        sys.exit(1)
    app_path = Path(__file__).resolve().with_name("app.py")
    sys.argv = ["streamlit", "run", str(app_path)] + sys.argv[1:]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
