#!/usr/bin/env bash
# Do the ghcr and Docker Hub images share layers?
#
# This decides whether switching registries reuses the partial download or
# throws it away. Layers are content-addressed, so identical blobs are reused
# across registries; separately built images are not.
set -uo pipefail
GHCR=ghcr.io/open-webui/open-webui:v0.11.1
HUB=openwebui/open-webui:0.11

layers() {
  docker manifest inspect --verbose "$1" 2>/dev/null \
    | tr -d ' ",' \
    | awk '/architecture:amd64/{amd=1} /digest:sha256:/{if (blob) print $0}
           /layers:/{blob=1} /architecture:/{blob=0}' \
    | sed 's/digest:sha256://' | sort -u
}

echo "  == ghcr layers =="
docker manifest inspect --verbose "$GHCR" > /tmp/ghcr.json 2>/dev/null
python3 - <<'PY'
import json
def load(p):
    try:
        d = json.load(open(p))
    except Exception:
        return []
    entries = d if isinstance(d, list) else [d]
    for e in entries:
        plat = (e.get("Descriptor") or {}).get("platform") or {}
        if plat.get("architecture") == "amd64" and plat.get("os") == "linux":
            man = e.get("SchemaV2Manifest") or e.get("OCIManifest") or {}
            return [l["digest"] for l in man.get("layers", [])]
    return []
g = load("/tmp/ghcr.json")
print(f"    {len(g)} layers")
for d in g[:4]:
    print(f"      {d[:26]}")
json.dump(g, open("/tmp/ghcr_layers.json", "w"))
PY

echo
echo "  == docker hub layers =="
docker manifest inspect --verbose "$HUB" > /tmp/hub.json 2>/dev/null
python3 - <<'PY'
import json
def load(p):
    try:
        d = json.load(open(p))
    except Exception:
        return []
    entries = d if isinstance(d, list) else [d]
    for e in entries:
        plat = (e.get("Descriptor") or {}).get("platform") or {}
        if plat.get("architecture") == "amd64" and plat.get("os") == "linux":
            man = e.get("SchemaV2Manifest") or e.get("OCIManifest") or {}
            return [l["digest"] for l in man.get("layers", [])]
    return []
h = load("/tmp/hub.json")
g = json.load(open("/tmp/ghcr_layers.json"))
print(f"    {len(h)} layers")
for d in h[:4]:
    print(f"      {d[:26]}")
shared = set(g) & set(h)
print()
print(f"    shared layers: {len(shared)} of {len(g)} (ghcr) / {len(h)} (hub)")
if g and h:
    print(f"    identical image: {g == h}")
PY
