param()
$ErrorActionPreference = 'Stop'
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python 3.10+ is required and must be on PATH.' }
Write-Host 'Starting OpenMycelium at http://127.0.0.1:8080'
python .\openmycelium.py serve
