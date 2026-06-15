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

$env:BOATRACE_ODDS_HOME = $RuntimeRoot
Remove-Item Env:\BOATRACE_ODDS_VENUES -ErrorAction SilentlyContinue
$env:PYTHONIOENCODING = "utf-8"

Set-Location $ProjectRoot

& $Python -m boatrace_odds.cli init-db
& $Python -m boatrace_odds.cli daemon
