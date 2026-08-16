param(
    [Parameter(Mandatory = $true)]
    [string]$VagrantDirectory,
    [string]$Server = "https://192.168.56.10:6443",
    [string]$OutputPath = ".openmycelium.kubeconfig"
)

$ErrorActionPreference = "Stop"
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$rbacManifest = Join-Path $projectRoot "k8s\remote-access.yaml"
$vagrantRoot = Resolve-Path $VagrantDirectory
$output = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputPath))

Push-Location $vagrantRoot
try {
    Get-Content -LiteralPath $rbacManifest -Raw | vagrant ssh control-plane -c "sudo k3s kubectl apply -f -"
    $token = (vagrant ssh control-plane -c "sudo k3s kubectl -n openmycelium-system create token openmycelium-controller --duration=8760h").Trim()
    $certificate = (vagrant ssh control-plane -c "sudo base64 -w0 /var/lib/rancher/k3s/server/tls/server-ca.crt").Trim()
} finally {
    Pop-Location
}

if (-not $token -or -not $certificate) {
    throw "Could not obtain the OpenMycelium service-account token or cluster CA."
}

$kubeconfig = @"
apiVersion: v1
kind: Config
clusters:
  - name: vagrant-k3s
    cluster:
      server: $Server
      certificate-authority-data: $certificate
users:
  - name: openmycelium-controller
    user:
      token: $token
contexts:
  - name: openmycelium@vagrant-k3s
    context:
      cluster: vagrant-k3s
      user: openmycelium-controller
current-context: openmycelium@vagrant-k3s
"@

[System.IO.File]::WriteAllText($output, $kubeconfig, [System.Text.UTF8Encoding]::new($false))
Set-Clipboard -Value $kubeconfig
Write-Host "Verified kubeconfig written to $output"
Write-Host "The kubeconfig is also in your clipboard. Paste it into OpenMycelium > Clusters > Connect cluster."
