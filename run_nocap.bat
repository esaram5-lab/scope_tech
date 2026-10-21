@echo off
rem 화면 캡처 없이 측정·커서 표시·파형 저장만 하는 실행 (scope.py --no-capture)
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 scope.py --no-capture %*
) else (
  python scope.py --no-capture %*
)
pause
