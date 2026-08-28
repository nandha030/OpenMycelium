set -u
R=/mnt/c/Users/User/Documents/Open_Mycelium/release/0.1.0a5
D=/mnt/c/Users/User/Documents/Open_Mycelium/dist
for w in openmycelium-0.1.0a5-py3-none-any.whl openmycelium_mccl-0.2.0a2-py3-none-any.whl; do
  if [ -e "$R/$w" ]; then echo "   refusing to overwrite existing artifact: $w"
  else cp "$D/$w" "$R/$w" && echo "   staged $w"; fi
done
cd "$R" && sha256sum -c SHA256SUMS.frozen 2>&1 | grep -v "No such" | sed 's/^/   /'
cat > "$R/network_rates.json" <<'JSON'
{
  "measuredOn": "2026-08-28",
  "method": "curl, 20 s per trial, two trials per source, no cache",
  "sources": {
    "pypi":            {"host": "files.pythonhosted.org", "trialsMBps": [1.56, 1.74], "medianMBps": 1.65},
    "pytorchCu128":    {"host": "download.pytorch.org/whl/cu128",   "trialsMBps": [1.36, 1.66], "medianMBps": 1.51},
    "pytorchRocm70":   {"host": "download.pytorch.org/whl/rocm7.0", "trialsMBps": [1.58, 1.37], "medianMBps": 1.48}
  },
  "downloadVolume": {
    "cudaEnvironmentGiB": 3.78, "rocmEnvironmentGiB": 4.56, "totalGiB": 8.34,
    "note": "resolved from the installed package sets; the ROCm torch wheel bundles its runtime (4.50 GiB), the CUDA wheel is 0.76 GiB plus separate nvidia-* wheels"
  },
  "projection": {"transferMinutes": [90, 101], "totalEstimateHours": 2.4,
                 "decision": "under three hours: proceed with the full new-WSL bootstrap, online acquisition, no wheelhouse"},
  "dependencyOrigin": "online, from the pinned vendor indexes"
}
JSON
echo "   wrote network_rates.json"
ls -1 "$R" | sed 's/^/   /'
