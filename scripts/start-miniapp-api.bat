@echo off
chcp 65001 >nul
rem  AI 学习猫 · 小程序端联调后端启动脚本（本地开发，小程序端通过 127.0.0.1:8000 联调）。
rem  不影响 Web 端部署方式（Docker / nginx 照旧）。
rem  优先使用 backend\venv\Scripts\python.exe：PATH 中的 python 可能未装依赖。

setlocal
cd /d "%~dp0..\backend"

rem 选择 Python 解释器：优先 venv，其次 PATH 中的 python
set "PY="
if exist "venv\Scripts\python.exe" (
  set "PY=venv\Scripts\python.exe"
) else (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)

echo.
echo [1/3] 检查 Python 环境...
if not defined PY (
  echo   [错误] 未找到可用的 Python。
  echo   [提示] 请先创建虚拟环境并安装依赖：
  echo            python -m venv venv
  echo            venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)
if /i "%PY%"=="python" (
  echo   [警告] 未发现 backend\venv，回退使用系统 python。
  echo   [警告] 若提示缺少 fastapi 等模块，请先构建虚拟环境：
  echo            python -m venv venv
  echo            venv\Scripts\python.exe -m pip install -r requirements.txt
)
echo   使用解释器: %PY%
"%PY%" --version

echo.
echo [2/3] 执行数据库迁移（新增 wechat_openid 列，幂等）...
"%PY%" -m alembic upgrade head
if errorlevel 1 (
  echo   [警告] 迁移失败，若数据库尚未初始化请先执行 alembic upgrade head 全量迁移
  echo   [提示] 若已是最新版本，此提示可忽略
)

echo.
echo [3/3] 启动后端服务...
echo   本机访问:   http://127.0.0.1:8000        （开发者工具模拟器）
echo   局域网访问: http://192.168.1.187:8000    （真机调试，需手机同 WiFi）
echo   接口文档:   http://127.0.0.1:8000/docs
echo   ------ 
echo   小程序端 app.js 已内置环境自适应：
echo     模拟器 -> 127.0.0.1 ；真机 -> 192.168.1.187
echo   若电脑局域网 IP 变了，请修改 app.js 里的 LAN_API_BASE
echo   真机连不上时检查：1) 同一 WiFi  2) 防火墙放行 8000  3) 手机开启调试模式
echo   按 Ctrl+C 停止服务
echo.

rem --host 0.0.0.0 是关键：只绑 127.0.0.1 时手机（局域网）无法访问
"%PY%" -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

endlocal
