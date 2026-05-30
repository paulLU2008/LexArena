#!/bin/bash
set -e

echo "==================================================="
echo "[Starting] LexArena AI Mock Court System"
echo "==================================================="

# 自動偵測並建立虛擬環境
if [ ! -d "venv" ]; then
    echo "[Init] 找不到虛擬環境 (venv)，系統將自動為您建立..."
    # 優先嘗試 python3，若失敗則退回 python
    if command -v python3 &>/dev/null; then
        python3 -m venv venv
    else
        python -m venv venv
    fi
    echo "[Init] 正在安裝必要套件 (requirements.txt)..."
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt
    echo "[Init] 初始化完成！"
    echo "==================================================="
fi

echo "[1/1] Starting LexArena Gateway (Backend & Frontend) on port 8000 ..."
./venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 > backend.log 2>&1 &
FASTAPI_PID=$!

echo ""
echo "System Started!"
echo "  LexArena UI  : http://localhost:8000/"
echo "  Backend API  : http://localhost:8000/docs"
echo "==================================================="

# 輪詢機制等待程序結束
while kill -0 $FASTAPI_PID 2>/dev/null; do
  sleep 1
done
kill $FASTAPI_PID 2>/dev/null