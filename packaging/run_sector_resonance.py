import sys

from sector_resonance.core import main

if __name__ == "__main__":
    code = main()
    if len(sys.argv) <= 1:
        # 双击运行：结束后停住窗口，让用户看清输出与图表路径
        try:
            input("\n按回车键退出...")
        except EOFError:
            pass
    sys.exit(code)
