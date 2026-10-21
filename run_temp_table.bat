@echo off
rem 온도별 핀 측정값 표(CSV): captures\temp_table_날짜_시각.csv
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 temp_table.py %*
) else (
  python temp_table.py %*
)
pause
