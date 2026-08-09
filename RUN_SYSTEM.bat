@echo off
setlocal
cd /d "%~dp0"
title Mahsoobak Hotel ERP Launcher

set "APP_URL=http://127.0.0.1:8000"
set "VENV_PY=.venv\Scripts\python.exe"

echo ==========================================
echo      Mahsoobak Hotel ERP - Launcher
echo ==========================================
echo.

rem Validate the bundled virtual environment. Rebuild it if it is missing or broken.
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo Existing Python environment is not usable on this PC. Rebuilding...
        rmdir /s /q .venv >nul 2>&1
    )
)

if not exist "%VENV_PY%" (
    echo Creating Python environment...
    where py >nul 2>&1
    if not errorlevel 1 (
        py -m venv .venv
    ) else (
        where python >nul 2>&1
        if errorlevel 1 goto :no_python
        python -m venv .venv
    )
    if errorlevel 1 goto :venv_error
)

rem Install dependencies only when they are not already available.
"%VENV_PY%" -c "import fastapi,uvicorn,sqlalchemy,jinja2" >nul 2>&1
if errorlevel 1 (
    echo Installing required packages for first run...
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 goto :install_error
)

echo.
echo Starting Mahsoobak Hotel ERP...
echo Login: admin
echo Password: admin123
echo.

rem Run the backend in its own window so this launcher can open the browser automatically.
start "Mahsoobak Hotel ERP Server" cmd /k ""%VENV_PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

rem Give the local server a moment to start, then open the system in the default browser.
timeout /t 3 /nobreak >nul
start "" "%APP_URL%"

exit /b 0

:no_python
echo.
echo ERROR: Python is not installed or is not available in PATH.
echo Install Python 3 and run this file again.
pause
exit /b 1

:venv_error
echo.
echo ERROR: Could not create the Python environment.
pause
exit /b 1

:install_error
echo.
echo ERROR: Required packages could not be installed.
echo Check the internet connection on the first run, then try again.
pause
exit /b 1
