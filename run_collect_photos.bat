@echo off
rem 이미 찍어 둔 캡처 사진을 captures\result 에 '시험_온도C_PIN핀_항목.png' 로 모으기
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 collect_photos.py %*
) else (
  python collect_photos.py %*
)
pause
