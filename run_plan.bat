@echo off
rem 시험 계획(test_plan.csv) 순서대로 측정
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 scope.py --plan %*
) else (
  python scope.py --plan %*
)
pause
