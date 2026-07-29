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
$FutuExe = "C:\Users\Administrator\AppData\Roaming\Futu_OpenD\Futu_OpenD.exe"
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

function Start-TaskIfExists {
    param([string]$TaskName)
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $Task) {
        Write-PowerLog ("Task not found, skip start: {0}" -f $TaskName)
        return
    }
    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-PowerLog ("Start task requested: {0}" -f $TaskName)
}

Write-PowerLog "AHNS server wake sequence started."

if (Test-Path -LiteralPath $FutuExe) {
    Start-TaskIfExists "Futu OpenD Autostart"
} else {
    Write-PowerLog ("Futu OpenD exe not found: {0}" -f $FutuExe)
}

# 给 Futu OpenD 留一点启动时间，再启动监听器和 GUI。
Start-Sleep -Seconds 20
Start-TaskIfExists "AHNS Command Watcher"
Start-Sleep -Seconds 5
Start-TaskIfExists "AHNS Service GUI"

Write-PowerLog "AHNS server wake sequence completed."
