@echo off
rem N6705C 확인: 장비·옵션·모듈·ARB 가능 여부 (스코프 연결 불필요)
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 psu.py check %*
) else (
  python psu.py check %*
)
pause
