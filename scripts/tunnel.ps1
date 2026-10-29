# SSH 反向隧道守护脚本（腾讯云服务器 ← 本地 Clash 代理）
# 用途: 服务器 172.17.0.1:7897 -> 本地 127.0.0.1:7897
#       供后端 GITHUB_PROXY=http://172.17.0.1:7897 拉取 GitHub README
# 行为: 断线(网络抖动/休眠唤醒/SSH超时)后 5 秒自动重连, 窗口关闭即停止

# 服务器地址：不要把生产 IP 与 root 用户名写进仓库。
# 取值优先级：环境变量 AICAT_TUNNEL_SERVER > 同目录 tunnel.local.ps1 的 $Server > 报错退出
$Server = $env:AICAT_TUNNEL_SERVER
$LocalOverride = Join-Path $PSScriptRoot "tunnel.local.ps1"
if (-not $Server -and (Test-Path $LocalOverride)) {
    . $LocalOverride
}
if (-not $Server) {
    Write-Host "[错误] 未配置服务器地址，脚本无法建立隧道。" -ForegroundColor Red
    Write-Host '  方式一: $env:AICAT_TUNNEL_SERVER = "root@<服务器IP>"' -ForegroundColor Yellow
    Write-Host "  方式二: 在 $LocalOverride 写入 `$Server = `"root@<服务器IP>`"（该文件不会被提交）" -ForegroundColor Yellow
    exit 1
}
$RemoteBind  = "172.17.0.1:7897"   # 服务器侧绑定地址(docker0 网桥, 容器可达)
$LocalTarget = "127.0.0.1:7897"    # 本地 Clash 监听地址
$LogFile     = Join-Path $PSScriptRoot "tunnel.log"

# 日志超过 1MB 自动清理, 防止无限增长
if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 1MB)) {
    Remove-Item $LogFile -ErrorAction SilentlyContinue
}

function Write-Log {
    param([string]$Message)
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message" | Out-File -Append -Encoding utf8 $LogFile
}

Write-Log "===== 隧道守护启动: $RemoteBind -> $LocalTarget ====="
Write-Host "隧道守护运行中 (服务器 $RemoteBind -> 本地 $LocalTarget), 关闭本窗口即停止" -ForegroundColor Green
Write-Host "日志: $LogFile"

# 连续失败计数(用于触发远程残留清理)
$failCount = 0

while ($true) {
    Write-Log "建立 SSH 隧道..."
    # ExitOnForwardFailure: 远端端口被残留会话占用时立即退出重试, 而非静默挂着一个空连接
    # ServerAliveInterval/CountMax: 90 秒内检测到断线即退出(如电脑休眠唤醒后)
    ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes `
        -N -R "$($RemoteBind):$($LocalTarget)" $Server
    $exit = $LASTEXITCODE
    Write-Log "SSH 断开 (exit=$exit), 5 秒后重连"

    # 自愈: 非正常退出多为远端 7897 被残留会话占用；连续失败 3 次后远程清理（该端口为本隧道专用，清理安全）
    if ($exit -ne 0) {
        $failCount++
        if ($failCount -ge 3) {
            Write-Log "连续 $failCount 次失败, 远程清理残留监听 7897..."
            ssh -o BatchMode=yes -o ConnectTimeout=10 $Server `
                "pid=\$(ss -tlnp | grep ':7897' | grep -oP 'pid=\\K[0-9]+' | head -1); [ -n \"\$pid\" ] && kill \$pid && echo cleaned || echo none" | ForEach-Object { Write-Log "  远程清理: $_" }
            $failCount = 0
        }
    } else {
        $failCount = 0
    }
    Start-Sleep -Seconds 5
}
