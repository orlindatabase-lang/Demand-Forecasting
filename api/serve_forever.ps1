
# Watchdog: keeps the SKU Production Plan API (run.py) alive forever.
# Started at logon by the "OrlinForecastAPI" scheduled task. If run.py exits
# for any reason (crash, session close), this relaunches it after 5 seconds.
# Single-instance is enforced by the task settings (MultipleInstances=IgnoreNew).
$ErrorActionPreference = 'SilentlyContinue'

$api = 'D:\Demand_Forecasting\api'
$py  = 'D:\Demand_Forecasting\venv\Scripts\python.exe'
$log = Join-Path $api '.cache'
New-Item -ItemType Directory -Force -Path $log | Out-Null
$watch = Join-Path $log 'watchdog.log'

function Log($msg) {
    Add-Content -Path $watch -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
}

Log "watchdog started (pid $PID)"
while ($true) {
    # If something is already serving port 8000 (e.g. a manual run.py), wait
    # rather than fighting over the bind.
    $busy = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if ($busy) { Start-Sleep -Seconds 10; continue }

    Log "starting run.py"
    $p = Start-Process -FilePath $py -ArgumentList 'run.py' -WorkingDirectory $api `
            -RedirectStandardOutput (Join-Path $log 'api.out.log') `
            -RedirectStandardError  (Join-Path $log 'api.err.log') `
            -WindowStyle Hidden -PassThru
    $p.WaitForExit()
    Log ("run.py exited (code {0}); restarting in 5s" -f $p.ExitCode)
    Start-Sleep -Seconds 5
}
