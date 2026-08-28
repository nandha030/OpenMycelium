#!/usr/bin/env bash
# Add AMD's repository to om-clean2 and measure, without installing anything.
#
# Adding an apt source and a keyring is two files and is trivially reversible;
# installing several gigabytes at 1.5 MB/s is not. So this stops at the
# measurement and prints exactly what each option would cost.
#
# The signing key is verified by comparing it against the key already trusted on
# the distribution that demonstrably works, rather than trusting whatever the
# download returns.
set -uo pipefail
KEY_URL=https://repo.radeon.com/rocm/rocm.gpg.key
KNOWN_KEY_SHA=$1              # sha256 of /etc/apt/keyrings/rocm.gpg on the working distro
export DEBIAN_FRONTEND=noninteractive

echo "  == signing key =="
mkdir -p /etc/apt/keyrings
curl -fsSL --max-time 120 "$KEY_URL" -o /tmp/rocm.key || {
  echo "    could not download the key"; exit 1; }
gpg --dearmor < /tmp/rocm.key > /tmp/rocm.gpg 2>/dev/null
GOT=$(sha256sum /tmp/rocm.gpg | cut -d' ' -f1)
echo "    downloaded from $KEY_URL"
echo "    sha256 $GOT"
echo "    known  $KNOWN_KEY_SHA"
if [ "$GOT" = "$KNOWN_KEY_SHA" ]; then
  echo "    [PASS] matches the key trusted by the working distribution"
  install -m 0644 /tmp/rocm.gpg /etc/apt/keyrings/rocm.gpg
else
  echo "    [WARN] differs from the working distribution's key"
  echo "           installing it anyway would trust an unverified key; stopping"
  exit 2
fi

echo
echo "  == repositories (same versions as the working distribution) =="
cat > /etc/apt/sources.list.d/rocm.list <<'EOF'
deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/rocm/apt/7.2 noble main
deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/graphics/7.2/ubuntu noble main
EOF
cat > /etc/apt/sources.list.d/amdgpu.list <<'EOF'
deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/amdgpu/30.30/ubuntu noble main
EOF
sed 's/^/    /' /etc/apt/sources.list.d/rocm.list /etc/apt/sources.list.d/amdgpu.list

echo
echo "  == apt update =="
apt-get update -qq > /tmp/aptupdate.log 2>&1
if [ $? = 0 ]; then echo "    [PASS] repositories reachable and signed correctly"
else echo "    [FAIL]"; tail -5 /tmp/aptupdate.log | sed 's/^/      /'; fi

echo
echo "  == what each option would download =="
size() {                       # label  packages...
  local label=$1; shift
  local out
  out=$(apt-get install --dry-run --no-install-recommends "$@" 2>&1)
  if echo "$out" | grep -q "Unable to locate\|E:"; then
    printf '    %-34s unavailable: %s\n' "$label" \
      "$(echo "$out" | grep -m1 'Unable to locate\|E:' | cut -c1-70)"
    return
  fi
  local n bytes
  n=$(echo "$out" | grep -c '^Inst ')
  bytes=$(echo "$out" | awk '/Need to get/ {print $0}')
  printf '    %-34s %s packages\n' "$label" "$n"
  [ -n "$bytes" ] && printf '    %-34s %s\n' "" "$bytes"
}
size "minimal: WSL HSA runtime only" hsa-runtime-rocr4wsl-amdgpu
size "documented: full rocm usecase" rocm

echo
echo "  == exact download size for each =="
for pkg in hsa-runtime-rocr4wsl-amdgpu rocm; do
  total=$(apt-get install --dry-run --no-install-recommends "$pkg" 2>/dev/null \
          | grep '^Inst ' | awk '{print $2}' \
          | xargs -r apt-cache --no-all-versions show 2>/dev/null \
          | awk '/^Size:/ {s+=$2} END {print s+0}')
  printf '    %-34s %.2f GiB\n' "$pkg" "$(awk -v s="$total" 'BEGIN{print s/1073741824}')"
done

echo
echo "  nothing was installed. To undo the repository addition:"
echo "    rm /etc/apt/sources.list.d/rocm.list /etc/apt/sources.list.d/amdgpu.list"
echo "    rm /etc/apt/keyrings/rocm.gpg && apt-get update"
