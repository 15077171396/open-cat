@echo off
chcp 65001 >nul
rem  AI 学习猫 · 小程序真机调试环境准备：
rem  1. 放行 Windows 防火墙的 8000 端口（入站 TCP）
rem  2. 打印当前电脑的局域网 IP，便于核对 app.js 里的 LAN_API_BASE
rem  ⚠️ 必须【以管理员身份】运行（改防火墙需要管理员权限）

setlocal
cd /d "%~dp0.."

echo.
echo ============================================================
echo  [1/3] 当前电脑的网络地址
echo ============================================================
echo.
echo  可用 IPv4 地址（请找出 192.168.x.x / 10.x.x.x 那个）：
echo.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
  for /f "tokens=* delims= " %%b in ("%%a") do echo    %%b
)
echo.
echo  本项目的 LAN_API_BASE 目前配置为：
findstr /c:"LAN_API_BASE =" miniprogram\app.js
echo.
echo  若上面两个地址不一致，请修改 miniprogram\app.js 里的 LAN_API_BASE。
echo.

echo ============================================================
echo  [2/3] 放行防火墙 8000 端口（入站 TCP）
echo ============================================================
net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo   [错误] 需要管理员权限。
  echo   [提示] 请右键本文件 -^> 以管理员身份运行
  echo.
  pause
  exit /b 1
)

rem 先删掉同名旧规则，避免重复
netsh advfirewall firewall delete rule name="AI-Cat API 8000" >nul 2>&1
netsh advfirewall firewall add rule name="AI-Cat API 8000" dir=in action=allow protocol=TCP localport=8000
if errorlevel 1 (
  echo   [失败] 添加防火墙规则失败
) else (
  echo   [成功] 已放行入站 TCP 8000
)
echo.

echo ============================================================
echo  [3/3] 检查后端监听状态
echo ============================================================
echo.
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
  echo   [警告] 8000 端口没有监听，后端可能未启动。
  echo   [提示] 请先运行 scripts\start-miniapp-api.bat 启动后端。
) else (
  echo   [正常] 8000 端口正在监听。监听地址应包含 0.0.0.0：
  netstat -ano | findstr ":8000" | findstr "LISTENING"
  echo.
  echo   若开头是 127.0.0.1 而不是 0.0.0.0，请用以下命令重启后端：
  echo     cd backend
  echo     venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
)
echo.

echo ============================================================
echo  真机调试检查清单
echo ============================================================
echo   1. 手机与电脑连【同一个 WiFi】
echo   2. 手机端微信 -> 打开小程序 -> 右上角 ... -> 打开调试
echo      （真机调试需开启调试模式，否则 http:// 明文请求会被拦）
echo   3. miniprogram/app.js 的 LAN_API_BASE 指向上面打印的局域网 IP
echo   4. 本脚本已放行防火墙 8000
echo   5. 后端以 --host 0.0.0.0 启动
echo.
echo   验证：在手机浏览器打开  http://<局域网IP>:8000/docs
echo         能打开 -> 网络通了；打不开 -> 回到第 1/4/5 条检查
echo.
pause
endlocal
