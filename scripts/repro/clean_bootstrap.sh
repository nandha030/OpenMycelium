#!/usr/bin/env bash
# Clean-bootstrap validation for OpenMycelium 0.1.0a6.
#
# Runs inside a brand-new WSL distribution that has never seen this project.
# The claim under test is narrow and has never been proven: that
# `openmycelium provision` can build both GPU environments from nothing. Every
# earlier run only recognised environments that already existed.
#
# Nothing here may depend on the development distro. The only paths reaching
# outside are /mnt/c for the frozen artifacts and the model source, read-only.
set -uo pipefail

REL=/mnt/c/Users/User/Documents/Open_Mycelium/release/0.1.0a6
LOG=/var/log/om-bootstrap
MODEL_SRC=/mnt/c/Users/User/Downloads/Models/Mistral-Nemo-Instruct-2407
OM=/opt/om/venv
HARNESS=${OM_HARNESS:-/mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro}
FAIL=0
mkdir -p "$LOG"

# Run from a neutral directory. WSL starts the shell in the translated Windows
# working directory, which is the checkout, and Python puts the working
# directory on sys.path -- so `import openmycelium` would find the staged wheel
# tree there and the inheritance check would report a failure about the harness
# rather than about the distribution.
cd /root || exit 1

say()   { printf '\n  == %s ==\n' "$1" | tee -a "$LOG/bootstrap.log"; }
log()   { printf '  %s\n' "$*" | tee -a "$LOG/bootstrap.log"; }
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1" | tee -a "$LOG/bootstrap.log"
  else printf '  [FAIL] %s\n' "$1" | tee -a "$LOG/bootstrap.log"; FAIL=$((FAIL+1)); fi
}
phase() { echo "$1 $(date +%s)" >> "$LOG/timing.txt"; }

echo "bootstrap $(date -Is) on $(hostname)" > "$LOG/bootstrap.log"
: > "$LOG/timing.txt"
phase start

# ---------------------------------------------------------------- P0 hygiene
say "P0  nothing is inherited"
H=0
for p in /opt/hetenv /opt/rocmenv /opt/models /opt/omfresh \
         /opt/xvendor_qualification.json /root/.config/openmycelium \
         /opt/hetccl /opt/mccl /opt/omportable; do
  if [ -e "$p" ]; then log "INHERITED: $p exists"; H=$((H+1)); fi
done
[ "$H" = "0" ]
check "no inherited environment, model store or ledger" $?

env | grep -E '^(OPENMYCELIUM_|OM_|PYTHONPATH)=' > "$LOG/env.txt" 2>/dev/null
[ ! -s "$LOG/env.txt" ]
check "no inherited OpenMycelium environment variables" $?

[ -z "${PYTHONPATH:-}" ]
check "no source checkout on PYTHONPATH" $?

if python3 -c "import openmycelium" 2>/dev/null; then false; else true; fi
check "openmycelium is not importable before installation" $?

log "distro   $(. /etc/os-release && echo "$PRETTY_NAME")"
log "kernel   $(uname -r)"
if [ -e /dev/dxg ]; then log "dxg      present"; else log "dxg      MISSING"; fi
[ -e /dev/dxg ]
check "/dev/dxg present (GPU access in a fresh distro)" $?
phase p0

# ------------------------------------------------------------ P1 base system
say "P1  base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >> "$LOG/apt.log" 2>&1
apt-get install -y -qq python3-venv python3-pip curl >> "$LOG/apt.log" 2>&1
check "python3-venv, python3-pip and curl installed" $?
log "python   $(python3 -V 2>&1)"
phase p1

