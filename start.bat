@echo off
REM =====================================================================
REM One-command start for the HIWIN Support Agent (Windows).
REM Brings up the llama.cpp router (all serving models resident) + the
REM FastAPI backend, so it is ready for POST /chat. Run from the repo root
REM (double-click works). Ctrl+C stops the backend; close the "llama-router"
REM window to stop the models.
REM
REM Assumes PostgreSQL is already running (it only warns if it cannot reach it).
REM Backend-only start (router already up): run.bat
REM Something wrong?                          python doctor.py
REM =====================================================================
setlocal
cd /d "%~dp0"

REM ---- CONFIG: edit these for your machine (or set them as env vars) ----
if "%LLAMA_SERVER%"=="" set "LLAMA_SERVER=C:\Users\User_11\Desktop\llama\llama-server.exe"
if "%PRESET%"=="" set "PRESET=%~dp0config.ini"
if "%ROUTER_HOST%"=="" set "ROUTER_HOST=127.0.0.1"
if "%ROUTER_PORT%"=="" set "ROUTER_PORT=11400"
REM How many models may be resident at once. 4 = the serving set. When the
REM pipeline runs, its two models evict the least-recently-used serving models
REM (they reload on the next /chat). Raise to 6 only if the VRAM allows it.
if "%MODELS_MAX%"=="" set "MODELS_MAX=4"
REM Which GPU(s) the router may use. On a shared multi-GPU box pin ONE card
REM (index as shown by nvidia-smi); delete the CUDA_VISIBLE_DEVICES line to let
REM llama.cpp use every GPU. PCI_BUS_ID makes CUDA index N == nvidia-smi index N.
set "CUDA_DEVICE_ORDER=PCI_BUS_ID"
if "%CUDA_VISIBLE_DEVICES%"=="" set "CUDA_VISIBLE_DEVICES=0"
REM ----------------------------------------------------------------------

if not exist "%LLAMA_SERVER%" (
    echo ERROR: llama-server not found at "%LLAMA_SERVER%".
    echo        Edit LLAMA_SERVER at the top of start.bat or set the env var.
    exit /b 1
)

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

echo Starting llama.cpp router on %ROUTER_HOST%:%ROUTER_PORT% (preset %PRESET%, GPU %CUDA_VISIBLE_DEVICES%) ...
start "llama-router" "%LLAMA_SERVER%" --models-preset "%PRESET%" --host %ROUTER_HOST% --port %ROUTER_PORT% --models-max %MODELS_MAX%

echo Waiting for the router to come up...
:waitloop
curl -sf "http://%ROUTER_HOST%:%ROUTER_PORT%/v1/models" >nul 2>&1
if errorlevel 1 (
    timeout /t 2 >nul
    goto waitloop
)
echo Router answers; waiting for the load-on-startup models to finish loading (about a minute)...
python doctor.py --wait-models

echo.
echo Running preflight checks (python doctor.py) ...
python doctor.py
if errorlevel 1 (
    echo.
    echo WARNING: doctor.py reported failures - the backend will start anyway, but fix them if /chat misbehaves.
    echo.
)

echo Starting the backend on port 8079 ...
uvicorn main:app --host 0.0.0.0 --port 8079
