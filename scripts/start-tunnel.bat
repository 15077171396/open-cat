@echo off
rem Manual start tunnel (foreground, stops when window closed)
rem For auto-start at login, run install-tunnel-task.ps1 once
powershell -ExecutionPolicy Bypass -File "%~dp0tunnel.ps1"
