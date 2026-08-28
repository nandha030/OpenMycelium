[CmdletBinding()]
param(
    [switch]$Native,
    [switch]$Cuda,
    [switch]$Rocm,
    [string]$Prefix = "$env:LOCALAPPDATA\OpenMycelium\MCCL"
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host 'OM  OPENMYCELIUM / MCCL' -ForegroundColor Green
Write-Host '    Portable heterogeneous collective runtime'

$Python = Get-Command python -ErrorAction SilentlyContinue
$PythonPrefix = @()
if (-not $Python) {
    $Python = Get-Command py -ErrorAction SilentlyContinue
    $PythonPrefix = @('-3')
}
if (-not $Python) { throw 'Python 3.9 or newer is required.' }
& $Python.Source @PythonPrefix -c "import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.9 or newer is required.' }

Write-Host '[1/3] Installing the MCCL Python SDK and CLI'
& $Python.Source @PythonPrefix -m pip install --user --upgrade $Root
if ($LASTEXITCODE -ne 0) { throw 'pip could not install MCCL.' }

if ($Native) {
    $CMake = Get-Command cmake -ErrorAction SilentlyContinue
    if (-not $CMake) { throw 'CMake 3.24 or newer is required for native adapters.' }
    $Build = Join-Path $Root 'build'
    $Arguments = @('-S', (Join-Path $Root 'native'), '-B', $Build, "-DCMAKE_INSTALL_PREFIX=$Prefix")
    if ($Cuda) { $Arguments += '-DMCCL_ENABLE_CUDA=ON' }
    if ($Rocm) { $Arguments += '-DMCCL_ENABLE_ROCM=ON' }
    Write-Host '[2/3] Building selected native adapters'
    & $CMake.Source @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'CMake configuration failed.' }
    & $CMake.Source --build $Build --config Release
    if ($LASTEXITCODE -ne 0) { throw 'Native adapter build failed.' }
    & $CMake.Source --install $Build --config Release
    if ($LASTEXITCODE -ne 0) { throw 'Native adapter installation failed.' }
} else {
    Write-Host '[2/3] Native adapters skipped; use -Native after installing a vendor SDK'
}

Write-Host '[3/3] Running capability discovery'
& $Python.Source @PythonPrefix -m mccl.cli doctor
if ($LASTEXITCODE -ne 0) { throw 'MCCL doctor failed.' }
Write-Host 'MCCL installation complete.' -ForegroundColor Green
