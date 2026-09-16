@echo off
REM Backend-only launcher (Windows): use when the llama.cpp router is ALREADY running.
REM Creates the venv on first use, installs requirements, runs the preflight checks,
REM then serves POST /chat on port 8079. Run from the repo root.
REM For router + backend in one go use start.bat instead.
cd /d "%~dp0"

REM ---- Find Python 3.12 (python on PATH, else the py launcher, else common installs) ----
set "PYCMD="
if defined PYTHON set "PYCMD=%PYTHON%"
if not defined PYCMD (python --version >nul 2>&1 && set "PYCMD=python")
if not defined PYCMD (py -3.12 --version >nul 2>&1 && set "PYCMD=py -3.12")
if not defined PYCMD (if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYCMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe")
if not defined PYCMD (if exist "C:\Python312\python.exe" set "PYCMD=C:\Python312\python.exe")
if not defined PYCMD (if exist "C:\ProgramData\anaconda3\python.exe" set "PYCMD=C:\ProgramData\anaconda3\python.exe")
if not defined PYCMD (
    echo ERROR: Python 3.12 was not found. Install it from python.org ^(tick "Add to PATH"^),
    echo        or set PYTHON=C:\path\to\python.exe before running this script.
    exit /b 1
)

if not exist ".venv\" (
    echo Creating virtual environment with %PYCMD% ...
    %PYCMD% -m venv .venv || (echo ERROR: could not create .venv & exit /b 1)
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
