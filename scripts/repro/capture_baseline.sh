#!/usr/bin/env bash
# Capture the pre-refactor baseline at the merged commit, on real hardware.
#
# Everything the Adapter SDK refactor must reproduce, measured now rather than
# quoted from an earlier gate: comparing against a run from minutes ago is
# comparing like with like, while comparing across a merge is not.
#
# Written to /var/log/om-baseline inside the distribution, deliberately outside
# tracked release evidence. It is preserved alongside the comparison only once
# the refactor passes.
set -uo pipefail
OM=/opt/om/venv
OUT=/var/log/om-baseline
MODEL=Mistral-Nemo-Instruct-2407
H=${OM_HARNESS:-/root/repro}
export PATH="$OM/bin:$PATH"
unset PYTHONPATH
mkdir -p "$OUT"
cd /root || exit 1

echo "  == environment =="
{
  echo "capturedAt $(date -Is)"
  echo "bootId $(cat /proc/sys/kernel/random/boot_id)"
  echo "uptimeSeconds $(cut -d. -f1 /proc/uptime)"
  echo "distro $(. /etc/os-release && echo "$PRETTY_NAME")"
  echo "kernel $(uname -r)"
} | tee "$OUT/environment.txt" | sed 's/^/    /'

openmycelium version --json > "$OUT/version.json" 2>/dev/null
"$OM/bin/python" - "$OUT" <<'PY'
import json, sys
r = json.load(open(f"{sys.argv[1]}/version.json"))
for k in ("openmycelium", "installedContentSha256", "pythonFiles", "mcclVersion",
          "mcclActivationProtocol", "transport", "eventSchemaVersion",
          "placementSchemaVersion"):
    print(f"    {k:<26} {r.get(k)}")
h = r.get("hsaRuntime") or {}
print(f"    {'hsaRuntime':<26} {h.get('flavor')} {h.get('source')} {str(h.get('sha256'))[:16]}")
PY

echo
echo "  == hardware identities =="
openmycelium fabric list --json > "$OUT/fabric.json" 2>/dev/null
"$OM/bin/python" - "$OUT" <<'PY'
import json, sys
f = json.load(open(f"{sys.argv[1]}/fabric.json"))
report = f.get("fabric", f)
for d in report.get("devices", []):
    print(f"    {d.get('vendor'):<7} {str(d.get('name'))[:28]:<30} {d.get('identity')}")
    print(f"    {'':<7} torch {d.get('torch')}  {d.get('identitySource')}/{d.get('identityConfidence')}")
PY

echo
echo "  == placement: digest, fingerprint, ownership =="
openmycelium plan --model "$MODEL" --output "$OUT/placement.json" > /dev/null 2>&1
"$OM/bin/python" - "$OUT" <<'PY'
import json, sys
m = json.load(open(f"{sys.argv[1]}/placement.json"))
stages = m["stages"]
names = [set(s["tensors"]) for s in stages]
print(f"    placementId        {m.get('placementId')}")
print(f"    manifestDigest     {m.get('manifestDigest')}")
print(f"    modelFingerprint   {m.get('model', {}).get('fingerprint') or m.get('modelFingerprint')}")
print(f"    schemaVersion      {m.get('schemaVersion')}")
print(f"    boundaryAfterLayer {m['pipeline']['boundaryAfterLayer']}")
print(f"    activationBytes    {m['pipeline']['activationBytesPerToken']} per token")
print(f"    transport          {m['pipeline']['transport']}")
print(f"    tensorCounts       {sorted(len(s['tensors']) for s in stages)}")
print(f"    overlap            {len(names[0] & names[1])}")
for s in stages:
    print(f"    {s['role']:<5} layers {s['layers'][0]}-{s['layers'][-1]}  "
          f"{len(s['tensors'])} tensors  {s['weightBytes']/(1<<30):.3f} GiB  "
          f"{s['deviceIdentity']}")
print(f"    manifestTopLevelKeys {sorted(m)}")
PY

echo
echo "  == boundary byte digest (qualification pass) =="
bash "$H/boundary_exact_clean.sh" baseline-boundary 2>&1 | tee "$OUT/boundary.txt" | sed 's/^/    /'

echo
echo "  == VRAM before the run =="
nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null | sed 's/^/    CUDA before: /' | tee "$OUT/vram-before.txt"

echo
echo "  == one-shot run: greedy tokens, text, TTFT, decode =="
openmycelium run --model "$MODEL" \
  --prompt "Explain cross-vendor GPU inference in one sentence." \
  --max-new-tokens 24 --json > "$OUT/run.json" 2> "$OUT/run.err"
RC=$?
echo "    exit code $RC"
"$OM/bin/python" - "$OUT" <<'PY'
import json, sys
raw = open(f"{sys.argv[1]}/run.json").read()
start = raw.find("{")
d = json.loads(raw[start:]) if start >= 0 else {}
r = d.get("result") or {}
tokens = r.get("generatedTokens") or []
print(f"    generatedTokenCount {len(tokens)}")
print(f"    generatedTokenIds   {tokens}")
print(f"    text                {(r.get('text') or '')!r}")
print(f"    ttftMs              {r.get('ttftMs')}")
print(f"    decodeTokensPerSec  {r.get('decodeTokensPerSecond')}")
print(f"    interTokenMs        {r.get('interTokenMs')}")
print(f"    stopReason          {r.get('stopReason')}")
h = d.get("health") or {}
print(f"    health              {h.get('state')}")
json.dump({"generatedTokens": tokens, "text": r.get("text"),
           "ttftMs": r.get("ttftMs"),
           "decodeTokensPerSecond": r.get("decodeTokensPerSecond"),
           "interTokenMs": r.get("interTokenMs")},
          open(f"{sys.argv[1]}/run-summary.json", "w"), indent=2)
PY

echo
echo "  == VRAM and workers after =="
sleep 5
{
  echo "orphanWorkers $(pgrep -fc pipeline_run 2>/dev/null || echo 0)"
  nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader 2>/dev/null \
    | sed 's/^/cudaAfter /'
} | tee "$OUT/after.txt" | sed 's/^/    /'
"$OM/bin/python" - <<'PY'
import json, subprocess
env = "/var/lib/openmycelium/state/env/rocm/bin/python"
S = ("import json,torch;ok=torch.cuda.is_available();"
     "f,t=(torch.cuda.mem_get_info(0) if ok else (0,0));"
     "print(json.dumps({'usedMiB':(t-f)//(1<<20),'totalMiB':t//(1<<20)}))")
out = subprocess.run([env, "-c", S], capture_output=True, text=True, timeout=300)
for line in reversed(out.stdout.splitlines()):
    if line.startswith("{"):
        d = json.loads(line)
        print(f"    rocmAfter {d['usedMiB']} MiB of {d['totalMiB']}")
        break
PY

echo
echo "  == the gate, unchanged =="
bash "$H/console_wheel_gate.sh" 2>&1 | tee "$OUT/gate.txt" | grep -E '\[PASS\]|\[FAIL\]|TTFT|check\(s\)' | sed 's/^/    /'

echo
echo "  baseline written to $OUT"
ls -1 "$OUT" | sed 's/^/    /'
