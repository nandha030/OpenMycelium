#!/usr/bin/env bash
# Move the prematurely staged 0.1.0a7 aside as candidate-1, with its hashes and
# the evidence produced against it, then leave release/0.1.0a7 empty for the
# rebuild. Nothing is deleted.
set -uo pipefail
R=/mnt/c/Users/User/Documents/Open_Mycelium/release
SRC=$R/0.1.0a7
DST=$R/0.1.0a7-candidate1

if [ -d "$DST" ]; then
  echo "  $DST already exists; refusing to overwrite"
  exit 1
fi
mkdir -p "$DST"
mv "$SRC"/* "$DST"/ 2>/dev/null
rmdir "$SRC" 2>/dev/null
mkdir -p "$SRC"

cat > "$DST/SUPERSEDED.txt" <<'EOF'
0.1.0a7 candidate-1 -- superseded, never approved, never published.

Staged and hashed before validation ran, which was premature: the rule is to
freeze only after all results pass. Validation then found that reinstall
detection never fired, because a working ROCm environment short-circuits as
"already usable" before the prerequisite block runs, so no qualified HSA runtime
was recorded and a later regression read as "never set up".

Kept because the evidence under validation/ was produced against these exact
bytes: steps 1 through 8 of the validation path all passed on this build. Only
the ledger recording in provision.py differs in the rebuilt 0.1.0a7.

  wheel    eb8f44e696386f4bdbef32bcd35b129c6598c4599e50be7643c8263eac5698f8
  content  2fbc932050edab6a182be929b816d9eaa357c75d97a6f43056eb03a4a327d6ea
EOF

echo "  moved to $DST:"
ls -1 "$DST" | sed 's/^/    /'
echo
echo "  $SRC is now empty and ready for the rebuild"
