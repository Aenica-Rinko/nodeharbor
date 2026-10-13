$ErrorActionPreference = 'Stop'
Unregister-ScheduledTask -TaskName 'ProxyNodeFetcher' -Confirm:$false
Write-Host 'NodeHarbor scheduled task removed.'
