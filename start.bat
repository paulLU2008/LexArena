@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ===================================================
echo [Starting] Soil Pollution AI Response System
echo ===================================================

echo [1/2] Starting FastAPI Gateway (Backend) ...
start "FastAPI Backend" cmd /k "python -m uvicorn main:app --host 0.0.0.0 --port 8000"

echo [2/2] Starting Streamlit Frontend ...
start "Streamlit Frontend" cmd /k "set STREAMLIT_BROWSER_GATHER_USAGE_STATS=false & python -m streamlit run app.py --server.port 8501"

echo.
echo System Started!
echo.
echo Frontend (Streamlit): http://localhost:8501
echo Backend API (FastAPI): http://localhost:8000/docs
echo ===================================================
pause
