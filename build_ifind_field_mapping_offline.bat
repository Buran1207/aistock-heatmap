@echo off
chcp 65001 >nul
cd /d %~dp0

echo ============================================
echo  Build iFind field mapping from exported CSV/Excel only
echo  No API call, no quota usage
echo ============================================

if not exist ifind_exports mkdir ifind_exports

where py >nul 2>nul
if %errorlevel%==0 (
    set PY=py -3
) else (
    set PY=python
)

%PY% scripts\build_ifind_field_mapping_from_exports.py --input-dir ifind_exports
pause
