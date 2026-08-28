#!/usr/bin/env bash
# Turn the artifacts this run downloaded into a verified wheelhouse and a lock.
#
# Run inside the distribution that just provisioned, while pip's HTTP cache is
# still warm, so nothing is fetched a second time. The output serves two
# purposes: a 0.1.0a7 distribution can install from it without another 96-minute
# download, and the SHA-256 of every file becomes the lock that stops a later
# PyPI change from silently altering an install.
#
# The distinction this preserves: environment creation stays clean, network
# acquisition is cached. Those are separate claims and the manifest says so.
set -uo pipefail
DEST=${1:-/mnt/c/Users/User/Documents/Open_Mycelium/release/wheelhouse}
CUDA_INDEX=https://download.pytorch.org/whl/cu128
ROCM_INDEX=https://download.pytorch.org/whl/rocm7.0
CUDA_ENV=/var/lib/openmycelium/state/env/cuda
ROCM_ENV=/var/lib/openmycelium/state/env/rocm

mkdir -p "$DEST/cuda" "$DEST/rocm"

fetch() {                 # env  index  torchpin  outdir
  local env=$1 index=$2 pin=$3 out=$4
  echo "  == $out =="
  "$env/bin/pip" download --quiet --dest "$out" --index-url "$index" "$pin" \
    2>&1 | tail -3 | sed 's/^/    /'
  echo "    torch from $index: $(ls "$out" | grep -c '^torch-') file(s)"
  "$env/bin/pip" download --quiet --dest "$out" \
    transformers==5.15.1 tokenizers==0.22.2 safetensors numpy \
    2>&1 | tail -3 | sed 's/^/    /'
  echo "    total files: $(ls "$out" | wc -l)"
}

fetch "$CUDA_ENV" "$CUDA_INDEX" "torch==2.11.0" "$DEST/cuda"
fetch "$ROCM_ENV" "$ROCM_INDEX" "torch==2.10.0" "$DEST/rocm"

echo
echo "  == hashing =="
for v in cuda rocm; do
  ( cd "$DEST/$v" && sha256sum ./*.whl ./*.tar.gz 2>/dev/null > SHA256SUMS )
  n=$(wc -l < "$DEST/$v/SHA256SUMS")
  size=$(du -sh "$DEST/$v" | cut -f1)
  echo "    $v  $n files, $size"
done

echo
echo "  == lock =="
"$CUDA_ENV/bin/python" - "$DEST" <<'PY'
import hashlib, json, os, sys

dest = sys.argv[1]
lock = {"generatedBy": "scripts/repro/wheelhouse.sh",
        "acquisition": "online, from the pinned vendor indexes and PyPI",
        "note": ("A 0.1.0a7 clean bootstrap may install from this wheelhouse. "
                 "Environment creation is still from zero; only network "
                 "acquisition is cached. These are separate claims."),
        "environments": {}}
for vendor in ("cuda", "rocm"):
    entries = []
    directory = os.path.join(dest, vendor)
    for name in sorted(os.listdir(directory)):
        if not (name.endswith(".whl") or name.endswith(".tar.gz")):
            continue
        path = os.path.join(directory, name)
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        stem = name.split("-")
        entries.append({"file": name, "name": stem[0],
                        "version": stem[1] if len(stem) > 1 else "",
                        "bytes": os.path.getsize(path),
                        "sha256": digest.hexdigest()})
    lock["environments"][vendor] = {
        "index": ("https://download.pytorch.org/whl/cu128" if vendor == "cuda"
                  else "https://download.pytorch.org/whl/rocm7.0"),
        "fileCount": len(entries),
        "totalBytes": sum(e["bytes"] for e in entries),
        "files": entries,
    }
out = os.path.join(dest, "LOCK.json")
with open(out, "w", encoding="utf-8") as handle:
    json.dump(lock, handle, indent=2, sort_keys=True)
for vendor, data in lock["environments"].items():
    print(f"    {vendor}  {data['fileCount']} files  "
          f"{data['totalBytes'] / (1 << 30):.2f} GiB")
print(f"    wrote {out}")
PY
