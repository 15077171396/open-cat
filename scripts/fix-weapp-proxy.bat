@echo off
chcp 65001 >nul
setlocal

echo ============================================================
echo  微信开发者工具 预览/真机调试 网络修复
echo  （清除系统代理残留 + 重启工具，解决 tunneling ECONNREFUSED）
echo ============================================================
echo.

set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

echo [1/4] 检查当前系统代理设置...
"%PS%" -NoProfile -Command ^
  "$p='HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings';" ^
  "$ie=Get-ItemProperty -Path $p -ErrorAction SilentlyContinue;" ^
  "Write-Host ('  ProxyEnable = ' + $ie.ProxyEnable);" ^
  "Write-Host ('  ProxyServer = ' + $ie.ProxyServer);" ^
  "if ($ie.ProxyServer -and $ie.ProxyEnable -eq 0) { Write-Host '  [发现问题] 开关已关但地址残留 -> 会坑 Electron 类应用' -ForegroundColor Yellow }"
echo.

echo [2/4] 清除残留的 ProxyServer 字段...
"%PS%" -NoProfile -Command ^
  "$p='HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings';" ^
  "Remove-ItemProperty -Path $p -Name 'ProxyServer' -ErrorAction SilentlyContinue;" ^
  "Remove-ItemProperty -Path $p -Name 'ProxyOverride' -ErrorAction SilentlyContinue;" ^
  "$ie=Get-ItemProperty -Path $p -ErrorAction SilentlyContinue;" ^
  "Write-Host ('  清除后 ProxyServer = [' + $ie.ProxyServer + ']')"
echo.

echo [3/4] 关闭微信开发者工具残留进程...
taskkill /F /IM WeChatAppEx.exe    >nul 2>&1
taskkill /F /IM Weixin.exe         >nul 2>&1
taskkill /F /IM wechatdevtools.exe >nul 2>&1
echo   已尝试结束相关进程（未运行则忽略）
echo.

echo [4/4] 后端服务状态检查（真机联调需要 0.0.0.0:8000）...
netstat -ano | findstr ":8000" | findstr LISTENING
echo.

echo ============================================================
echo  修复完成！现在请手动重新打开微信开发者工具，再点「预览」。
echo.
echo  如果仍失败，请检查工具内：
echo    设置 - 代理设置 - 选择「不使用任何代理」
echo ============================================================
echo.
pause
