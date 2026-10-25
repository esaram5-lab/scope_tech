@echo off
rem 온도를 잘못 넣고 측정한 기록 고치기 (스코프 연결 불필요). 차례로 물어봄
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 fix_temp.py %*
) else (
  python fix_temp.py %*
)
pause
