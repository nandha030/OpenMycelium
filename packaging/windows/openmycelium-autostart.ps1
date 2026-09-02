<#
.SYNOPSIS
    Start the OpenMycelium console if it is not already serving.

.DESCRIPTION
    Written to be run at logon by a scheduled task, and to be safe to run by
    hand at any time. It is idempotent: if something is already listening on
    the console port it exits without starting a second one, because a second
    console dies with "Address already in use" and leaves a scheduled task
    that appears to have failed when in fact the service was already up.

    WSL2 does not run at boot. A distribution starts when something asks it to,
    and it stops when the last client disconnects -- which is why this holds a
    process for the life of the console rather than firing and forgetting.

    SECURITY. The console binds 127.0.0.1 only and this release has no
    authentication. Autostarting it means any local user of this machine can
    reach it while you are logged in. It can read state, start one inference
    run, and stop that run; it cannot pull, import or remove models, change
    configuration, or hold a token. Decide that is acceptable before installing
    the task -- on a shared machine it may not be.

.PARAMETER Distro
    The WSL distribution holding the runtime. Defaults to the persisted
    OPENMYCELIUM_WSL_DISTRO, then to om-clean2.

.PARAMETER Port
    Console port. Default 11501.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File openmycelium-autostart.ps1
#>

[CmdletBinding()]
param(
    [string] $Distro = $(if ($env:OPENMYCELIUM_WSL_DISTRO) { $env:OPENMYCELIUM_WSL_DISTRO }
                         else { "om-clean2" }),
    [int]    $Port   = 11501,
    [string] $Launcher = "C:\Users\User\Documents\Open_Mycelium\openmycelium.cmd"
)

$ErrorActionPreference = "Stop"

function Test-ConsoleUp {
    param([int] $OnPort)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $connect = $client.BeginConnect("127.0.0.1", $OnPort, $null, $null)
        $ok = $connect.AsyncWaitHandle.WaitOne(700)
        if ($ok -and $client.Connected) { $client.Close(); return $true }
        $client.Close()
    } catch { }
    return $false
}

if (Test-ConsoleUp -OnPort $Port) {
    Write-Output "console already serving on 127.0.0.1:$Port; nothing to do"
    exit 0
}

if (-not (Test-Path $Launcher)) {
    Write-Error "launcher not found: $Launcher"
    exit 70
}

# The distro is passed through the environment rather than as an argument,
# because that is what the launcher reads, and leaving it unset makes the
# launcher stop and ask -- which a scheduled task cannot answer.
$env:OPENMYCELIUM_WSL_DISTRO = $Distro

# Carry the runtime's own variables into WSL. wsl.exe forwards nothing that
# WSLENV does not name.
$carried = "OM_SAFETY_MODE:OM_QUALIFICATION_MODE:OM_QUALIFICATION_ACTOR"
$env:WSLENV = if ($env:WSLENV) { "$env:WSLENV`:$carried" } else { $carried }

Write-Output "starting the console in $Distro on 127.0.0.1:$Port"
& $Launcher console
