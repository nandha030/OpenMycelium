#!/usr/bin/env bash
# Preserve a release's canonical bytes read-only outside the repository.
#
# `dist/` is gitignored, so a tag naming a wheel SHA-256 names bytes the
# repository cannot check. Rebuilding does not recover them: three builds of one
# commit produce three wheel byte streams at identical size, all installing to
# one content digest. Program content is reproducible; wheel bytes are not. So
# the canonical bytes are copied once and never regenerated.
#
# Refuses to overwrite an existing directory. A preserved artifact is not
# replaced -- that is the whole point of preserving it.
#
# Usage:  preserve_artifacts.sh <version> <manifest-file> [mccl-version]
set -euo pipefail

VERSION=${1:?usage: preserve_artifacts.sh <version> <manifest-file> [mccl-version]}
MANIFEST=${2:?usage: preserve_artifacts.sh <version> <manifest-file> [mccl-version]}
# Named, not globbed. A glob copied openmycelium_mccl-0.2.0a2 alongside the
# pinned 0.2.0a3 once, which puts two transport versions in a directory whose
# entire purpose is to say which one this release was qualified against.
MCCL=${3:-0.2.0a3}

SRC=${OM_DIST:-/mnt/c/Users/User/Documents/Open_Mycelium/dist}
ROOT=${OM_ARTIFACTS:-/mnt/c/Users/User/Documents/OpenMycelium_artifacts}
DST="$ROOT/v$VERSION"

if [ ! -d "$SRC" ]; then
    echo "  no dist directory at $SRC" >&2
    exit 2
fi
if [ -e "$DST" ]; then
    echo "  $DST already exists; preserved artifacts are never replaced" >&2
    exit 3
fi
if [ ! -f "$MANIFEST" ]; then
    echo "  no manifest at $MANIFEST" >&2
    exit 2
fi

wheel="$SRC/openmycelium-$VERSION-py3-none-any.whl"
if [ ! -f "$wheel" ]; then
    echo "  no wheel at $wheel" >&2
    exit 2
fi

mkdir -p "$DST"
cp "$wheel" "$DST/"
for extra in "$SRC/openmycelium_mccl-$MCCL-py3-none-any.whl" \
             "$SRC/openmycelium_mccl-$MCCL.tar.gz"; do
    if [ -f "$extra" ]; then
        cp "$extra" "$DST/"
    else
        echo "  missing pinned transport artifact: $extra" >&2
        exit 2
    fi
done
sed 's/\r$//' "$MANIFEST" > "$DST/MANIFEST.txt"

# Digests computed from the copies, then verified, so a truncated copy is caught
# here rather than discovered when the artifact is next needed.
( cd "$DST" && sha256sum ./*.whl ./*.tar.gz ./MANIFEST.txt > SHA256SUMS )
( cd "$DST" && sha256sum -c SHA256SUMS )

# Best effort only, and not relied on. These live on a Windows drive mounted
# through drvfs, which accepts chmod and does not apply it -- the files come back
# rwxrwxrwx. SHA256SUMS is the real protection: it detects a change rather than
# preventing one, which is what the evidence needs anyway.
chmod -w "$DST"/* 2>/dev/null || true

echo
echo "  preserved in $DST"
echo "  verify later with: cd $DST && sha256sum -c SHA256SUMS"
echo "  (write protection is not enforceable on this mount; the digests are)"
ls -l "$DST"
