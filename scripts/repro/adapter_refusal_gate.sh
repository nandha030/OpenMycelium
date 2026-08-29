#!/usr/bin/env bash
# The two refusals that this milestone exists to produce, on real hardware.
#
# Both are measured, not asserted from the code path. A refusal that happens
# after a device context is created still leaves VRAM resident and a process to
# reap, and reading the source cannot tell you which side of that line it fell
# on. So each test samples VRAM before, runs the refusal, waits for the process
# to be gone, and samples again.
#
# Test 1 -- planning path. A structurally complete Llama checkpoint. No worker
#   is spawned at all, so "no worker" is the claim and it is checkable.
#
# Test 2 -- direct-worker path. The real frozen v1 Mistral manifest, invoked the
#   way scripts/repro/boundary_exact_clean.sh invokes a worker. A worker process
#   NECESSARILY exists here -- it was started deliberately -- so the claim is
#   narrower: it exits before stage construction, leaves no orphan, and returns
#   VRAM to where it was.
#
# Run inside the qualified distribution, against the INSTALLED wheel.
set -uo pipefail

OM=${OM_VENV:-/opt/om/venv}
# Building the fixture needs torch, which lives in the provisioned CUDA
# environment, not in the CLI's own virtualenv. Only the fixture uses it; the
# refusals themselves must come from the installed wheel.
TORCH_PY=${OM_TORCH_PYTHON:-/var/lib/openmycelium/state/env/cuda/bin/python}
OUT=${OM_OUT:-/var/log/om-adapter-gate}
# Deliberately NOT under a model store. Writing the fixture into /opt/models
# created that directory, config discovery then preferred it over the real
# store, and `run --model Mistral-Nemo-Instruct-2407` stopped resolving. The
# fixture is addressed by full path and must not join any store.
FIXTURE=${OM_FIXTURE:-/var/lib/om-fixtures/tiny-llama-fixture}
MODEL=${OM_MODEL:-/var/lib/openmycelium/models/Mistral-Nemo-Instruct-2407}
H=${OM_HARNESS:-/root/repro}
LEGACY=${OM_LEGACY_MANIFEST:-$H/frozen-0.1.0a5-placement.json}

export PATH="$OM/bin:$PATH"
unset PYTHONPATH            # wheels only; never the checkout
mkdir -p "$OUT"
FAILURES=0

vram() { nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1; }
workers() { pgrep -fc "pipeline_run|forward_pass" 2>/dev/null || true; }

note() { printf '    %s\n' "$*"; }
fail() { printf '    FAIL  %s\n' "$*"; FAILURES=$((FAILURES + 1)); }
pass() { printf '    ok    %s\n' "$*"; }

# This machine also runs a console and a display, so reported VRAM drifts by a
# megabyte or two between samples with nothing of ours running. The claim being
# tested is that no *stage* was allocated, and a stage of this model is gigabytes
# -- so the tolerance is small enough to catch anything that matters and large
# enough not to fail on unrelated noise. The raw numbers are printed either way.
VRAM_TOLERANCE_MIB=${OM_VRAM_TOLERANCE:-32}
vram_returned() {
  local before=$1
  local after=$2
  local delta=$((after - before))
  [ "$delta" -lt 0 ] && delta=$((-delta))
  [ "$delta" -le "$VRAM_TOLERANCE_MIB" ]
}

# `pgrep -c` prints 0 and exits non-zero when nothing matches, so `|| echo 0`
# would append a second line. `|| true` keeps the single "0" it already printed.
normalise() { local n; n=$(workers); echo "${n:-0}"; }

echo "  == adapter refusal gate =="
{
  echo "ranAt $(date -Is)"
  echo "bootId $(cat /proc/sys/kernel/random/boot_id)"
  echo "openmycelium $("$OM/bin/openmycelium" version --json 2>/dev/null | tr -d '\n')"
} > "$OUT/environment.txt"
sed 's/^/    /' "$OUT/environment.txt" | cut -c1-160

# ---------------------------------------------------------------- fixture
if [ ! -f "$FIXTURE/config.json" ]; then
  note "building the Llama fixture (structurally valid, unsupported architecture)"
  "$TORCH_PY" "$H/make_llama_fixture.py" "$FIXTURE" | sed 's/^/    /'
fi
note "fixture architecture: $("$OM/bin/python" -c "import json,sys;print(json.load(open(sys.argv[1]))['architectures'])" "$FIXTURE/config.json")"

echo
echo "  -- test 1: planning path, before any allocation --"
V0=$(vram); W0=$(normalise)
note "VRAM before ${V0} MiB, matching worker processes ${W0}"

"$OM/bin/openmycelium" plan --model "$FIXTURE" --json > "$OUT/plan.json" 2> "$OUT/plan.err"
CODE=$?
sleep 1
V1=$(vram); W1=$(normalise)

cat "$OUT/plan.json" "$OUT/plan.err" > "$OUT/plan.all" 2>/dev/null
note "exit ${CODE}"
grep -o 'UNSUPPORTED_ARCHITECTURE\|INVALID_CHECKPOINT\|ADAPTER_[A-Z_]*' "$OUT/plan.all" \
  | sort -u | sed 's/^/    saw code: /'

[ "$CODE" -ne 0 ] && pass "planning refused (exit ${CODE})" || fail "planning did not refuse"
if grep -q 'UNSUPPORTED_ARCHITECTURE' "$OUT/plan.all"; then
  pass "refused as UNSUPPORTED_ARCHITECTURE"
else
  fail "expected UNSUPPORTED_ARCHITECTURE in the output"
