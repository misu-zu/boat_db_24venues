$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$StartScript = Join-Path $ProjectRoot "start_daily_all_venues.ps1"
$TaskName = "BoatraceOddsDailyCollector"

if (-not (Test-Path $StartScript)) {
    throw "start script not found: $StartScript"
}

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`""

$DailyTrigger = New-ScheduledTaskTrigger -Daily -At 7:00
$LogonTrigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger @($DailyTrigger, $LogonTrigger) `
    -Settings $Settings `
    -Description "BOAT RACE odds collector: discover all 24 venues daily and collect held venues only." `
    -Force | Out-Null

Write-Output "registered scheduled task: $TaskName"
