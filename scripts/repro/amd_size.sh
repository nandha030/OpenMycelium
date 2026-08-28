#!/usr/bin/env bash
# Exact download size for each ROCm-on-WSL option, using --print-uris, which
# reports the real byte counts apt would fetch.
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive

measure() {                    # label  packages...
  local label=$1; shift
  local uris total count
  uris=$(apt-get install --print-uris --no-install-recommends -y "$@" 2>/dev/null \
         | grep "^'" || true)
  if [ -z "$uris" ]; then
    printf '    %-32s could not be resolved\n' "$label"
    return
  fi
  count=$(echo "$uris" | wc -l)
  total=$(echo "$uris" | awk '{s+=$3} END {print s+0}')
  printf '    %-32s %3s files  %7.1f MiB  (~%.0f min at 1.5 MB/s)\n' \
    "$label" "$count" "$(awk -v t="$total" 'BEGIN{print t/1048576}')" \
    "$(awk -v t="$total" 'BEGIN{print t/1500000/60}')"
  echo "$uris" | awk '{print $2}' | head -6 | sed 's/^/        /'
}

echo "  == option A: the one package torch actually needs =="
measure "hsa-runtime-rocr4wsl-amdgpu" hsa-runtime-rocr4wsl-amdgpu

echo
echo "  == option B: AMD's documented installer, then its wsl,rocm usecase =="
measure "amdgpu-install" amdgpu-install

echo
echo "  == what the working distribution actually has from these repos =="
echo "    (for reference: package that owns the runtime file)"
apt-cache show hsa-runtime-rocr4wsl-amdgpu 2>/dev/null \
  | grep -E '^(Package|Version|Size|Installed-Size|Depends)' | head -8 | sed 's/^/      /'
