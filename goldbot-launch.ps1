$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$manifest = Join-Path $root "config\scope.gold.v1.yaml"
$macroDir = Join-Path $root "data\raw\macro"
$dashboardLedger = "reports\micro_50.paper.ledger.sqlite"

$profiles = @{
    "1" = "config\profiles\standard_1k.yaml"
    "2" = "config\profiles\micro_50.yaml"
    "3" = "config\profiles\micro_100.yaml"
}

function Select-Profile {
    Write-Host ""
    Write-Host "Choose the account profile:"
    Write-Host "  1) standard_1k (promotion-eligible, 5:1, `$1,000)"
    Write-Host "  2) micro_50 (HIGH RISK, 100:1, not promotion-eligible)"
    Write-Host "  3) micro_100 (HIGH RISK, 50:1, not promotion-eligible)"
    Write-Host "  4) cancel"
    $choice = Read-Host "Select"
    return $profiles[$choice]
}

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

function Open-Dashboard {
    $port = 8010
    $owner = Get-PortOwner -PortNumber $port
    if ($owner) {
        $cmd = $owner.CommandLine
        if (Test-IsDashboard -Owner $owner) {
            if (Test-IsRepoDashboard -Owner $owner) {
                Write-Host "Dashboard already running on port $port (pid $($owner.Pid), repo .venv). Reusing it."
                if ($owner.StartTime) {
                    Write-Host "  started $($owner.StartTime). If the UI looks stale, use 'Shutdown Dashboard' in the UI and relaunch."
                }
                Start-Process "http://127.0.0.1:$port/"
                return
            }
            Write-Host "Port $port is held by a dashboard NOT launched from the repo .venv:"
            Write-Host "  pid $($owner.Pid): $cmd"
            $answer = Read-Host "Kill it and restart the repo .venv dashboard on port $port? [y/N]"
            if ($answer -notmatch '^(y|yes)$') {
                Write-Host "Leaving it running; not touching anything."
                return
            }
            Stop-Process -Id $owner.Pid -Force -ErrorAction SilentlyContinue
            $parent = Get-ProcessInfo -ProcessId $owner.ParentPid
            if ($parent -and $parent.ExecutablePath -and $parent.ExecutablePath.ToLower().Contains($script:VenvMarker)) {
                Stop-Process -Id $parent.Pid -Force -ErrorAction SilentlyContinue
            }
            Start-Sleep -Seconds 1
            # fall through to start a fresh repo .venv dashboard
        } else {
            Write-Host "Port $port is held by an unrecognized process (pid $($owner.Pid)):"
            Write-Host "  $cmd"
            Write-Host "Not this script's dashboard; refusing to kill it. Free port $port or pick another port."
            return
        }
    }
    if (-not (Get-PortOwner -PortNumber $port)) {
        Write-Host "Starting the read-only dashboard on port $port ..."
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
}

function Run-Preflight {
    param([string]$ProfileRel)
    $profile = Join-Path $root $ProfileRel
    $ledger = Join-Path $root "reports\demo.preflight.ledger.sqlite"
    $report = Join-Path $root "reports\demo.preflight.json"
    & $python -m goldbot.cli demo-preflight `
        --manifest $manifest `
        --account-profile $profile `
        --macro-dir $macroDir `
        --broker-utc-offset-minutes 120 `
        --confirm-broker-offset `
        --expected-login 106806652 `
        --ledger $ledger `
        --report $report
}

Write-Host "GoldBot launcher"
Write-Host "1) Open read-only dashboard (http://127.0.0.1:8010/)"
Write-Host "2) Run demo preflight (read-only; no order is sent)"
Write-Host "3) Request demo order (explicit SEND DEMO ORDER required)"
Write-Host "4) Exit"
$choice = Read-Host "Select"

switch ($choice) {
    "1" {
        Open-Dashboard
    }
    "2" {
        $p = Select-Profile
        if ($p) {
            Run-Preflight -ProfileRel $p
        } else {
            Write-Host "Cancelled."
        }
    }
    "3" {
        $p = Select-Profile
        if (-not $p) {
            Write-Host "Cancelled."
        } else {
            Write-Host "Running read-only preflight first..."
            Run-Preflight -ProfileRel $p
            if ($LASTEXITCODE -ne 0) {
                Write-Host ""
                Write-Host "Preflight failed or reported a blocker; no order was sent."
            } else {
                Write-Host ""
                $confirm = Read-Host "If the preflight is PASS and ENTER_LONG, type SEND DEMO ORDER to send the demo order"
                if ($confirm -eq "SEND DEMO ORDER") {
                    $ledger = Join-Path $root "reports\demo.execution.ledger.sqlite"
                    $report = Join-Path $root "reports\demo.execution.json"
                    $profile = Join-Path $root $p
                    & $python -m goldbot.cli demo-send `
                        --manifest $manifest `
                        --account-profile $profile `
                        --macro-dir $macroDir `
                        --broker-utc-offset-minutes 120 `
                        --confirm-broker-offset `
                        --expected-login 106806652 `
                        --send `
                        --confirm-order "SEND DEMO ORDER" `
                        --ledger $ledger `
                        --report $report
                } else {
                    Write-Host "No order was sent."
                }
            }
        }
    }
    default {
        Write-Host "Exiting."
    }
}

Read-Host "Press Enter to close"
