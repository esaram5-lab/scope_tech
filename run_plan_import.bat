@echo off
rem 예전 측정 기록을 시험 계획에 붙이기 (스코프 연결 불필요)
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 plan_import.py %*
) else (
  python plan_import.py %*
)
pause
