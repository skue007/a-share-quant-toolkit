@echo off
REM ═══════════════════════════════════════════════════
REM  联动选股 — Windows 启动脚本
REM  依赖安装：pip install -r requirements.txt
REM ═══════════════════════════════════════════════════
chcp 65001 >nul
cd /d "%~dp0"

REM 1. 清理 Python 字节码缓存
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"

REM 2. 检查 .env（若不存在则从模板复制）
if not exist ".env" copy /y ".env.example" ".env" >nul

REM 3. 启动 Streamlit
streamlit run app.py
