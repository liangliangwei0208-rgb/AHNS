$ErrorActionPreference = "Stop"

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
try {
    chcp.com 65001 | Out-Null
    [Console]::InputEncoding = $Utf8NoBom
    [Console]::OutputEncoding = $Utf8NoBom
    $OutputEncoding = $Utf8NoBom
} catch {
    # Some double-click sessions do not expose a normal console.
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$Repo = "C:\Users\Administrator\Desktop\AHNS"
$Python = "D:\anaconda\envs\py310\python.exe"
$Log = Join-Path $Repo "logs\service_gui.log"

function Write-GuiStartLog {
    param([string]$Message)
    try {
        $Line = "[{0}] {1}`r`n" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
        [System.IO.File]::AppendAllText($Log, $Line, $Utf8NoBom)
    } catch {
        Write-Host $Message
    }
}

try {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
    Write-GuiStartLog "Starting AHNS Service GUI."
    Set-Location -LiteralPath $Repo
    $GuiArgs = ".\service_gui.py"
    $Process = Start-Process -FilePath $Python -ArgumentList $GuiArgs -WorkingDirectory $Repo -PassThru
    Write-GuiStartLog ("AHNS Service GUI started, PID {0}." -f $Process.Id)
    $Process.WaitForExit()
    $ExitCode = if ($null -eq $Process.ExitCode) { 0 } else { $Process.ExitCode }
    Write-GuiStartLog "AHNS Service GUI exited, exit code $ExitCode."
    exit $ExitCode
} catch {
    Write-GuiStartLog ("AHNS Service GUI startup script error: {0}" -f $_.Exception.Message)
    exit 1
}
