@echo off
REM Launch the HIWIN Support Agent Backend on Windows. Run from the repo root.
cd /d "%~dp0"

if not exist ".venv\" (
    echo Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate.bat
pip install -r requirements.txt

if not exist ".env" (
    echo.
    echo WARNING: .env not found. It is normally committed - restore it and set IMAGE_STATIC_ROOT.
    echo.
)

uvicorn main:app --host 0.0.0.0 --port 8079
