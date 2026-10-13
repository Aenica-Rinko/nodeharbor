param([int]$EveryHours = 1)
$ErrorActionPreference = 'Stop'
if ($EveryHours -lt 1 -or $EveryHours -gt 24) { throw 'EveryHours must be between 1 and 24.' }
$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$TaskPython = Join-Path $AppRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $TaskPython)) { $TaskPython = (Get-Command python -ErrorAction Stop).Source }
$Launcher = Join-Path $AppRoot 'NodeHarbor.py'
$TaskArguments = '"' + $Launcher + '" refresh-github'
$Action = New-ScheduledTaskAction -Execute $TaskPython -Argument $TaskArguments -WorkingDirectory $AppRoot
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Hours $EveryHours)
$Options = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -StartWhenAvailable
Register-ScheduledTask -TaskName 'ProxyNodeFetcher' -Action $Action -Trigger $Trigger -Settings $Options -Description 'NodeHarbor subscription refresh' -Force | Out-Null
Write-Host "NodeHarbor GitHub nodes scheduled every $EveryHours hour(s)."
