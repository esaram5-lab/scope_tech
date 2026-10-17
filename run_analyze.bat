@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 analyze.py --plot %*
) else (
  python analyze.py --plot %*
)
pause
