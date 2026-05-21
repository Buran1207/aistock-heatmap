@echo off
chcp 65001 >nul
cd /d %~dp0

echo ============================================
echo  Process iFind exported Excel/CSV only: no API, no quota usage
echo ============================================
echo  Put iFind exports into the ifind_exports folder first.
echo ============================================

if not exist ifind_exports mkdir ifind_exports

where py >nul 2>nul
if %errorlevel%==0 (
    set PY=py -3
) else (
    set PY=python
)

%PY% scripts\process_ifind_exports_only.py --input-dir ifind_exports --outdir deploy_data

if errorlevel 1 (
    echo.
    echo Processing failed. Check the error above.
    pause
    exit /b 1
)

echo.
echo Offline processing finished.
echo.
pause
