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
echo  Dry run only: no iFind API call, no quota usage
echo ============================================

where py >nul 2>nul
if %errorlevel%==0 (
    set PY=py -3
) else (
    set PY=python
)

%PY% scripts\ifind_low_quota_daily_update.py --mode dry-run --low-quota --build-signals
pause
