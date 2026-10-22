@echo off
rem 시험 계획 결과·설정 창만 열기 (측정은 run_plan.bat). 콘솔 없이 창만 뜬다.
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 plan_gui.py %*
) else (
  start "" pythonw plan_gui.py %*
)
