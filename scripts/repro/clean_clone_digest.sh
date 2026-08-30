#!/usr/bin/env bash
# Prove the shipped Python bytes do not depend on who cloned.
#
# 0.3.0a10 was built from a tree mixing CRLF and LF, because `.gitattributes`
# declared no rule for `.py` and the checked-out bytes therefore followed
# `core.autocrlf` -- true for the Windows git on this machine, unset for the WSL
# git. Every file was byte-for-byte the same program, and the digest still named
# a tree no checkout can reproduce.
#
# The property under test is about the checkout, not the build: are the shipped
# `.py` bytes identical whichever way the tree was written? So the digest is
# computed from the checked-out files directly. Going through the build would
# drag in the dirty-tree guard, which -- correctly -- refuses to build an
# `autocrlf=true` clone because the *non*-Python text files are still affected.
# That is a real finding and it is reported below, but it is not this property.
#
# Run against a ref WITHOUT the `*.py text eol=lf` rule and the two digests
# differ. That counterfactual is the point: a proof that passes either way
# proves nothing.
#
# Usage:  clean_clone_digest.sh <ref> [workdir]
set -uo pipefail

REF=${1:?usage: clean_clone_digest.sh <ref> [workdir]}
WORK=${2:-/tmp/om-clean-clone}
REPO=${OM_REPO:-/mnt/c/Users/User/Documents/Open_Mycelium}

rm -rf "$WORK"
mkdir -p "$WORK"

# The subtrees the wheel ships, mirroring INCLUDE in the build script, plus the
# launcher. These are the files whose bytes become installedContentSha256.
SHIPPED="runtime/cli runtime/serving runtime/scheduler runtime/fabric runtime/safety packaging/launcher.py"

measure() {
    local setting=$1 dir="$WORK/autocrlf-$1"

    # --force on both checkouts, and the result is verified below rather than
    # assumed. Without it the branch checkout aborts: an autocrlf=true clone
    # dirties every text file the moment it lands, so git refuses to switch ref
    # "because local changes would be overwritten". That abort left the clone on
    # the wrong ref and the counterfactual silently measured the branch it was
    # meant to contrast with -- a vacuous pass.
    # Clone the ref directly, so no ref switch is needed at all, and force the
    # re-checkout anyway. Both matter: without --branch the clone lands on the
    # source repo's current branch, and the switch to $REF then aborts, because
    # an autocrlf=true clone dirties every text file the moment it lands and git
    # refuses to overwrite "local changes". That abort left the clone on the
    # wrong ref and the counterfactual silently measured the branch it was meant
    # to contrast with -- a vacuous pass that looked like a real one.
    git -c core.autocrlf="$setting" clone --quiet --no-local \
        --branch "$REF" --single-branch "$REPO" "$dir" >/dev/null 2>&1
    ( cd "$dir" \
      && git -c advice.detachedHead=false -c core.autocrlf="$setting" \
             checkout --quiet --force "$REF" \
      && rm -rf ./* >/dev/null 2>&1 \
      && git -c core.autocrlf="$setting" checkout --quiet --force -- . )

    # Prove the clone is on the ref that was asked for. A measurement of the
    # wrong tree is worse than no measurement.
    local on
    on=$( cd "$dir" && git rev-parse HEAD 2>/dev/null )
    local want
    want=$( cd "$REPO" && git rev-parse "$REF" 2>/dev/null )
    if [ "$on" != "$want" ]; then
        printf '  autocrlf=%-5s  WRONG REF: on %s, wanted %s (%s)\n' \
            "$setting" "${on:0:12}" "${want:0:12}" "$REF"
        return 1
    fi

    # Digest over the shipped Python, sorted by path: name then bytes, the same
    # shape provenance uses.
    local content
    content=$( cd "$dir" && find $SHIPPED -name '*.py' -not -path '*/__pycache__/*' \
               2>/dev/null | LC_ALL=C sort | while read -r f; do
                   printf '%s' "$f"; cat "$f"
               done | sha256sum | cut -d' ' -f1 )

    local pyfiles pycr othercr dirty
    pyfiles=$( cd "$dir" && find $SHIPPED -name '*.py' 2>/dev/null | wc -l )
    pycr=$( cd "$dir" && find $SHIPPED -name '*.py' -exec grep -lU $'\r' {} + \
            2>/dev/null | wc -l )
    othercr=$( cd "$dir" && git status --porcelain --untracked-files=no \
               | grep -cv '\.py$' )
    dirty=$( cd "$dir" && git status --porcelain --untracked-files=no \
             | grep -c '\.py$' )

    printf '  autocrlf=%-5s  %3s shipped .py, %s with CR, %s dirty .py, %s dirty non-.py\n' \
        "$setting" "$pyfiles" "$pycr" "$dirty" "$othercr"
    printf '                 content %s\n' "$content"
    echo "$content" > "$WORK/digest-$setting.txt"
    echo "$othercr"  > "$WORK/othercr-$setting.txt"
}

echo
echo "  ref: $REF"
echo
measure true
measure false
echo

a=$(cat "$WORK/digest-true.txt" 2>/dev/null)
b=$(cat "$WORK/digest-false.txt" 2>/dev/null)
if [ -z "$a" ] || [ -z "$b" ]; then
    echo "  == inconclusive: a clone produced no digest =="
    exit 1
fi

if [ "$a" != "$b" ]; then
    echo "  == the shipped Python DEPENDS on core.autocrlf =="
    echo "     true  $a"
    echo "     false $b"
    exit 1
fi

echo "  == the shipped Python does not depend on core.autocrlf =="
other=$(cat "$WORK/othercr-true.txt" 2>/dev/null)
if [ "${other:-0}" != "0" ]; then
    echo
    echo "  Still outstanding, and not what this rule set out to fix:"
    echo "  $other non-Python text file(s) differ from the index in the"
    echo "  autocrlf=true clone -- .c, .sh, .js, .html, .css, .md and friends."
    echo "  installedContentSha256 covers .py only, so the artifact identity is"
    echo "  now cloner-independent; but the dirty-tree guard refuses to build"
    echo "  such a clone at all, so a default Windows clone cannot produce a"
    echo "  releasable wheel until those types are declared too."
fi
exit 0
