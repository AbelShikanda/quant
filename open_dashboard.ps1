$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$port = 8010

$listening = Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue -InformationLevel Quiet
if (-not $listening) {
    Write-Host "Starting GOLD BOT dashboard on port $port ..."
    Start-Process -FilePath $python -ArgumentList @(
        "-m", "goldbot.cli", "dashboard-live",
        "--ledger", "reports/demo.execution.ledger.sqlite",
        "--profile", "config/profiles/micro_50.yaml",
        "--host", "127.0.0.1",
        "--port", "$port"
    ) -WorkingDirectory $root
    Start-Sleep -Seconds 2
}
Start-Process "http://127.0.0.1:$port/"
