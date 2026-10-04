$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force C:\studio\q, C:\studio\done | Out-Null
icacls C:\studio /grant 'demo:(OI)(CI)M' | Out-Null
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\studio\agent.ps1'
$t = New-ScheduledTaskTrigger -AtLogOn -User demo
$p = New-ScheduledTaskPrincipal -UserId demo -LogonType Interactive -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName StudioAgent -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
Start-ScheduledTask -TaskName StudioAgent
Start-Sleep 3; Get-Content C:\studio\done\agent.log
