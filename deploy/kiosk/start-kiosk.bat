@echo off
REM 数字人一体机启动脚本（web-027，Windows）
REM 用法：部署到一体机后双击 / 加入 Windows 启动项（shell:startup）
setlocal
set KIOSK_DIR=%~dp0..\..
set FRONT_PORT=8080

start "kiosk-frontend" /min python "%KIOSK_DIR%\deploy\kiosk\serve-dist.py" --dir "%KIOSK_DIR%\frontend\dist" --port %FRONT_PORT%
timeout /t 3 /nobreak >nul

set CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
if not exist "%CHROME%" (echo 未找到 Chrome/Edge 浏览器 & pause & exit /b 1)

start "kiosk-chrome" "%CHROME%" --kiosk http://127.0.0.1:%FRONT_PORT%/ --use-fake-ui-for-media-stream --autoplay-policy=no-user-gesture-required --disable-pinch --overscroll-history-navigation=0 --disable-features=TranslateUI --noerrdialogs --disable-infobars --incognito
endlocal
