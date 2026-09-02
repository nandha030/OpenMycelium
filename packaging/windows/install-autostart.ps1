<#
.SYNOPSIS
    Register, remove or inspect the scheduled task that starts the console at logon.

.DESCRIPTION
    At logon rather than at boot. WSL2 needs a user session: a task set to run
    "whether the user is logged on or not" starts before there is a session for
    the distribution to live in, and fails in a way that looks like the runtime
    is broken.

    The task runs as the current user, because WSL distributions and the
    qualification ledger are per-user. Running it as SYSTEM would start a
    distribution the operator cannot see.

    Read the security note in openmycelium-autostart.ps1 before installing.
    The console has no authentication in this release.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install-autostart.ps1 -Action install
    powershell -ExecutionPolicy Bypass -File install-autostart.ps1 -Action status
    powershell -ExecutionPolicy Bypass -File install-autostart.ps1 -Action remove
#>

[CmdletBinding()]
param(
    [ValidateSet("install", "remove", "status", "run")]
    [string] $Action = "status",
    [string] $TaskName = "OpenMycelium Console",
    [string] $Script = "C:\Users\User\Documents\Open_Mycelium\packaging\windows\openmycelium-autostart.ps1",
    [string] $Distro = "om-clean2"
)

$ErrorActionPreference = "Stop"

function Get-Task {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

switch ($Action) {

    "status" {
        $task = Get-Task
        if (-not $task) {
            Write-Output "  not installed"
            Write-Output "  install with: -Action install"
            break
        }
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Output "  task            $TaskName"
        Write-Output "  state           $($task.State)"
        Write-Output "  last run        $($info.LastRunTime)"
        Write-Output "  last result     $($info.LastTaskResult)  (0 = success)"
        Write-Output "  next run        $($info.NextRunTime)"
    }

    "install" {
        if (-not (Test-Path $Script)) { throw "script not found: $Script" }

        # Not $action: PowerShell parameter names are case-insensitive, so a
        # local $action reassigns this script's own -Action parameter and trips
        # its ValidateSet with "MSFT_TaskExecAction is not a valid value".
        $taskAction = New-ScheduledTaskAction `
            -Execute "powershell.exe" `
            -Argument ("-NoProfile -NonInteractive -WindowStyle Hidden " +
                       "-ExecutionPolicy Bypass -File `"$Script`" -Distro $Distro")

        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

        # A delay, because the console probes the GPUs on its first readiness
        # call and the drivers are not always ready the instant a session
        # appears. Starting late is invisible; starting early looks like a
        # hardware fault.
        $trigger.Delay = "PT30S"

        # Interactive: WSL needs the user's session. S4U or SYSTEM would start
        # a distribution the operator cannot see.
        $principal = New-ScheduledTaskPrincipal `
            -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 1)

        Register-ScheduledTask -TaskName $TaskName -Action $taskAction `
            -Trigger $trigger -Principal $principal -Settings $settings `
            -Description ("Starts the OpenMycelium operator console on " +
                          "127.0.0.1:11501 at logon. No authentication in " +
                          "this release; loopback only.") `
            -Force | Out-Null

        Write-Output "  installed: $TaskName"
        Write-Output "  starts the console at logon, 30s after the session begins"
        Write-Output "  console:   http://127.0.0.1:11501"
        Write-Output "  remove with: -Action remove"
    }

    "remove" {
        if (-not (Get-Task)) { Write-Output "  not installed"; break }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "  removed: $TaskName"
        Write-Output "  a console already running is left alone; stop it with Ctrl+C"
    }

    "run" {
        if (-not (Get-Task)) { throw "not installed; run with -Action install" }
        Start-ScheduledTask -TaskName $TaskName
        Write-Output "  started; give it a few seconds, then check http://127.0.0.1:11501"
    }
}
