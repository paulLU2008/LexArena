# ── 基礎映像 ──────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# 設定時區（台灣）
ENV TZ=Asia/Taipei
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 系統套件（chromadb / sentence-transformers 等需要 gcc）
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── 工作目錄 ──────────────────────────────────────────────────────────────────
WORKDIR /app

# ── 安裝 Python 依賴（先複製 requirements.txt，利用 Layer 快取）───────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── 複製其餘所有程式碼與資源 ──────────────────────────────────────────────────
COPY . .

# 賦予啟動腳本執行權限
RUN chmod +x start.sh

# ── 暴露 Port ─────────────────────────────────────────────────────────────────
# Streamlit
EXPOSE 8501
# FastAPI / uvicorn
EXPOSE 8000

# ── 啟動 ──────────────────────────────────────────────────────────────────────
CMD ["bash", "start.sh"]
