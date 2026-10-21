@echo off
rem 스코프(MSO58B Windows)에서 실행: LAN 화면 캡처 서버. TekScope 창만, LAN 자동 검색 켬.
rem 처음 한 번: py -m pip install Pillow  /  창 제목 확인: py scope_capture_server.py --list-windows
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 scope_capture_server.py --discovery --window TekScope %*
) else (
  python scope_capture_server.py --discovery --window TekScope %*
)
pause
