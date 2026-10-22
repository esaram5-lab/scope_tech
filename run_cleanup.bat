@echo off
rem 여러 버전이 섞인 측정 기록 정리 (스코프 연결 불필요)
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 cleanup.py %*
) else (
  python cleanup.py %*
)
pause
