[CmdletBinding()]
param(
  [string]$ControlPlane = 'http://127.0.0.1:8081',
  [System.Management.Automation.PSCredential]$Credential,
  [string]$AgentToken = $env:OPENMYCELIUM_AGENT_TOKEN,
  # Print the discovered profile without contacting the control plane.
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$cpu = Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1
$system = Get-CimInstance -ClassName Win32_ComputerSystem
# Win32_VideoController.AdapterRAM is a signed 32-bit value and saturates at
# 4 GiB, so a 16 GiB card reports 4 GiB. The display-class registry key holds
# the true 64-bit size in HardwareInformation.qwMemorySize.
$displayClass = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
$vramByName = @{}
Get-ChildItem $displayClass -ErrorAction SilentlyContinue |
  Where-Object { $_.PSChildName -match '^\d{4}$' } |
  ForEach-Object {
    $entry = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
    if ($entry.DriverDesc -and $entry.'HardwareInformation.qwMemorySize') {
      $vramByName[[string]$entry.DriverDesc] = [uint64]$entry.'HardwareInformation.qwMemorySize'
    }
  }

# Claim a compute runtime only when the vendor's own tooling answers here.
$hasNvidiaSmi = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
$hasRocmSmi = [bool](Get-Command rocm-smi -ErrorAction SilentlyContinue)

$gpus = Get-CimInstance -ClassName Win32_VideoController | ForEach-Object {
  $vendor = if ($_.Name -match 'NVIDIA') { 'NVIDIA' } elseif ($_.Name -match 'AMD|Radeon') { 'AMD' } elseif ($_.Name -match 'Intel') { 'Intel' } else { 'Unknown' }
  $bytes = $vramByName[[string]$_.Name]
  $source = if ($bytes) { 'windows-registry:qwMemorySize' } elseif ($_.AdapterRAM) { 'wmi:AdapterRAM (32-bit, may be capped)' } else { 'unreported' }
  if (-not $bytes -and $_.AdapterRAM) { $bytes = [uint64]$_.AdapterRAM }
  $runtime = switch ($vendor) {
    'NVIDIA' { if ($hasNvidiaSmi) { 'cuda' } else { 'display-only' } }
    'AMD'    { if ($hasRocmSmi) { 'rocm' } else { 'display-only' } }
    default  { 'display-only' }
  }
  [PSCustomObject]@{
    id = ('windows-gpu-' + $_.DeviceID.Replace('\', '-').Replace('/', '-'))
    vendor = $vendor
    model = $_.Name
    runtime = $runtime
    computeReady = ($runtime -ne 'display-only')
    memory = if ($bytes) { ('{0:N1} GiB' -f ($bytes / 1GB)) } else { 'reported by Windows' }
    memoryMiB = if ($bytes) { [int]($bytes / 1MB) } else { 0 }
    memorySource = $source
    health = if ($_.Status) { $_.Status } else { 'unknown' }
    simulated = $false
  }
}
$profile = [PSCustomObject]@{
  name = $env:COMPUTERNAME
  os = (Get-CimInstance Win32_OperatingSystem).Caption
  cpu = $cpu.Name.Trim()
  logicalCores = [int]$cpu.NumberOfLogicalProcessors
  memoryGB = [math]::Round($system.TotalPhysicalMemory / 1GB, 1)
  accelerators = @($gpus)
}
$json = $profile | ConvertTo-Json -Depth 5
if ($DryRun) {
  Write-Output $json
  return
}
$request = @{
  Method = 'Post'
  Uri = "$($ControlPlane.TrimEnd('/'))/api/v1/discovery/import"
  ContentType = 'application/json'
  Body = $json
}
if ($AgentToken) {
  $request.Headers = @{ Authorization = "Bearer $AgentToken" }
} else {
  if (-not $Credential) {
    $Credential = Get-Credential -Message 'OpenMycelium platform administrator or operator'
  }
  if (-not $Credential) {
    throw 'OpenMycelium credentials or an agent token are required.'
  }
  $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
  $loginBody = @{
    email = $Credential.UserName
    password = $Credential.GetNetworkCredential().Password
  } | ConvertTo-Json
  Invoke-RestMethod -Method Post -Uri "$($ControlPlane.TrimEnd('/'))/api/v1/auth/login" -ContentType 'application/json' -Body $loginBody -WebSession $session | Out-Null
  $loginBody = $null
  $request.WebSession = $session
}
Invoke-RestMethod @request
Write-Host "OpenMycelium host profile uploaded to $ControlPlane"
