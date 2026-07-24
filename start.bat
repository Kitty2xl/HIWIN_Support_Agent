@echo off
REM =====================================================================
REM One-command start for the HIWIN Support Agent (Windows).
REM Brings up the llama.cpp router (all models resident) + the FastAPI
REM backend, so it's ready for POST /chat. Run from the repo root.
REM
REM Assumes Postgres is already running (it only warns if it can't reach it).
REM For a backend-only start (router already up), use run.bat instead.
REM =====================================================================
setlocal
cd /d "%~dp0"

REM ---- CONFIG: edit these for your machine (or set them as env vars) ----
if "%LLAMA_SERVER%"=="" set "LLAMA_SERVER=C:\Users\User_11\Desktop\llama\llama-server.exe"
if "%PRESET%"=="" set "PRESET=%~dp0config.ini"
if "%ROUTER_HOST%"=="" set "ROUTER_HOST=127.0.0.1"
if "%ROUTER_PORT%"=="" set "ROUTER_PORT=11400"
REM ----------------------------------------------------------------------

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

echo Starting llama.cpp router (mode: --models-preset) on %ROUTER_HOST%:%ROUTER_PORT% ...
start "llama-router" "%LLAMA_SERVER%" --models-preset "%PRESET%" --host %ROUTER_HOST% --port %ROUTER_PORT%

echo Waiting for the router to come up...
:waitloop
curl -sf "http://%ROUTER_HOST%:%ROUTER_PORT%/v1/models" >nul 2>&1
if errorlevel 1 (
    timeout /t 2 >nul
    goto waitloop
)
echo Router is up (models load in the background via load-on-startup).

python -c "import psycopg2, config; psycopg2.connect(dbname=config.DB_NAME, user=config.DB_USER, password=config.DB_PASSWORD, host=config.DB_HOST, port=config.DB_PORT, sslmode=config.DB_SSLMODE).close()" 2>nul
if errorlevel 1 echo WARNING: could not reach Postgres - make sure it is running and .env DB_* are set.

echo Starting the backend on port 8079 ...
uvicorn main:app --host 0.0.0.0 --port 8079
