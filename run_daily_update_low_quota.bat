@echo off
chcp 65001 >nul
cd /d %~dp0

rem Optional iFind Python API path. Adjust this if your iFind is installed elsewhere.
set "IFIND_API_DIR=C:\iFinD\THSDataInterface_Windows\bin\x64"
if exist "%IFIND_API_DIR%\iFinDPy.py" (
    set "PYTHONPATH=%IFIND_API_DIR%;%PYTHONPATH%"
    set "PATH=%IFIND_API_DIR%;%PATH%"
)


echo ============================================
echo  HK IPO System - 16:30 Low-Quota Daily Update
echo ============================================
echo  Low-quota rules:
echo  1. Recent-window updates for static IPO tables.
echo  2. Incremental daily quote updates.
echo  3. Close snapshot only for 2024+ IPO stock pool.
echo  4. Keep previous data and write logs if a source fails.
echo ============================================

where py >nul 2>nul
if %errorlevel%==0 (
    set PY=py -3
) else (
    set PY=python
)

%PY% scripts\ifind_low_quota_daily_update.py --mode api --low-quota --build-signals

if errorlevel 1 (
    echo.
    echo Update failed. Check logs\update_YYYYMMDD.log.
    echo If iFind API is unavailable, run: process_ifind_exports_offline.bat
    pause
    exit /b 1
)

echo.
echo Update finished. Run streamlit run streamlit_app.py locally, or push to GitHub for Streamlit Cloud refresh.
echo.
pause
