@echo off
REM PC 竖屏体验预览（web-032）：竖屏应用窗 + 免麦克风弹窗 + 自动播放
REM 前置：kiosk_server(:7861) 与前端 dev/build 已起（默认 http://localhost:5173）
set FRONT_URL=%1
if "%FRONT_URL%"=="" set FRONT_URL=http://localhost:5173

set CHROME="C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist %CHROME% set CHROME="C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

REM --app= 去边框应用窗；--window-size=540,960 = 9:16 竖屏（一体机比例）
start "pc-preview" %CHROME% ^
  --app=%FRONT_URL% ^
  --window-size=540,960 ^
  --use-fake-ui-for-media-stream ^
  --autoplay-policy=no-user-gesture-required ^
  --disable-pinch ^
  --new-window
