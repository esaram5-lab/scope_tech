@echo off
rem 측정 PC(오프라인)에서 실행: tm_devices_wheels 폴더에서 tm_devices 를 설치합니다.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
if not exist tm_devices_wheels (
  echo tm_devices_wheels 폴더가 없습니다. 인터넷 PC 에서 get_tm_devices.bat 로 받아 복사하세요.
  pause
  exit /b 1
)
%PY% -m pip install --no-index --find-links tm_devices_wheels tm_devices
pause
