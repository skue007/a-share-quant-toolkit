@echo off
chcp 65001 >nul
title 联动选股 — Linkage Stock Picker
cd /d "%~dp0"

REM 使用包内自带的 Python（无需安装 Python）
set PATH=%~dp0python;%PATH%

echo ============================================
echo   联动选股 — 启动中，请稍候...
echo   浏览器会自动打开 http://localhost:8765
echo   关闭本窗口即退出程序
echo ============================================

python\python.exe -m streamlit run app\app.py --server.port 8765 --server.headless false

pause
