@echo off
rem 인터넷 되는 PC 에서 실행: tm_devices 와 의존 패키지(Windows 64bit, Python 3.13 용)를 tm_devices_wheels 폴더로 받습니다.
rem 받은 tm_devices_wheels 폴더를 측정 PC 의 이 폴더(scope_tech)에 복사하세요.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% -m pip download tm_devices -d tm_devices_wheels --only-binary=:all: --platform win_amd64 --python-version 3.13
pause
