param(
    [ValidateRange(0, 23)]
    [int]$Hour = 2
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Create the project virtual environment first: $python"
}
if (-not (Test-Path (Join-Path $projectRoot ".env"))) {
    throw "Copy .env.example to .env and configure the database before scheduling backups."
}

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument '-m flask --app run backup-database --scheduled' `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours($Hour))
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName "OBP Daily Database Backup" `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Description "Create and retain a daily Optimum Business Products PostgreSQL backup." `
    -Force | Out-Null

Write-Output "Registered the OBP daily database backup at $Hour`:00 for $env:USERNAME."
