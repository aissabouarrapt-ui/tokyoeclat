# Creates (or replaces) the Windows scheduled task "CLIPPING_GTA6": run.bat every 2 hours.
#  - starts 2 minutes from now, then every 2 h, forever
#  - runs hidden (no window popping up); progress is in log.txt
#  - runs a missed run as soon as possible (PC was asleep/off), wakes the PC if allowed
#  - never two runs at the same time (IgnoreNew + the program's own lock)
#  - runs as you, only while you are logged in (no password stored)
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Name = 'CLIPPING_GTA6'
$Bat = Join-Path $Root 'run.bat'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    schtasks /Delete /TN $Name /F 2>$null | Out-Null
    Write-Host "Task '$Name' removed." -ForegroundColor Yellow
    exit 0
}

try {
    $arg = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"& '$Bat' scheduled`""
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg -WorkingDirectory $Root
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Hours 2)
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3)
    $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description 'CLIPPING_GTA6: find, cut, caption and post GTA clips (run.bat). Stop: create STOP.txt in the folder.' -Force | Out-Null
} catch {
    Write-Host "PowerShell scheduler API failed ($($_.Exception.Message)); using schtasks instead." -ForegroundColor Yellow
    $tr = '\"' + $Bat + '\" scheduled'   # Windows PowerShell 5.1 quoting for a path with spaces
    schtasks /Create /F /TN $Name /SC HOURLY /MO 2 /TR $tr | Out-Host
}

$t = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
if ($t -and $t.Triggers.Count -gt 0 -and $t.Triggers[0].Repetition -and $t.Triggers[0].Repetition.Duration) {
    # Some Windows builds store a finite repetition duration: make it "indefinitely".
    try { $t.Triggers[0].Repetition.Duration = ''; Set-ScheduledTask -InputObject $t | Out-Null } catch {}
}
if ($t) {
    $info = Get-ScheduledTaskInfo -TaskName $Name
    Write-Host "Task '$Name' created. Next run: $($info.NextRunTime). Then every 2 hours." -ForegroundColor Green
} else {
    Write-Host "Task '$Name' could NOT be created." -ForegroundColor Red
    exit 1
}