# -------------------------------------------------- P2 install frozen wheels
say "P2  install the frozen 0.1.0a6 artifacts"
mkdir -p /opt/om/wheels
cp "$REL"/*.whl /opt/om/wheels/
# Only the wheels are copied, so verify only the wheels. Checking the whole
# frozen manifest here would report a failure about the sdist not being staged,
# which says nothing about whether what we installed is the frozen build.
grep '\.whl$' "$REL/SHA256SUMS.frozen" > /opt/om/wheels/SHA256SUMS.wheels
cd /opt/om/wheels || exit 1
sha256sum -c SHA256SUMS.wheels > "$LOG/wheelsums.txt" 2>&1
sed 's/^/  /' "$LOG/wheelsums.txt" | tee -a "$LOG/bootstrap.log"
if grep -q "FAILED" "$LOG/wheelsums.txt"; then false; else true; fi
check "frozen wheels match their recorded SHA-256" $?

python3 -m venv "$OM" >> "$LOG/install.log" 2>&1
"$OM/bin/pip" install -q --upgrade pip >> "$LOG/install.log" 2>&1
"$OM/bin/pip" install -q /opt/om/wheels/*.whl >> "$LOG/install.log" 2>&1
check "openmycelium installed from the wheel" $?
export PATH="$OM/bin:$PATH"
unset PYTHONPATH
log "entry    $(command -v openmycelium)"
phase p2

# --------------------------------------------------------- P3 bare provenance
say "P3  provenance from a bare install"
openmycelium version --verbose 2>&1 | head -12 | sed 's/^/  /' | tee -a "$LOG/bootstrap.log"
openmycelium version --json > "$LOG/provenance-fresh.json" 2>/dev/null
"$OM/bin/python" "$HARNESS/check_provenance.py" \
  "$LOG/provenance-fresh.json"
check "installed content hash equals the frozen build" $?
openmycelium config 2>&1 | head -14 | sed 's/^/  /' | tee -a "$LOG/bootstrap.log"
phase p3

# ------------------------------------------------------------- P4 provisioning
say "P4  provision both environments from nothing"
log "this is the claim under test; expect roughly 100 minutes of transfer"
openmycelium provision --dry-run > "$LOG/provision-dryrun.log" 2>&1
grep -E 'would create|would install|already usable' "$LOG/provision-dryrun.log" \
  | head -6 | sed 's/^/  /'
grep -q "would create" "$LOG/provision-dryrun.log"
check "dry run reports it would CREATE both environments, not recognise them" $?

S=$(date +%s)
openmycelium provision > "$LOG/provision.log" 2>&1
RC=$?
E=$(date +%s)
tail -30 "$LOG/provision.log" | sed 's/^/  /'
log "provision took $(( (E - S) / 60 )) min $(( (E - S) % 60 )) s"
echo "provisionSeconds $(( E - S ))" >> "$LOG/timing.txt"
[ "$RC" = "0" ]
check "openmycelium provision created both environments" $?
[ "$(grep -c 'matmul finite' "$LOG/provision.log")" = "2" ]
check "a real BF16 matmul succeeded on both GPUs during provisioning" $?
phase p4

# ------------------------------------------------------------------ P5 doctor
say "P5  doctor against the newly built environments"
openmycelium doctor > "$LOG/doctor.log" 2>&1
sed 's/^/  /' "$LOG/doctor.log"
[ "$(grep -c '\[FAIL\]' "$LOG/doctor.log")" = "0" ]
check "doctor reports no failures" $?
openmycelium fabric list > "$LOG/fabric.log" 2>&1
check "fabric list ran" $?
head -20 "$LOG/fabric.log" | sed 's/^/  /'
phase p5

# ------------------------------------------------------------------- P6 model
say "P6  import the model into the fresh store"
[ -d "$MODEL_SRC" ]
check "model source is readable" $?
S=$(date +%s)
openmycelium model import "$MODEL_SRC" > "$LOG/import.log" 2>&1
RC=$?
E=$(date +%s)
log "import took $(( (E - S) / 60 )) min $(( (E - S) % 60 )) s"
tail -4 "$LOG/import.log" | sed 's/^/  /'
[ "$RC" = "0" ]
check "openmycelium model import" $?
openmycelium model list 2>&1 | sed 's/^/  /'
openmycelium model verify Mistral-Nemo-Instruct-2407 > "$LOG/verify.log" 2>&1
check "openmycelium model verify" $?
phase p6

# ------------------------------------------------------------ P7 command flow
say "P7  full command flow"
openmycelium model inspect Mistral-Nemo-Instruct-2407 > "$LOG/inspect.log" 2>&1
check "model inspect" $?
openmycelium plan --model Mistral-Nemo-Instruct-2407 \
  --output /opt/om/placement.json > "$LOG/plan.log" 2>&1
check "plan" $?
"$OM/bin/python" "$HARNESS/check_placement.py" /opt/om/placement.json
check "placement is exclusive 181/182" $?

S=$(date +%s)
openmycelium run --model Mistral-Nemo-Instruct-2407 \
  --prompt "Explain cross-vendor GPU inference in one sentence." \
  --max-new-tokens 24 > "$LOG/run.log" 2>&1
RC=$?
E=$(date +%s)
tail -24 "$LOG/run.log" | sed 's/^/  /'
log "run wall clock $(( E - S )) s"
[ "$RC" = "0" ]
check "openmycelium run" $?

printf 'Name one limitation of this approach.\n/bye\n' \
  | openmycelium chat --model Mistral-Nemo-Instruct-2407 \
    --max-new-tokens 24 > "$LOG/chat.log" 2>&1
check "openmycelium chat" $?
tail -10 "$LOG/chat.log" | sed 's/^/  /'
phase p7

printf '\n  %d check(s) failed in phases P0-P7\n' "$FAIL" | tee -a "$LOG/bootstrap.log"
echo "$FAIL" > "$LOG/failcount.txt"
phase end
exit "$FAIL"
