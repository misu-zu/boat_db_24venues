$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeRoot = Join-Path (Split-Path -Parent $ProjectRoot) "boat_db_8min_runtime"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$BundledPython = "C:\Users\みすず\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (Test-Path $VenvPython) {
    $Python = $VenvPython
} elseif (Test-Path $BundledPython) {
    $Python = $BundledPython
} else {
    $Python = "python"
}

$Date = Get-Date -Format "yyyy-MM-dd"
$ReportDate = Get-Date -Format "yyyyMMdd"
$ReportDir = Join-Path $RuntimeRoot "data\reports"
$ReportFile = Join-Path $ReportDir "summary_$ReportDate.txt"

$env:BOATRACE_ODDS_HOME = $RuntimeRoot
Remove-Item Env:\BOATRACE_ODDS_VENUES -ErrorAction SilentlyContinue
$env:PYTHONIOENCODING = "utf-8"

Set-Location $ProjectRoot
New-Item -ItemType Directory -Force $ReportDir | Out-Null

$Summary = & $Python -m boatrace_odds.cli summary-day --date $Date
$ExitCode = $LASTEXITCODE

@(
    "generated_at=$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK')"
    ""
    $Summary
) | Set-Content -Encoding UTF8 $ReportFile

$Summary
Write-Output ""
Write-Output "report written to $ReportFile"
exit $ExitCode
