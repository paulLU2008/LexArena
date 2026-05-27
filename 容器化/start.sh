#!/bin/bash
set -e

echo "==================================================="
echo "[Starting] Soil Pollution AI Response System"
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

echo "[1/2] Starting FastAPI Gateway (Backend) on port 8000 ..."
./venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 > backend.log 2>&1 &
FASTAPI_PID=$!

echo "[2/2] Starting Streamlit Frontend on port 8501 ..."
STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
  ./venv/bin/python -m streamlit run app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true > frontend.log 2>&1 &
STREAMLIT_PID=$!

echo ""
echo "System Started!"
echo "  Frontend  (Streamlit) : http://localhost:8501"
echo "  Backend API (FastAPI) : http://localhost:8000/docs"
echo "==================================================="

# 等待任一程序結束後，一併終止另一個 (使用相容於 macOS bash 3.2+ 的輪詢機制)
while kill -0 $FASTAPI_PID 2>/dev/null && kill -0 $STREAMLIT_PID 2>/dev/null; do
  sleep 1
done
kill $FASTAPI_PID $STREAMLIT_PID 2>/dev/null