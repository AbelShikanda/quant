param(
    [int]$Port = 8010,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
# This machine's .venv python is a shim that re-execs the base global interpreter;
# the process actually bound to the port is the global child, so "repo .venv" is
# identified via the parent shim, not the owner's own executable path.
$script:VenvMarker = (Join-Path $root '.venv').ToLower()

function Get-ProcessInfo {
    param([int]$ProcessId)
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $p) { return $null }
    return [pscustomobject]@{
        Pid = [int]$p.ProcessId
        ParentPid = [int]$p.ParentProcessId
        ExecutablePath = $p.ExecutablePath
        CommandLine = $p.CommandLine
        StartTime = $p.CreationDate
    }
}

function Get-PortOwner {
    param([int]$PortNumber)
    $conn = Get-NetTCPConnection -State Listen -LocalPort $PortNumber -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $conn) { return $null }
    return Get-ProcessInfo -ProcessId $conn.OwningProcess
}

function Test-IsDashboard {
    param($Owner)
    return $Owner.CommandLine -and ($Owner.CommandLine -match 'goldbot\.cli\s+dashboard-live')
}

function Test-IsRepoDashboard {
    param($Owner)
    if (-not (Test-IsDashboard -Owner $Owner)) { return $false }
    if ($Owner.CommandLine.ToLower().Contains($script:VenvMarker)) { return $true }
    $parent = Get-ProcessInfo -ProcessId $Owner.ParentPid
    if ($parent) {
        $parentInfo = "$($parent.ExecutablePath) $($parent.CommandLine)".ToLower()
        if ($parentInfo.Contains($script:VenvMarker)) { return $true }
    }
    return $false
}

function Open-Browser {
    param([string]$Url)
    if (-not $NoBrowser) { Start-Process $Url }
}

$owner = Get-PortOwner -PortNumber $Port
if ($owner) {
    $cmd = $owner.CommandLine
    if (Test-IsDashboard -Owner $owner) {
        if (Test-IsRepoDashboard -Owner $owner) {
            Write-Host "Dashboard already running on port $Port (pid $($owner.Pid), repo .venv). Reusing it."
            if ($owner.StartTime) {
                Write-Host "  started $($owner.StartTime). If the UI looks stale, use 'Shutdown Dashboard' in the UI and relaunch."
            }
            Open-Browser "http://127.0.0.1:$Port/"
            exit 0
        }
        Write-Host "Port $Port is held by a dashboard NOT launched from the repo .venv:"
        Write-Host "  pid $($owner.Pid): $cmd"
        $answer = Read-Host "Kill it and restart the repo .venv dashboard on port $Port? [y/N]"
        if ($answer -notmatch '^(y|yes)$') {
            Write-Host "Leaving it running; not touching anything."
            exit 1
        }
        Stop-Process -Id $owner.Pid -Force -ErrorAction SilentlyContinue
        $parent = Get-ProcessInfo -ProcessId $owner.ParentPid
        if ($parent -and $parent.ExecutablePath -and $parent.ExecutablePath.ToLower().Contains($script:VenvMarker)) {
            Stop-Process -Id $parent.Pid -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
        # fall through to start a fresh repo .venv dashboard
    } else {
        Write-Host "Port $Port is held by an unrecognized process (pid $($owner.Pid)):"
        Write-Host "  $cmd"
        Write-Host "Not this script's dashboard; refusing to kill it. Free port $Port or pick another port."
        exit 1
    }
}

if (-not (Get-PortOwner -PortNumber $Port)) {
    Write-Host "Starting GOLD BOT dashboard on port $Port ..."
    Start-Process -FilePath $python -ArgumentList @(
        "-m", "goldbot.cli", "dashboard-live",
        "--ledger", "reports/demo.execution.ledger.sqlite",
        "--profile", "config/profiles/micro_50.yaml",
        "--host", "127.0.0.1",
        "--port", "$Port"
    ) -WorkingDirectory $root
    Start-Sleep -Seconds 2
}
Open-Browser "http://127.0.0.1:$Port/"
