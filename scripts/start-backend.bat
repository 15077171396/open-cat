@echo off
chcp 65001 >nul
rem  AI 学习猫 · 后端启动（稳定常驻版）：手动启动后端并持续运行，关闭本窗口即停止。
rem  小程序真机/预览联调需要后端监听 0.0.0.0:8000 并持续运行。

title AI猫后端 (0.0.0.0:8000)
setlocal
cd /d "%~dp0..\backend"

set "PY="
if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
if not defined PY (
  echo [错误] 未找到 backend\venv\Scripts\python.exe
  echo        请先创建虚拟环境并安装依赖。
  pause
  exit /b 1
)

echo ============================================================
echo   启动 AI 猫后端
echo ------------------------------------------------------------
echo   本机访问:   http://127.0.0.1:8000      （工具模拟器）
echo   局域网访问: http://192.168.1.187:8000  （手机真机，需同一WiFi）
echo   接口文档:   http://127.0.0.1:8000/docs
echo ------------------------------------------------------------
echo   小程序 app.js 已按环境自适应：
echo     模拟器 -> 127.0.0.1 ; 真机 -> 192.168.1.187
echo   若电脑局域网 IP 变了，改 app.js 里的 LAN_API_BASE
echo ------------------------------------------------------------
echo   关闭本窗口 = 停止后端
echo ============================================================
echo.

rem --host 0.0.0.0 是关键：只绑 127.0.0.1 时手机（局域网）访问不到
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000

echo.
echo 后端已停止。按任意键关闭窗口。
pause >nul
endlocal
