$ErrorActionPreference = "Continue"

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$Repo = "C:\Users\Administrator\Desktop\AHNS"
$Log = Join-Path $Repo "logs\health_monitor.log"
$StatePath = Join-Path $Repo "logs\health_monitor_state.json"
$ToDeskExe = "D:\todesk\ToDesk.exe"
$MaxLogBytes = 5MB
$KeepLogTailLines = 1500
$Now = Get-Date

# 只在北京时间 06:00-24:00 执行进程自恢复，凌晨休眠时不主动拉起服务。
if ($Now.Hour -lt 6) {
    exit 0
}

New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
if (Test-Path -LiteralPath $Log) {
    $LogItem = Get-Item -LiteralPath $Log
    if ($LogItem.Length -gt $MaxLogBytes) {
        $Tail = Get-Content -LiteralPath $Log -Encoding UTF8 -Tail $KeepLogTailLines
        [System.IO.File]::WriteAllLines($Log, $Tail, $Utf8NoBom)
    }
}

function Write-HealthLog {
    param([string]$Message)
    $Line = "[{0}] {1}`r`n" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    [System.IO.File]::AppendAllText($Log, $Line, $Utf8NoBom)
}

function Test-CommandLineProcess {
    param([string]$Pattern)
    $Matched = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { ([string]$_.CommandLine) -match $Pattern }
    return (@($Matched).Count -gt 0)
}

function Start-TaskSafely {
    param([string]$TaskName)
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $Task) {
        Write-HealthLog ("Task not found: {0}" -f $TaskName)
        return $false
    }
    try {
        if ($Task.State -eq "Running") {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
        Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        Write-HealthLog ("Start task requested: {0}" -f $TaskName)
        return $true
    } catch {
        Write-HealthLog ("Failed to start task {0}: {1}" -f $TaskName, $_.Exception.Message)
        return $false
    }
}

function Read-MonitorState {
    if (-not (Test-Path -LiteralPath $StatePath)) {
        return $null
    }
    try {
        return (Get-Content -LiteralPath $StatePath -Encoding UTF8 -Raw | ConvertFrom-Json)
    } catch {
        Write-HealthLog ("Failed to read monitor state; starting fresh: {0}" -f $_.Exception.Message)
        return $null
    }
}

function Write-MonitorState {
    param(
        [datetime[]]$RestartTimes,
        [Nullable[datetime]]$LastRebootRequest
    )
    $Payload = [ordered]@{
        watcher_restart_times = @($RestartTimes | ForEach-Object { $_.ToString("o") })
        last_reboot_request = if ($LastRebootRequest.HasValue) { $LastRebootRequest.Value.ToString("o") } else { $null }
    }
    $Json = $Payload | ConvertTo-Json -Depth 3
    [System.IO.File]::WriteAllText($StatePath, $Json + "`r`n", $Utf8NoBom)
}

$State = Read-MonitorState
$RestartTimes = @()
$LastRebootRequest = $null
if ($null -ne $State) {
    foreach ($Value in @($State.watcher_restart_times)) {
        $Parsed = [datetime]::MinValue
        if ([datetime]::TryParse([string]$Value, [ref]$Parsed)) {
            $RestartTimes += $Parsed
        }
    }
    if ($State.last_reboot_request) {
        $ParsedReboot = [datetime]::MinValue
        if ([datetime]::TryParse([string]$State.last_reboot_request, [ref]$ParsedReboot)) {
            $LastRebootRequest = $ParsedReboot
        }
    }
}

$WatcherHealthy = Test-CommandLineProcess "service_command_watcher\.py"
if (-not $WatcherHealthy) {
    [void](Start-TaskSafely "AHNS Command Watcher")
    $RestartTimes += $Now
} else {
    $RestartTimes = @()
}

$RecentCutoff = $Now.AddMinutes(-15)
$RestartTimes = @($RestartTimes | Where-Object { $_ -ge $RecentCutoff })

if (-not (Get-Process -Name "Futu_OpenD" -ErrorAction SilentlyContinue)) {
    [void](Start-TaskSafely "Futu OpenD Autostart")
}

if (-not (Get-Process -Name "ToDesk" -ErrorAction SilentlyContinue)) {
    if (Test-Path -LiteralPath $ToDeskExe) {
        try {
            Start-Process -FilePath $ToDeskExe
            Write-HealthLog "ToDesk process was missing and has been started."
        } catch {
            Write-HealthLog ("Failed to start ToDesk: {0}" -f $_.Exception.Message)
        }
    }
}

if (-not (Test-CommandLineProcess "service_gui\.py")) {
    [void](Start-TaskSafely "AHNS Service GUI")
}

# 监听器连续三次无法恢复时才重启 Windows，并设置六小时限频，避免重启循环。
if ($RestartTimes.Count -ge 3) {
    $CanReboot = ($null -eq $LastRebootRequest) -or ($LastRebootRequest -lt $Now.AddHours(-6))
    if ($CanReboot) {
        $LastRebootRequest = $Now
        Write-MonitorState -RestartTimes $RestartTimes -LastRebootRequest $LastRebootRequest
        Write-HealthLog "Watcher failed repeatedly; Windows restart scheduled in 60 seconds."
        shutdown.exe /r /t 60 /f /c "AHNS watcher failed repeatedly; automatic recovery restart." | Out-Null
        exit 2
    }
    Write-HealthLog "Watcher failed repeatedly, but reboot rate limit is active."
}

Write-MonitorState -RestartTimes $RestartTimes -LastRebootRequest $LastRebootRequest
Write-HealthLog ("Health check complete: watcher={0}, Futu={1}, ToDesk={2}, GUI={3}." -f
    (Test-CommandLineProcess "service_command_watcher\.py"),
    [bool](Get-Process -Name "Futu_OpenD" -ErrorAction SilentlyContinue),
    [bool](Get-Process -Name "ToDesk" -ErrorAction SilentlyContinue),
    (Test-CommandLineProcess "service_gui\.py"))

exit 0
