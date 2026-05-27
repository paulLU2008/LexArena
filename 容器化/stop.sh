#!/bin/bash

echo "==================================================="
echo "[Stopping] Soil Pollution AI Response System"
echo "==================================================="

echo "正在終止 FastAPI 後端服務 (Port 8000)..."
lsof -ti :8000 | xargs kill -9 2>/dev/null || echo "  -> FastAPI 尚未啟動或已關閉。"

echo "正在終止 Streamlit 前端服務 (Port 8501)..."
lsof -ti :8501 | xargs kill -9 2>/dev/null || echo "  -> Streamlit 尚未啟動或已關閉。"

# 雙重保險：透過指令名稱精準撲殺殘留程序
pkill -f "uvicorn main:app" 2>/dev/null || true
pkill -f "streamlit run app.py" 2>/dev/null || true

echo "==================================================="
echo "系統已完全停止！✅"
echo "==================================================="