fi
if grep -q 'INVALID_CHECKPOINT' "$OUT/plan.all"; then
  fail "refused as INVALID_CHECKPOINT -- the fixture is valid; wrong reason"
else
  pass "not INVALID_CHECKPOINT: the architecture is what was rejected"
fi
grep -qi 'LlamaForCausalLM' "$OUT/plan.all" \
  && pass "message names the architecture found" \
  || fail "message does not name LlamaForCausalLM"
grep -qi 'MistralForCausalLM' "$OUT/plan.all" \
  && pass "message names what is installed" \
  || fail "message does not name the installed architectures"

[ "$W1" = "$W0" ] && pass "no worker spawned (${W0} -> ${W1})" \
                  || fail "worker count changed ${W0} -> ${W1}"
if vram_returned "$V0" "$V1"; then
  pass "VRAM unchanged within ${VRAM_TOLERANCE_MIB} MiB (${V0} -> ${V1} MiB)"
else
  fail "VRAM moved ${V0} -> ${V1} MiB; something was allocated"
fi
[ -s "$OUT/plan.json" ] && grep -q '"stages"' "$OUT/plan.json" \
  && fail "a placement was produced for an unsupported architecture" \
  || pass "no placement was written"

echo
echo "  -- test 2: direct-worker path, before stage construction --"
if [ ! -f "$LEGACY" ]; then
  fail "the frozen v1 manifest is missing: $LEGACY"
  echo
  echo "  == $FAILURES failure(s) =="
  exit 1
fi
"$OM/bin/python" -c "import json,sys;m=json.load(open(sys.argv[1]));print(f\"    manifest schemaVersion {m['schemaVersion']}, adapterId {m.get('adapterId')!r}\")" "$LEGACY"

V2=$(vram); W2=$(normalise)
note "VRAM before ${V2} MiB, matching worker processes ${W2}"

RUNTIME=$("$OM/bin/python" -c "import openmycelium,os;print(os.path.join(os.path.dirname(openmycelium.__file__),'runtime'))")
SERVING="$RUNTIME/serving"
note "worker module: $SERVING/pipeline_run.py"

# Invoked exactly as the coordinator invokes it: the vendor's own interpreter,
# which is where torch lives, with the runtime directories on PYTHONPATH.
# Running it under the CLI's virtualenv instead would fail on a missing torch
# and prove nothing about where the refusal happens.
note "worker interpreter: $TORCH_PY"
PYTHONPATH="$SERVING:$RUNTIME/scheduler:$RUNTIME/fabric:$RUNTIME/cli" \
"$TORCH_PY" "$SERVING/pipeline_run.py" \
  --role cuda --model "$MODEL" --placement "$LEGACY" \
  --run-id "$(cat /proc/sys/kernel/random/uuid)" > "$OUT/worker.json" 2> "$OUT/worker.err"
WCODE=$?
# Wait for it to be gone rather than assuming: an exit code says the parent
# reaped it, not that a child of it released the device.
for _ in $(seq 1 20); do [ "$(normalise)" = "$W2" ] && break; sleep 0.5; done
sleep 1
V3=$(vram); W3=$(normalise)

cat "$OUT/worker.json" "$OUT/worker.err" > "$OUT/worker.all" 2>/dev/null
note "exit ${WCODE}"
head -c 400 "$OUT/worker.all" | sed 's/^/    /'
echo

[ "$WCODE" -ne 0 ] && pass "worker refused (exit ${WCODE})" || fail "worker did not refuse"
[ "$WCODE" = "65" ] && pass "exit 65, as the contract specifies" \
                    || note "exit ${WCODE} (contract specifies 65)"
grep -q 'LEGACY_UNPINNED_MANIFEST' "$OUT/worker.all" \
  && pass "errorCode LEGACY_UNPINNED_MANIFEST" \
  || fail "expected LEGACY_UNPINNED_MANIFEST"
grep -q 'REPLAN_REQUIRED' "$OUT/worker.all" \
  && pass "remediation REPLAN_REQUIRED" \
  || fail "expected remediation REPLAN_REQUIRED"
# One code for one condition. Two would make a caller guess which to match.
if [ "$(grep -o 'LEGACY_UNPINNED_MANIFEST\|INVALID_MANIFEST\|UNSUPPORTED_MANIFEST_SCHEMA' "$OUT/worker.all" | sort -u | wc -l)" = "1" ]; then
  pass "exactly one manifest error code reported"
else
  fail "more than one manifest error code reported"
fi
grep -qi 'meta device\|installing\|loaded .* tensors\|stage built' "$OUT/worker.all" \
  && fail "the worker got as far as building a stage" \
  || pass "exited before stage construction"

[ "$W3" = "$W2" ] && pass "no orphan left (${W2} -> ${W3})" \
                  || fail "worker processes remain: ${W2} -> ${W3}"
if vram_returned "$V2" "$V3"; then
  pass "VRAM returned to baseline within ${VRAM_TOLERANCE_MIB} MiB (${V2} -> ${V3} MiB)"
else
  fail "VRAM did not return: ${V2} -> ${V3} MiB"
fi

echo
if [ "$FAILURES" = "0" ]; then
  echo "  == adapter refusal gate PASSED =="
else
  echo "  == adapter refusal gate FAILED: $FAILURES check(s) =="
fi
{
  echo "failures $FAILURES"
  echo "test1 exit $CODE vram $V0 -> $V1 workers $W0 -> $W1"
  echo "test2 exit $WCODE vram $V2 -> $V3 workers $W2 -> $W3"
} | tee "$OUT/summary.txt" | sed 's/^/    /'
exit "$FAILURES"
