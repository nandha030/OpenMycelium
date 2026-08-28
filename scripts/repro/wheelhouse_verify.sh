#!/usr/bin/env bash
# Verify the wheelhouse independently of the process that produced it.
#
# Re-reads every file, re-checks its SHA-256 against both the SHA256SUMS written
# at collection time and the hashes recorded in LOCK.json, and confirms the file
# count matches the number of packages actually installed in each environment.
set -uo pipefail
W=/mnt/c/Users/User/Documents/Open_Mycelium/release/wheelhouse
LOG=/var/log/om-bootstrap
FAIL=0
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

for v in cuda rocm; do
  echo "  == $v =="
  ( cd "$W/$v" && sha256sum -c SHA256SUMS > /tmp/verify-$v.txt 2>&1 )
  bad=$(grep -c "FAILED" /tmp/verify-$v.txt)
  ok=$(grep -c ": OK" /tmp/verify-$v.txt)
  echo "    $ok verified, $bad failed"
  [ "$bad" = "0" ]
  check "$v wheelhouse re-verifies against SHA256SUMS" $?

  installed=$(wc -l < "$LOG/packages-$v.txt" 2>/dev/null || echo 0)
  files=$(ls "$W/$v" | grep -cE '\.whl$|\.tar\.gz$')
  echo "    $files files collected, $installed packages installed"
  [ "$files" = "$installed" ]
  check "$v wheelhouse covers every installed package" $?
done

echo
echo "  == LOCK.json agrees with the files on disk =="
python3 - "$W" <<'PY'
import hashlib, json, os, sys
w = sys.argv[1]
lock = json.load(open(os.path.join(w, "LOCK.json")))
problems = 0
for vendor, data in lock["environments"].items():
    for entry in data["files"]:
        path = os.path.join(w, vendor, entry["file"])
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        if digest.hexdigest() != entry["sha256"]:
            print(f"    MISMATCH {vendor}/{entry['file']}")
            problems += 1
    print(f"    {vendor:<5} {data['fileCount']} entries, "
          f"{data['totalBytes'] / (1 << 30):.2f} GiB, index {data['index']}")
print(f"    {problems} mismatch(es)")
sys.exit(1 if problems else 0)
PY
check "every LOCK.json hash matches the file on disk" $?

printf '\n  %d check(s) failed\n' "$FAIL"
exit "$FAIL"
