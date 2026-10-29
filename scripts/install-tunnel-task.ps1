# 一键注册开机自启任务计划(当前用户登录时自动运行隧道守护脚本)
# 用法: 右键"使用 PowerShell 运行", 或在 PowerShell 中执行 .\install-tunnel-task.ps1
# 卸载: Unregister-ScheduledTask -TaskName "SSH-Tunnel-ai-cat" -Confirm:$false

$TaskName = "SSH-Tunnel-ai-cat"
# 服务器地址（代码审查 2026-10-10 P1-9）：**不要把生产 IP 与 root 用户名写进仓库**。
# 取值优先级：环境变量 AICAT_TUNNEL_SERVER > 同目录 tunnel.local.ps1 的 $Server > 报错退出
$Server = $env:AICAT_TUNNEL_SERVER
$LocalOverride = Join-Path $PSScriptRoot "tunnel.local.ps1"
if (-not $Server -and (Test-Path $LocalOverride)) {
    . $LocalOverride
}
if (-not $Server) {
    Write-Host "[错误] 未配置服务器地址。" -ForegroundColor Red
    Write-Host '  方式一: $env:AICAT_TUNNEL_SERVER = "root@<服务器IP>"' -ForegroundColor Yellow
    Write-Host "  方式二: 在 $LocalOverride 写入 `$Server = `"root@<服务器IP>`"（该文件不会被提交）" -ForegroundColor Yellow
    exit 1
}

# 前置检查: 确认免密登录可用(任务计划在后台运行, 无法输入密码)
Write-Host "检查 SSH 免密登录..." -ForegroundColor Cyan
ssh -o BatchMode=yes -o ConnectTimeout=10 $Server "echo ok" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host @"

[未通过] 当前 SSH 需要输入密码, 后台任务无法自动登录。
请先配置密钥免密(只需一次), 在 PowerShell 中依次执行:

  1. ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\id_ed25519
     (一路回车; 已有密钥则跳过)
  2. type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh $Server "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
     (这一步需输一次密码)
  3. 重新运行本脚本

"@ -ForegroundColor Yellow
    exit 1
}
Write-Host "[通过] 免密登录可用" -ForegroundColor Green

# 注册任务计划: 用户登录时启动, 隐藏窗口运行
$ScriptPath = Join-Path $PSScriptRoot "tunnel.ps1"
$Action   = New-ScheduledTaskAction -Execute "powershell.exe" `
            -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`""
$Trigger  = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Description "AI学习猫: GitHub 出站代理 SSH 反向隧道" -Force | Out-Null

Write-Host "`n[完成] 已注册任务计划 `"$TaskName`" (每次登录自动启动)" -ForegroundColor Green
Write-Host "立即启动: Start-ScheduledTask -TaskName `"$TaskName`""
Write-Host "手动停止: Stop-ScheduledTask -TaskName `"$TaskName`""
Write-Host "彻底卸载: Unregister-ScheduledTask -TaskName `"$TaskName`" -Confirm:`$false"

# 顺手立即启动一次
Start-ScheduledTask -TaskName $TaskName
Write-Host "`n已立即启动隧道, 3 秒后验证服务器端口..." -ForegroundColor Cyan
Start-Sleep -Seconds 3

ssh -o BatchMode=yes -o ConnectTimeout=10 $Server "ss -tln | grep 7897 && echo '--- 隧道正常 ---'" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "[验证通过] 服务器 7897 端口已监听" -ForegroundColor Green
} else {
    Write-Host "[验证失败] 服务器端口未就绪, 可能仍在建立中, 稍后可在服务器执行 ss -tlnp | grep 7897 确认" -ForegroundColor Yellow
    Write-Host "也可查看日志: $(Join-Path $PSScriptRoot 'tunnel.log')"
}
