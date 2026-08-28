#!/usr/bin/env bash
# Preserve the 0.1.0a7 validation evidence from om-clean2.
set -uo pipefail
D=/mnt/c/Users/User/Documents/Open_Mycelium/release/0.1.0a7/validation
mkdir -p "$D"
cp -r /var/log/om-a7/. "$D"/ 2>/dev/null
cp /var/log/om-bootstrap/post.log "$D/post-provision.log" 2>/dev/null
cp /var/log/om-bootstrap/reboot.log "$D/reboot.log" 2>/dev/null
cp /var/log/om-bootstrap/api.log "$D/api.log" 2>/dev/null
cp /var/log/om-bootstrap/gpu-cuda.json "$D/" 2>/dev/null
cp /var/log/om-bootstrap/gpu-rocm.json "$D/" 2>/dev/null
cp /var/lib/openmycelium/xvendor_qualification.json "$D/ledger.json" 2>/dev/null
rm -f "$D/serve-token.txt"
dpkg -l 2>/dev/null | awk '/rocm-core|rocr4wsl/ {print $2, $3}' > "$D/amd-packages.txt"
{
  echo "AMD prerequisite installed manually on $(date -Is)"
  echo "repositories:"
  cat /etc/apt/sources.list.d/rocm.list /etc/apt/sources.list.d/amdgpu.list 2>/dev/null
  echo "signing key sha256: $(sha256sum /etc/apt/keyrings/rocm.gpg | cut -d' ' -f1)"
  echo "packages:"
  cat "$D/amd-packages.txt"
  echo "runtime file:"
  R=$(readlink -f /opt/rocm/lib/libhsa-runtime64.so.1)
  echo "  $R  $(stat -c%s "$R") bytes  $(sha256sum -b "$R" | cut -d' ' -f1)"
} > "$D/amd-prerequisite.txt"
echo "  preserved to $D"
ls -1 "$D" | sed 's/^/    /'
