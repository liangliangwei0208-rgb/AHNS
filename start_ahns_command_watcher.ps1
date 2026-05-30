$ErrorActionPreference = "Continue"

$Repo = "C:\Users\Administrator\Desktop\AHNS"
$Python = "D:\anaconda\envs\py310\python.exe"
$Log = Join-Path $Repo "logs\service_command_watcher.log"

$StartTime = [TimeSpan]::FromHours(6)
$NowTime = (Get-Date).TimeOfDay
if ($NowTime -lt $StartTime) {
    exit 0
}

New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null

$RemoteArgs = @("--primary-remote", "gitee")

Set-Location -LiteralPath $Repo
& $Python ".\service_command_watcher.py" --interval-seconds 60 @RemoteArgs *>> $Log
