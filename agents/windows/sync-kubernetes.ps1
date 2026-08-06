[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$Kubeconfig,
  [string]$ControlPlane = "http://127.0.0.1:8081",
  [string]$ClusterName = "laptop-k3s",
  [System.Management.Automation.PSCredential]$Credential,
  [switch]$Watch,
  [ValidateRange(10, 3600)]
  [int]$IntervalSeconds = 30
)

$ErrorActionPreference = "Stop"
$ControlPlane = $ControlPlane.TrimEnd("/")
$Kubeconfig = (Resolve-Path -LiteralPath $Kubeconfig).Path

if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
  throw "kubectl is required and was not found in PATH."
}

function Invoke-KubectlJson {
  param([string[]]$KubectlArguments)

  $output = & kubectl --kubeconfig $Kubeconfig @KubectlArguments 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "kubectl failed: $($output -join [Environment]::NewLine)"
  }
  return (($output -join [Environment]::NewLine) | ConvertFrom-Json)
}

function Get-KubernetesInventory {
  $nodeList = Invoke-KubectlJson -KubectlArguments @("get", "nodes", "-o", "json")
  $nodes = @($nodeList.items)
  $readyNodes = 0
  $accelerators = 0
  $versions = @()
  $acceleratorPattern = '(?i)(^nvidia\.com/gpu$|^amd\.com/gpu$|^gpu\.intel\.com/|(^|/)(gpu|tpu|npu)$)'

  foreach ($node in $nodes) {
    $ready = @($node.status.conditions | Where-Object { $_.type -eq "Ready" -and $_.status -eq "True" })
    if ($ready.Count -gt 0) {
      $readyNodes++
    }
    if ($node.status.nodeInfo.kubeletVersion) {
      $versions += [string]$node.status.nodeInfo.kubeletVersion
    }
    foreach ($resource in $node.status.allocatable.PSObject.Properties) {
      if ($resource.Name -match $acceleratorPattern) {
        $count = 0
        if ([int]::TryParse([string]$resource.Value, [ref]$count)) {
          $accelerators += $count
        }
      }
    }
  }

  $config = Invoke-KubectlJson -KubectlArguments @("config", "view", "--minify", "-o", "json")
  return [PSCustomObject]@{
    Endpoint = [string]$config.clusters[0].cluster.server
    Nodes = $nodes.Count
    ReadyNodes = $readyNodes
    Accelerators = $accelerators
    Version = (($versions | Sort-Object -Unique) -join ", ")
  }
}

if (-not $Credential) {
  $Credential = Get-Credential -Message "OpenMycelium platform administrator or operator"
}
if (-not $Credential) {
  throw "OpenMycelium credentials are required."
}

$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$loginBody = @{
  email = $Credential.UserName
  password = $Credential.GetNetworkCredential().Password
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$ControlPlane/api/v1/auth/login" -ContentType "application/json" -Body $loginBody -WebSession $session | Out-Null
$loginBody = $null

function Sync-OpenMyceliumCluster {
  $inventory = Get-KubernetesInventory
  $clusterResponse = Invoke-RestMethod -Uri "$ControlPlane/api/v1/clusters" -WebSession $session
  $cluster = @($clusterResponse.clusters | Where-Object { $_.name -eq $ClusterName }) | Select-Object -First 1

  if (-not $cluster) {
    $registration = @{
      name = $ClusterName
      endpoint = $inventory.Endpoint
      type = "kubernetes"
    } | ConvertTo-Json
    $cluster = Invoke-RestMethod -Method Post -Uri "$ControlPlane/api/v1/clusters" -ContentType "application/json" -Body $registration -WebSession $session
  }

  $report = @{
    nodes = $inventory.Nodes
    readyNodes = $inventory.ReadyNodes
    accelerators = $inventory.Accelerators
    version = $inventory.Version
  } | ConvertTo-Json
  $result = Invoke-RestMethod -Method Patch -Uri "$ControlPlane/api/v1/clusters/$($cluster.id)/inventory" -ContentType "application/json" -Body $report -WebSession $session

  [PSCustomObject]@{
    Cluster = $result.name
    Status = $result.status
    Nodes = "$($result.readyNodes)/$($result.nodes) ready"
    Accelerators = $result.accelerators
    Version = $result.version
    Endpoint = $result.endpoint
    ReportedAt = $result.updatedAt
  }
}

do {
  Sync-OpenMyceliumCluster | Format-List
  if ($Watch) {
    Start-Sleep -Seconds $IntervalSeconds
  }
} while ($Watch)
