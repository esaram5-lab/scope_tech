@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 seq_test.py %*
) else (
  python seq_test.py %*
)
pause
