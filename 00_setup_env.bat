@echo off
chcp 65001 >nul
cd /d %~dp0

echo ============================================
echo  HK IPO System - Setup Python Environment
echo ============================================

where py >nul 2>nul
if %errorlevel%==0 (
    set PY=py -3
) else (
    set PY=python
)

%PY% --version
if errorlevel 1 (
    echo Python was not found. Please install Python 3.10 or above.
    pause
    exit /b 1
)

%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt

echo.
echo Done. Next, run: run_daily_update_low_quota.bat
echo.
pause
