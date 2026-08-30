#!/usr/bin/env bash
# Prove the installed-content digest does not depend on who cloned.
#
# 0.3.0a10 was built from a tree mixing CRLF and LF, because `.gitattributes`
# declared no rule for `.py` and the checked-out bytes therefore followed
# `core.autocrlf` -- true for the Windows git on this machine, unset for the WSL
# git. Every file was byte-for-byte the same program, and the digest still named
# a tree no checkout can reproduce.
#
# The `*.py text eol=lf` rule is supposed to remove that dependency. This checks
# it rather than assuming it: clone the same ref twice, once with
# core.autocrlf=true and once with false, build in each, and compare the
# installed-content digest of the two wheels.
#
# Run it against a ref WITHOUT the rule and the two digests differ. That
# counterfactual is the point -- a proof that passes either way proves nothing.
#
# Usage:  clean_clone_digest.sh <ref> [workdir]
set -uo pipefail

REF=${1:?usage: clean_clone_digest.sh <ref> [workdir]}
WORK=${2:-/tmp/om-clean-clone}
REPO=${OM_REPO:-/mnt/c/Users/User/Documents/Open_Mycelium}
PY=${OM_PY:-/opt/hetenv/bin/python3}

rm -rf "$WORK"
mkdir -p "$WORK"

digest_for() {
    local setting=$1 dir="$WORK/autocrlf-$1"
    git -c core.autocrlf="$setting" clone --quiet --no-local \
        --branch "$REF" --single-branch "$REPO" "$dir" 2>/dev/null \
        || git -c core.autocrlf="$setting" clone --quiet --no-local \
               "$REPO" "$dir"
    ( cd "$dir" && git -c advice.detachedHead=false checkout --quiet "$REF" \
        && git -c core.autocrlf="$setting" checkout --quiet -- . )

    # How many .py files carry a CR, so the report says what the setting did
    # rather than only what it produced.
    local carriage
    carriage=$(find "$dir" -name '*.py' -not -path '*/.git/*' \
               -exec grep -lU $'\r' {} + 2>/dev/null | wc -l)

    ( cd "$dir" && "$PY" scripts/build_openmycelium_wheel.py \
        --out "$dir/dist" >/dev/null 2>&1 )
    local wheel
    wheel=$(ls "$dir"/dist/openmycelium-*.whl 2>/dev/null | head -1)
    if [ -z "$wheel" ]; then
        printf '  autocrlf=%-5s BUILD FAILED\n' "$setting"
        return 1
    fi
    local content
    content=$("$PY" - "$wheel" <<'PYEOF'
import hashlib, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    running = hashlib.sha256()
    for name in sorted(n for n in archive.namelist() if n.endswith(".py")):
        running.update(name.encode("utf-8"))
        running.update(archive.read(name))
print(running.hexdigest())
PYEOF
)
    printf '  autocrlf=%-5s  %s py files with CR  content %s\n' \
        "$setting" "$carriage" "$content"
    echo "$content" > "$WORK/digest-$setting.txt"
}

echo
echo "  ref: $REF"
echo
digest_for true
digest_for false
echo

a=$(cat "$WORK/digest-true.txt" 2>/dev/null)
b=$(cat "$WORK/digest-false.txt" 2>/dev/null)
if [ -z "$a" ] || [ -z "$b" ]; then
    echo "  == inconclusive: a build did not produce a wheel =="
    exit 1
fi
if [ "$a" = "$b" ]; then
    echo "  == the digest does not depend on core.autocrlf =="
    exit 0
fi
echo "  == the digest DEPENDS on core.autocrlf =="
echo "     true  $a"
echo "     false $b"
exit 1
