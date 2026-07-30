$ErrorActionPreference = "Continue"

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
try {
    chcp.com 65001 | Out-Null
    [Console]::InputEncoding = $Utf8NoBom
    [Console]::OutputEncoding = $Utf8NoBom
    $OutputEncoding = $Utf8NoBom
} catch {
    # Scheduled-task sessions may not expose a normal console.
}

$Repo = "C:\Users\Administrator\Desktop\AHNS"
$Log = Join-Path $Repo "logs\server_power.log"

function Write-PowerLog {
    param([string]$Message)
    try {
        New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
        $Line = "[{0}] {1}`r`n" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
        [System.IO.File]::AppendAllText($Log, $Line, $Utf8NoBom)
    } catch {
        Write-Host $Message
    }
}

function Stop-TaskIfExists {
    param([string]$TaskName)
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $Task) {
        Write-PowerLog ("Task not found, skip stop: {0}" -f $TaskName)
        return
    }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-PowerLog ("Stop task requested: {0}" -f $TaskName)
}

function Stop-MatchedProcess {
    param(
        [string]$Reason,
        [scriptblock]$Predicate
    )
    $Processes = Get-CimInstance Win32_Process | Where-Object $Predicate
    foreach ($Process in $Processes) {
        if ($Process.ProcessId -eq $PID) {
            continue
        }
        try {
            Write-PowerLog ("Stopping process for {0}: PID={1}, Name={2}" -f $Reason, $Process.ProcessId, $Process.Name)
            Stop-Process -Id $Process.ProcessId -Force -ErrorAction Stop
        } catch {
            Write-PowerLog ("Failed to stop PID={0}: {1}" -f $Process.ProcessId, $_.Exception.Message)
        }
    }
}

Write-PowerLog "AHNS server sleep sequence started."

# ToDesk 是主要远程通道，只忽略它阻止睡眠的电源请求，不结束 ToDesk 进程。
powercfg.exe /requestsoverride PROCESS ToDesk.exe DISPLAY SYSTEM | Out-Null
Write-PowerLog "Applied ToDesk DISPLAY/SYSTEM power request override."

try {
    $WakeTimerText = (powercfg.exe /waketimers 2>&1 | Out-String).Trim()
    Write-PowerLog ("Wake timers before sleep:`r`n{0}" -f $WakeTimerText)
} catch {
    Write-PowerLog ("Failed to query wake timers: {0}" -f $_.Exception.Message)
}

Stop-TaskIfExists "AHNS Command Watcher"
Stop-TaskIfExists "AHNS Service GUI"
Stop-TaskIfExists "Futu OpenD Autostart"

Start-Sleep -Seconds 5

$AhnsProcessPredicate = {
    $CommandLine = [string]$_.CommandLine
    if ($_.Name -notmatch "^python") {
        return $false
    }
    if ($CommandLine -match "service_command_watcher.py") {
        return $true
    }
    if ($CommandLine -match "service_gui.py") {
        return $true
    }
    if ($CommandLine -match "service_runner.py") {
        return $true
    }
    if ($CommandLine -match "service_main.py") {
        return $true
    }
    if ($CommandLine -match "git_main.py") {
        return $true
    }
    return ($CommandLine -match "C:\\Users\\Administrator\\Desktop\\AHNS")
}
Stop-MatchedProcess -Reason "AHNS python jobs" -Predicate $AhnsProcessPredicate

$FutuProcessPredicate = {
    $CommandLine = [string]$_.CommandLine
    if ($_.Name -eq "Futu_OpenD.exe") {
        return $true
    }
    if ($_.Name -ne "CrashReporter.exe") {
        return $false
    }
    return ($CommandLine -match "FutuOpenD|Futu_OpenD|futunn")
}
Stop-MatchedProcess -Reason "Futu OpenD" -Predicate $FutuProcessPredicate

Write-PowerLog "Requesting forced S3 sleep with wake events enabled."
try {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
    $SuspendResult = [System.Windows.Forms.Application]::SetSuspendState(
        [System.Windows.Forms.PowerState]::Suspend,
        $true,
        $false
    )
    Write-PowerLog ("Returned from S3 sleep; SetSuspendState result={0}." -f $SuspendResult)
    if (-not $SuspendResult) {
        exit 1
    }
} catch {
    Write-PowerLog ("S3 sleep request failed: {0}" -f $_.Exception.Message)
    exit 1
}
