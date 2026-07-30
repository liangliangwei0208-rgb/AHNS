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
$WatcherScript = Join-Path $Repo "service_command_watcher.py"
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

Set-Location -LiteralPath $Repo

# Windows PowerShell 5.1 的 *>> 会强制写 UTF-16。这里交给 cmd 做原始字节重定向，
# 配合 PYTHONIOENCODING=utf-8，保证长期日志始终是 UTF-8。
$StartLine = "[AHNS-COMMAND] Watcher launcher started at {0}." -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
[System.IO.File]::AppendAllText($Log, $StartLine + "`r`n", $Utf8NoBom)

$CommandLine = '"{0}" "{1}" --interval-seconds 60 --primary-remote gitee >> "{2}" 2>&1' -f $Python, $WatcherScript, $Log
& $env:ComSpec /d /s /c $CommandLine
$WatcherExitCode = [int]$LASTEXITCODE

$ExitLine = "[AHNS-COMMAND] Watcher launcher exited at {0}; exit code={1}." -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $WatcherExitCode
[System.IO.File]::AppendAllText($Log, $ExitLine + "`r`n", $Utf8NoBom)
exit $WatcherExitCode
