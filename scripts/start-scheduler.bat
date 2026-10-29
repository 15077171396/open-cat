@echo off
chcp 65001 >nul
rem  AI 学习猫 · 定时任务调度器（本机开发用）
rem  生产由 compose 的 scheduler 容器常驻；本机没有那台容器，要跑任务就得手动起这个窗口。
rem  不开也能用后台「系统 → 定时任务」页：能看状态、能启停/改周期，
rem  但「立即执行」会返回 503（没有进程消费命令队列），页面也会提示「调度器未运行」—— 那是准确的。

title AI猫调度器 (scheduler)
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
echo   启动 AI 猫定时任务调度器
echo ------------------------------------------------------------
echo   任务清单/周期：后台「系统 → 定时任务」页
echo   配置热重载：   每 5 秒对齐一次数据库里的 scheduled_job_configs
echo   立即执行：     后台点「执行」经 Redis 队列送达本进程
echo ------------------------------------------------------------
echo   注意：本进程会真的执行任务（清理日志、降级 VIP、推送收录等）。
echo         本机开发库同样会被改动，介意就先停掉它。
echo   关闭本窗口 = 停止调度器
echo ============================================================
echo.

"%PY%" -m scheduler.tasks

echo.
echo 调度器已停止。按任意键关闭窗口。
pause >nul
endlocal
