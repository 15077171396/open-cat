@echo off
chcp 65001 >nul
rem  AI 学习猫 · 三端一键启动（后端 + 用户端 + 管理端）
rem
rem  为什么需要它：
rem    后端/两个前端分别开在三个独立窗口里，**窗口关掉服务才停**，
rem    不受任何外部进程回收影响（把三端交给同一个父进程托管时，
rem    父进程被回收会连坐 —— 2026-10-27 连续踩了两次）。
rem
rem  用法：
rem    双击本脚本 → 弹出三个窗口，等它们各自 ready 即可；
rem    停服务 = 关掉对应窗口（或三个全关）。
rem
rem  与单独启动的关系：
rem   后端直接复用 start-backend-dev.bat（自带「先释放 8000」+ venv 检查 +
rem   --reload 只监听 app/ 与 scheduler/），本脚本不重复实现那套逻辑。
rem   前端加 --strictPort：端口被占时**直接报错**，而不是悄悄换到 5174/5175
rem   让人对着旧地址排查。
title AI猫 三端一键启动
setlocal
cd /d "%~dp0.."

if not exist "backend\venv\Scripts\python.exe" (
  echo [错误] 未找到 backend\venv\Scripts\python.exe
  echo        请先创建虚拟环境并安装依赖。
  pause
  exit /b 1
)
if not exist "frontend\node_modules\vite\bin\vite.js" (
  echo [错误] 未找到 frontend\node_modules —— 请先在 frontend 目录执行 npm install
  pause
  exit /b 1
)
if not exist "admin-frontend\node_modules\vite\bin\vite.js" (
  echo [错误] 未找到 admin-frontend\node_modules —— 请先在该目录执行 npm install
  pause
  exit /b 1
)

echo ============================================================
echo   正在打开三个窗口（各自独立，互不影响）
echo ------------------------------------------------------------
echo   后端       http://127.0.0.1:8000/docs    （热重载 app/ scheduler/）
echo   用户端     http://127.0.0.1:5173/
echo   管理端     http://127.0.0.1:5174/admin/  （必须带尾斜杠）
echo ------------------------------------------------------------
echo   依赖：MySQL 3306 常驻；Redis 6379 若没起，缓存类功能会降级
echo   停止：关掉对应窗口即可
echo ============================================================
echo.

start "AI猫后端-热重载 (0.0.0.0:8000)" /D "%~dp0" cmd /k "start-backend-dev.bat"
start "AI猫用户端 (127.0.0.1:5173)" /D "%~dp0..\frontend" cmd /k "node ./node_modules/vite/bin/vite.js --host 127.0.0.1 --strictPort"
start "AI猫管理端 (127.0.0.1:5174)" /D "%~dp0..\admin-frontend" cmd /k "node ./node_modules/vite/bin/vite.js --host 127.0.0.1 --strictPort"

echo [ok] 三个窗口已拉起。本窗口可以关闭。
timeout /t 5 >nul
endlocal
