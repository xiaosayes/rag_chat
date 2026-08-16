@echo off
REM 数字人一体机启动脚本（web-027，Windows）
REM 用法：部署到一体机后双击 / 加入 Windows 启动项（shell:startup）
REM 前置：frontend/dist 已构建；CHROME_PATH 指向 Chrome 安装路径

setlocal
set KIOSK_DIR=%~dp0..\..
set API_URL=http://ub-server:7861
set FRONT_PORT=8080

REM 1) 启动静态伺服（后台）
start "kiosk-frontend" /min python "%KIOSK_DIR%\deploy\kiosk\serve-dist.py" --dir "%KIOSK_DIR%\frontend\dist" --port %FRONT_PORT%

REM 2) 等待伺服就绪
timeout /t 3 /nobreak >nul

REM 3) Chrome kiosk 全屏（免提收音免授权弹窗 + 禁一切提示）
set CHROME="C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist %CHROME% set CHROME="C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
start "kiosk-chrome" %CHROME% ^
  --kiosk http://127.0.0.1:%FRONT_PORT%/ ^
  --use-fake-ui-for-media-stream ^
  --autoplay-policy=no-user-gesture-required ^
  --disable-pinch ^
  --overscroll-history-navigation=0 ^
  --disable-features=TranslateUI ^
  --noerrdialogs ^
  --disable-infobars ^
  --incognito
endlocal
