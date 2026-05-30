$ErrorActionPreference = "Continue"

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
try {
    chcp.com 65001 | Out-Null
    [Console]::InputEncoding = $Utf8NoBom
    [Console]::OutputEncoding = $Utf8NoBom
    $OutputEncoding = $Utf8NoBom
} catch {
    # Some scheduled-task sessions do not expose a normal console.
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$Repo = "C:\Users\Administrator\Desktop\AHNS"
$Python = "D:\anaconda\envs\py310\python.exe"
$Log = Join-Path $Repo "logs\service_command_watcher.log"
$MaxLogBytes = 20MB
$KeepLogTailLines = 3000

$StartTime = [TimeSpan]::FromHours(6)
$NowTime = (Get-Date).TimeOfDay
if ($NowTime -lt $StartTime) {
    exit 0
}

New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
if (Test-Path -LiteralPath $Log) {
    $LogItem = Get-Item -LiteralPath $Log
    if ($LogItem.Length -gt $MaxLogBytes) {
        $RetainedLines = Get-Content -LiteralPath $Log -Encoding UTF8 -Tail $KeepLogTailLines
        $Header = "[AHNS-COMMAND] Log trimmed at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'); kept last $KeepLogTailLines lines because file exceeded $MaxLogBytes bytes."
        [System.IO.File]::WriteAllLines($Log, @($Header) + $RetainedLines, $Utf8NoBom)
    }
}

$RemoteArgs = @("--primary-remote", "gitee")

Set-Location -LiteralPath $Repo
& $Python ".\service_command_watcher.py" --interval-seconds 60 @RemoteArgs *>> $Log
