import sys

from industry_mismatch.cli import main

if __name__ == "__main__":
    if len(sys.argv) <= 1:
        # 双击运行（无参数）：默认执行全市场扫描，结束后停住窗口不闪退
        print("未指定子命令，默认执行全市场扫描（scan，结果写入 ~/.industry_mismatch/output/）。")
        print("命令行用法：industry-mismatch scan -h / industry-mismatch ui\n")
        code = main(["scan"])
        try:
            input("\n扫描结束，按回车键退出...")
        except EOFError:
            pass
    else:
        code = main()
    sys.exit(code)
