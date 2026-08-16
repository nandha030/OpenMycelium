[CmdletBinding()]
param(
  [string]$ControlPlane = 'http://127.0.0.1:8081',
  [System.Management.Automation.PSCredential]$Credential,
  [string]$AgentToken = $env:OPENMYCELIUM_AGENT_TOKEN
)

$ErrorActionPreference = 'Stop'
$cpu = Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1
$system = Get-CimInstance -ClassName Win32_ComputerSystem
$gpus = Get-CimInstance -ClassName Win32_VideoController | ForEach-Object {
  $vendor = if ($_.Name -match 'NVIDIA') { 'NVIDIA' } elseif ($_.Name -match 'AMD|Radeon') { 'AMD' } elseif ($_.Name -match 'Intel') { 'Intel' } else { 'Unknown' }
  [PSCustomObject]@{
    id = ('windows-gpu-' + $_.DeviceID.Replace('\', '-').Replace('/', '-'))
    vendor = $vendor
    model = $_.Name
    runtime = 'windows-display-driver'
    memory = if ($_.AdapterRAM) { ('{0:N1} GiB' -f ($_.AdapterRAM / 1GB)) } else { 'reported by Windows' }
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
