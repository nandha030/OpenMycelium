#!/usr/bin/env bash
# Assemble release/0.2.0a5 evidence: hashes, pin, installed content, and the
# runtime tuple. Hashes and identities are collected here; the narrative lives
# in GATE.md beside them.
set -uo pipefail
# Repository root, derived rather than hard-coded. These scripts are sometimes
# copied elsewhere before running (line-ending fixes), so BASH_SOURCE alone is
# not enough: fall back to OM_REPO, and fail loudly rather than guess.
_here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)
REPO=${OM_REPO:-$(cd "$_here/../.." 2>/dev/null && pwd)}
export OM_DIST="${OM_DIST:-$REPO/dist}"
if [ ! -f "$REPO/packaging/launcher.py" ]; then
  echo "  cannot locate the repository root; set OM_REPO to the checkout" >&2
  exit 78
fi
OUT=$REPO/release/0.2.0a5
OM=/opt/om/venv
mkdir -p "$OUT"

echo "  == artifact hashes =="
cd "$REPO/dist" || exit 1
sha256sum openmycelium-0.2.0a5-py3-none-any.whl \
          openmycelium_mccl-0.2.0a3-py3-none-any.whl \
          openmycelium_mccl-0.2.0a3.tar.gz > "$OUT/SHA256SUMS.frozen"
sed 's/^/    /' "$OUT/SHA256SUMS.frozen"

echo
echo "  == stage the artifacts (never overwriting) =="
for w in openmycelium-0.2.0a5-py3-none-any.whl \
         openmycelium_mccl-0.2.0a3-py3-none-any.whl \
         openmycelium_mccl-0.2.0a3.tar.gz; do
  if [ -e "$OUT/$w" ]; then echo "    refusing to overwrite $w"
  else cp "$REPO/dist/$w" "$OUT/$w" && echo "    staged $w"; fi
done

echo
echo "  == dependency pin, read from the wheel's own metadata =="
"$OM/bin/python" - "$OUT" <<'PY'
import glob, json, os, re, sys, zipfile
path = glob.glob(os.environ["OM_DIST"] + "/openmycelium-0.2.0a5-*.whl")[0]
z = zipfile.ZipFile(path)
text = z.read([n for n in z.namelist() if n.endswith("METADATA")][0]).decode()
requires = [l.split(": ", 1)[1] for l in text.splitlines() if l.startswith("Requires-Dist")]
record = {"version": re.search(r"^Version: (.+)$", text, re.M).group(1),
          "requiresDist": requires,
          "pinVerified": any("openmycelium-mccl==0.2.0a3" in r.replace("_", "-")
                             for r in requires)}
print(f"    version        {record['version']}")
print(f"    requires       {record['requiresDist']}")
print(f"    pin verified   {record['pinVerified']}")
json.dump(record, open(f"{sys.argv[1]}/dependency-pin.json", "w"), indent=2)
PY

echo
echo "  == installed content, from the qualified environment =="
cd /root || exit 1
"$OM/bin/openmycelium" version --json > "$OUT/provenance.json" 2>/dev/null
"$OM/bin/python" - "$OUT" <<'PY'
import json, sys
r = json.load(open(f"{sys.argv[1]}/provenance.json"))
for k in ("openmycelium", "installedContentSha256", "pythonFiles", "mcclVersion",
          "transport", "mcclActivationProtocol", "eventSchemaVersion",
          "placementSchemaVersion"):
    print(f"    {k:<26} {r.get(k)}")
h = r.get("hsaRuntime") or {}
print(f"    {'hsaRuntime':<26} {h.get('flavor')} / {h.get('source')} / {str(h.get('sha256'))[:16]}")
PY

echo
echo "  == runtime and hardware tuple =="
"$OM/bin/python" - "$OUT" <<'PY'
import json, os, subprocess, sys

def probe(python, script):
    out = subprocess.run([python, "-c", script], capture_output=True, text=True,
                         timeout=300)
    for line in reversed(out.stdout.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    return {}

S = ("import json,torch;ok=torch.cuda.is_available();"
     "print(json.dumps({'name':torch.cuda.get_device_name(0) if ok else '',"
     "'torch':torch.__version__,'totalBytes':(torch.cuda.mem_get_info(0)[1] if ok else 0)}))")

tuple_ = {}
for vendor, env in (("cuda", "/var/lib/openmycelium/state/env/cuda"),
                    ("rocm", "/var/lib/openmycelium/state/env/rocm")):
    info = probe(f"{env}/bin/python", S)
    tuple_[vendor] = info
    print(f"    {vendor:<5} {info.get('name'):<28} torch {info.get('torch')}")
tuple_["kernel"] = os.uname().release
tuple_["distribution"] = open("/etc/os-release").read().split('PRETTY_NAME="')[1].split('"')[0]
print(f"    kernel {tuple_['kernel']}")
print(f"    distro {tuple_['distribution']}")
json.dump(tuple_, open(f"{sys.argv[1]}/runtime-tuple.json", "w"), indent=2)
PY

echo
echo "  == placement summary (identities as placeholders) =="
"$OM/bin/openmycelium" plan --model Mistral-Nemo-Instruct-2407 \
  --output /tmp/a5-plan.json > /dev/null 2>&1
"$OM/bin/python" - "$OUT" <<'PY'
import json, re, sys
m = json.load(open("/tmp/a5-plan.json"))
stages = m["stages"]
def anon(value, vendor):
    return f"{vendor}:device-0" if value else value
summary = {
    "boundaryAfterLayer": m["pipeline"]["boundaryAfterLayer"],
    "transport": m["pipeline"]["transport"],
    "activationBytesPerToken": m["pipeline"]["activationBytesPerToken"],
    "tensorCounts": sorted(len(s["tensors"]) for s in stages),
    "totalTensors": sum(len(s["tensors"]) for s in stages),
    "overlap": len(set(stages[0]["tensors"]) & set(stages[1]["tensors"])),
    "stages": [{"role": s["role"], "runtime": s["runtime"],
                "layers": [s["layers"][0], s["layers"][-1]],
                "weightBytes": s["weightBytes"], "budgetBytes": s["budgetBytes"],
                "deviceIdentity": anon(s.get("deviceIdentity"),
                                       "nvidia" if s["runtime"] == "cuda" else "amd")}
               for s in stages],
}
print(f"    boundary after layer {summary['boundaryAfterLayer']}, "
      f"{summary['tensorCounts']} tensors, overlap {summary['overlap']}")
json.dump(summary, open(f"{sys.argv[1]}/placement-summary.json", "w"), indent=2)
PY

echo
echo "  == idle state =="
n=$(pgrep -fc pipeline_run 2>/dev/null); n=${n:-0}
echo "    worker processes: $n"
nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null | sed 's/^/    CUDA: /'

echo
ls -1 "$OUT" | sed 's/^/    /'
