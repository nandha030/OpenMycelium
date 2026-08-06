param([string]$ControlPlane = 'http://127.0.0.1:8081')

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
Invoke-RestMethod -Method Post -Uri "$ControlPlane/api/v1/discovery/import" -ContentType 'application/json' -Body $json
Write-Host "OpenMycelium host profile uploaded to $ControlPlane"
