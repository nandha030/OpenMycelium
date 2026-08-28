#!/usr/bin/env bash
# Prove the wheelhouse can build an environment with the network refused.
#
# Until now the wheelhouse was only collected and hashed. Collecting files is
# not the same as being able to install from them, and saying otherwise would be
# the sort of claim this project keeps refusing to make.
#
# --no-index removes PyPI and the vendor index entirely, so anything missing
# from the wheelhouse is an error rather than a silent download.
set -uo pipefail
WH=/mnt/c/Users/User/Documents/Open_Mycelium/release/wheelhouse
SCRATCH=/opt/wh-test
LOG=/var/log/om-a8
FAIL=0
mkdir -p "$LOG"
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

echo "  == verify the wheelhouse before using it =="
for v in cuda rocm; do
  ( cd "$WH/$v" && sha256sum -c SHA256SUMS ) > "$LOG/wh-$v.txt" 2>&1
  bad=$(grep -c FAILED "$LOG/wh-$v.txt"); ok=$(grep -c ': OK' "$LOG/wh-$v.txt")
  printf '    %-5s %s verified, %s failed\n' "$v" "$ok" "$bad"
  [ "$bad" = "0" ]; check "$v wheelhouse intact" $?
done

echo
echo "  == install the ROCm environment offline =="
rm -rf "$SCRATCH"
python3 -m venv "$SCRATCH" > "$LOG/wh-install.log" 2>&1
S=$(date +%s)
"$SCRATCH/bin/pip" install -q --no-index --find-links "$WH/rocm" \
  torch==2.10.0 transformers==5.15.1 tokenizers==0.22.2 safetensors numpy \
  >> "$LOG/wh-install.log" 2>&1
RC=$?
E=$(date +%s)
printf '    took %s min %s s, no network\n' "$(( (E-S)/60 ))" "$(( (E-S)%60 ))"
[ "$RC" = "0" ]
check "pip installed the whole set with --no-index" $?
if [ "$RC" != "0" ]; then tail -6 "$LOG/wh-install.log" | sed 's/^/      /'; fi

if [ "$RC" = "0" ]; then
  "$SCRATCH/bin/python" -c "
import torch, transformers
print(f'    torch        {torch.__version__}')
print(f'    transformers {transformers.__version__}')
import sys
sys.exit(0 if torch.__version__ == '2.10.0+rocm7.0' else 1)" 2>/dev/null
  check "the offline-installed torch is the pinned ROCm build" $?
  n=$("$SCRATCH/bin/pip" list --format=freeze | wc -l)
  printf '    %s packages installed from local files\n' "$n"
fi

echo
echo "  == clean up =="
rm -rf "$SCRATCH"
check "scratch environment removed" $?

printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
