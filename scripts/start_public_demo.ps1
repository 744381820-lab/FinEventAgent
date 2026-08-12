# 启动本地服务 + Cloudflare 临时公网链接（本机需保持开机）
# 用法：在仓库根目录执行 .\scripts\start_public_demo.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$env:PYTHONPATH = $Root

if (-not (Test-Path "$Root\tools\cloudflared.exe")) {
  Write-Host "缺少 tools/cloudflared.exe，请先下载 cloudflared"
  exit 1
}

# 若 8000 未监听则拉起服务
$listening = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
  Write-Host "启动 FinEventAgent ..."
  Start-Process -FilePath "python" -ArgumentList "server.py" -WorkingDirectory $Root -WindowStyle Minimized
  Start-Sleep -Seconds 3
}

Write-Host "正在创建 Cloudflare 公网隧道 ..."
& "$Root\tools\cloudflared.exe" tunnel --url http://localhost:8000
