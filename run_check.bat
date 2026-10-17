@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 scope_check.py %*
) else (
  python scope_check.py %*
)
pause
