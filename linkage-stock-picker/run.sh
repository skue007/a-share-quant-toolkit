#!/usr/bin/env bash
# ═══════════════════════════════════════════════════
#  联动选股 — Linux/macOS 启动脚本
#  依赖安装：pip install -r requirements.txt
# ═══════════════════════════════════════════════════
set -e
cd "$(dirname "$0")"

# 1. 检查 .env（若不存在则从模板复制）
if [ ! -f .env ]; then
    cp .env.example .env
fi

# 2. 启动 Streamlit
exec streamlit run app.py
