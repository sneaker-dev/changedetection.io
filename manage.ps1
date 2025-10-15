<#
    Simple manager for changedetection.io on Windows PowerShell.

    Usage examples:
      - Start (background):  .\manage.ps1 -Action start -Datastore "D:\changedata" -Port 5000
      - Stop:                .\manage.ps1 -Action stop
      - Status:              .\manage.ps1 -Action status -Port 5000
      - Tail logs:           .\manage.ps1 -Action logs -Tail 200

    Notes:
      - Requires the venv at .venv created in the repo root
      - Uses logs in ./logs/server_out.txt and ./logs/server_err.txt
#>

[CmdletBinding()]
param(
    [ValidateSet('start','stop','status','logs')]
    [string]$Action = 'status',

    [string]$Datastore = 'D:\changedata',

    [int]$Port = 5000,

    [int]$Tail = 100
)

set-strictmode -version latest
$ErrorActionPreference = 'Stop'

# Resolve important paths
$Root = Split-Path -Parent $PSCommandPath
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$App = Join-Path $Root 'changedetection.py'
$LogsDir = Join-Path $Root 'logs'
$StdOut = Join-Path $LogsDir 'server_out.txt'
$StdErr = Join-Path $LogsDir 'server_err.txt'
$PidFile = Join-Path $LogsDir 'server.pid'

function Test-ProcessRunningById([int]$Pid) {
    try {
        $p = Get-Process -Id $Pid -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Get-ServerStatus([int]$CheckPort) {
    try {
        $resp = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}" -f $CheckPort) -UseBasicParsing -TimeoutSec 5
        if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) {
            return "OK ($($resp.StatusCode))"
        }
        return "HTTP $($resp.StatusCode)"
    } catch {
        return "DOWN: $($_.Exception.Message)"
    }
}

switch ($Action) {
    'start' {
        if (!(Test-Path $Python)) {
            Write-Error "Python venv not found at '$Python'. Create it with: py -3.11 -m venv .venv"
        }

        if (!(Test-Path $Datastore)) {
            New-Item -ItemType Directory -Path $Datastore | Out-Null
        }

        if (!(Test-Path $LogsDir)) {
            New-Item -ItemType Directory -Path $LogsDir | Out-Null
        }

        # Launch in background and record PID
        $proc = Start-Process -FilePath $Python -ArgumentList @($App,'-d',$Datastore,'-p',$Port) -WorkingDirectory $Root -RedirectStandardOutput $StdOut -RedirectStandardError $StdErr -PassThru
        Set-Content -Path $PidFile -Value $proc.Id -Encoding ASCII
        Start-Sleep -Seconds 2
        $status = Get-ServerStatus -CheckPort $Port
        Write-Output ("Started PID {0} | Status: {1}" -f $proc.Id, $status)
    }

    'stop' {
        $stopped = $false
        if (Test-Path $PidFile) {
            $pidText = Get-Content -Path $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($pidText -and ($pidText -as [int])) {
                $pid = [int]$pidText
                if (Test-ProcessRunningById -Pid $pid) {
                    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                    $stopped = $true
                    Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
                    Write-Output "Stopped PID $pid"
                }
            }
        }

        if (-not $stopped) {
            # Fallback: stop by matching process path and script name
            Get-Process python -ErrorAction SilentlyContinue |
                Where-Object { $_.Path -and $_.Path -like "*$($Root.Replace('\\','\'))*\.venv*python.exe" } |
                ForEach-Object {
                    try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; Write-Output "Stopped PID $($_.Id)" } catch {}
                }
        }
    }

    'status' {
        $pidInfo = $null
        if (Test-Path $PidFile) {
            $pidInfo = Get-Content -Path $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        }
        $running = $false
        if ($pidInfo -and ($pidInfo -as [int])) {
            $running = Test-ProcessRunningById -Pid ([int]$pidInfo)
        }
        $status = Get-ServerStatus -CheckPort $Port
        $pidOut = if ($running) { $pidInfo } else { 'n/a' }
        Write-Output ("Status: {0} | PID: {1}" -f $status, $pidOut)
        if (Test-Path $StdErr) { Write-Output "Recent errors:"; Get-Content -Tail 5 $StdErr }
    }

    'logs' {
        if (!(Test-Path $StdOut)) { Write-Output "No logs yet at $StdOut"; break }
        Write-Output "Tailing $StdOut (Ctrl+C to stop)"
        Get-Content -Tail $Tail -Wait $StdOut
    }
}


