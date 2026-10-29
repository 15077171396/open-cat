@echo off
chcp 65001 >nul
rem  AI 学习猫 · 后端启动（开发热重载版）
rem
rem  与 start-backend.bat 的区别：
rem    - 本脚本带 --reload：改 backend/app 或 backend/scheduler 下的 .py 会**自动重载**，
rem      不需要手动重启（也就不会再出现「改了代码但接口还是 404」）；
rem    - 启动前会**先释放 8000 端口**（关掉上一次的进程），所以「再双击一次 = 重启」。
rem
rem  ⚠️ 什么时候不要用它：
rem    - 小程序真机 / 预览联调：热重载会在保存文件的瞬间中断在飞请求，
rem      真机侧可能报网络错误。那种场景用 start-backend.bat（稳定常驻版）。
rem    - 跑迁移（alembic upgrade）后：**必须重启**，因为已建立的连接/ORM 元数据不会自动刷新。
rem
rem  依赖：--reload 需要 watchfiles（已装入 backend\venv；若换机器请先
rem        backend\venv\Scripts\python.exe -m pip install watchfiles）
title AI猫后端-热重载 (0.0.0.0:8000)
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
echo   启动 AI 猫后端（开发热重载）
echo ------------------------------------------------------------
echo   本机访问:   http://127.0.0.1:8000
echo   接口文档:   http://127.0.0.1:8000/docs
echo   热重载范围: backend\app 与 backend\scheduler 下的 .py
echo   停止服务:   关闭本窗口（或再双击一次本脚本）
echo ------------------------------------------------------------
echo   提示: 真机联调请改用 start-backend.bat（reload 会中断在飞请求）
echo ============================================================
echo.

rem 释放 8000：上一次可能还在跑（没有 --reload 的旧进程尤其常见）。
rem 只杀监听在 :8000 的进程，不动其它端口。
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"LISTENING" ^| findstr /c:":8000 "') do (
  echo [info] 释放 8000 端口，结束旧进程 PID=%%p
  taskkill /PID %%p /F >nul 2>&1
)

rem --reload-dir 显式限定范围：不限定会去监听整个 backend（含 venv 里上万文件），
rem 启动慢且容易误触发重载。
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir app --reload-dir scheduler

echo.
echo 后端已停止。按任意键关闭窗口。
pause >nul
endlocal
