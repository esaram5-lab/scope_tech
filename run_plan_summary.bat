@echo off
rem 시험 계획 요약표 다시 만들기 (스코프 연결 불필요)
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 plan_summary.py %*
) else (
  python plan_summary.py %*
)
pause
