# Registers a Task Scheduler task that starts the RAG daemon at user logon.
# Run once as the current user (no admin required for logon triggers).

$projectDir = (Resolve-Path "$PSScriptRoot\..").Path
$batPath    = Join-Path $projectDir "scripts\start_daemon.bat"
$taskName   = "ObsidianRagDaemon"

$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$batPath`"" -WorkingDirectory $projectDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Force

Write-Host "Task '$taskName' registered. It will run at next logon."
Write-Host "To start it now without rebooting:"
Write-Host "  Start-ScheduledTask -TaskName '$taskName'"
