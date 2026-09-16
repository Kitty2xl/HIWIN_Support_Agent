@echo off
REM Backend-only launcher (Windows): use when the llama.cpp router is ALREADY running.
REM Creates the venv on first use, installs requirements, runs the preflight checks,
REM then serves POST /chat on port 8079. Run from the repo root.
REM For router + backend in one go use start.bat instead.
cd /d "%~dp0"

if not exist ".venv\" (
    echo Creating virtual environment...
    python -m venv .venv || (echo ERROR: Python 3.12 not on PATH & exit /b 1)
)

call .venv\Scripts\activate.bat
pip install -q -r requirements.txt

if not exist ".env" (
    echo.
    echo WARNING: .env not found. It is normally committed - restore it, or run: python env_from_settings.py
    echo.
)

python doctor.py
if errorlevel 1 (
    echo.
    echo WARNING: doctor.py reported failures - starting anyway; fix them if /chat misbehaves.
    echo.
)

uvicorn main:app --host 0.0.0.0 --port 8079
