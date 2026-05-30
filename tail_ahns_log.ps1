$ErrorActionPreference = "Continue"

$Repo = "C:\Users\Administrator\Desktop\AHNS"
$Log = Join-Path $Repo "logs\service_command_watcher.log"

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
try {
    chcp.com 65001 | Out-Null
    [Console]::InputEncoding = $Utf8NoBom
    [Console]::OutputEncoding = $Utf8NoBom
    $OutputEncoding = $Utf8NoBom
} catch {
    # Older or non-interactive PowerShell hosts may reject console changes.
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if (-not (Test-Path -LiteralPath $Log)) {
    Write-Host "日志文件不存在: $Log"
    exit 1
}

Get-Content -LiteralPath $Log -Encoding UTF8 -Tail 120 -Wait
