@echo off
REM PC 竖屏体验预览（web-032）：9:16 应用窗 + 免麦克风弹窗 + 自动播放
REM 用法：双击；或 start-pc-preview.bat http://localhost:5173 指定前端地址
setlocal
set FRONT_URL=%~1
if "%FRONT_URL%"=="" set FRONT_URL=http://localhost:5173

set CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
if not exist "%CHROME%" set CHROME=C:\Program Files\Microsoft\Edge\Application\msedge.exe
if not exist "%CHROME%" (echo 未找到 Chrome/Edge 浏览器 & pause & exit /b 1)

echo 正在启动 PC 竖屏预览: %FRONT_URL%  （浏览器: %CHROME%）
start "pc-preview" "%CHROME%" --app=%FRONT_URL% --window-size=540,960 --use-fake-ui-for-media-stream --autoplay-policy=no-user-gesture-required --disable-pinch --new-window
endlocal
