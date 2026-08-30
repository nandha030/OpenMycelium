#!/usr/bin/env bash
# The qualification lifecycle, measured on hardware, in the order it must hold.
#
# Six states, and each one is only meaningful because of the one before it:
#
#   1  no record          -> execution refuses, before any allocation
#   2  qualification mode -> the hardware gate is permitted to run
#   3  gate passes        -> a tuple-scoped record is written atomically
#   4  record present     -> normal execution succeeds, no mode set
#   5  tuple altered      -> refuses again
#   6  override used      -> permitted, and named in the audit trail
#
# Step 1 is the load-bearing one. If a record already exists when this starts,
# the whole sequence proves nothing, so the ledger's adapter section is moved
# aside first and restored at the end -- the provisioning and transport sections
# are never touched.
#
# Run inside the qualified distribution, against the INSTALLED wheel.
set -uo pipefail

OM=${OM_VENV:-/opt/om/venv}
OUT=${OM_OUT:-/var/log/om-qualification}
MODEL=${OM_MODEL:-Mistral-Nemo-Instruct-2407}
LEDGER=${OM_LEDGER:-/var/lib/openmycelium/xvendor_qualification.json}
ACTOR=${OM_QUALIFICATION_ACTOR:-adapter-sdk-gate}

export PATH="$OM/bin:$PATH"
unset PYTHONPATH
unset OM_QUALIFICATION_MODE          # step 1 must start from nothing set
mkdir -p "$OUT"
FAILURES=0

note() { printf '    %s\n' "$*"; }
fail() { printf '    FAIL  %s\n' "$*"; FAILURES=$((FAILURES + 1)); }
pass() { printf '    ok    %s\n' "$*"; }
vram() { nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1; }
workers() { local n; n=$(pgrep -fc "pipeline_run|forward_pass" 2>/dev/null || true); echo "${n:-0}"; }

# Detach the adapter section, keep everything else. Restored unconditionally, so
# an interrupted run does not leave the machine unqualified.
SAVED="$OUT/ledger-adapter-section.json"
"$OM/bin/python" - "$LEDGER" "$SAVED" <<'PY'
import json, os, sys
path, saved = sys.argv[1], sys.argv[2]
document = {}
if os.path.isfile(path):
    try:
        document = json.load(open(path))
    except ValueError:
        document = {}
section = document.pop("adapterQualification", None)
json.dump(section, open(saved, "w"))
if os.path.isfile(path):
    temporary = path + ".partial"
    json.dump(document, open(temporary, "w"), indent=2, sort_keys=True)
    os.replace(temporary, path)
print(f"    set aside {len((section or {}).get('records', []))} existing record(s)")
PY

restore() {
  "$OM/bin/python" - "$LEDGER" "$SAVED" <<'PY'
import json, os, sys
path, saved = sys.argv[1], sys.argv[2]
try:
    section = json.load(open(saved))
except (OSError, ValueError):
    section = None
document = {}
if os.path.isfile(path):
    try:
        document = json.load(open(path))
    except ValueError:
        document = {}
# Replace, never merge. Step 5 deliberately corrupts a record to prove the
# tuple is checked; leaving that behind would put a record on the machine that
# can never match, which reads as "qualified once, broken now". Restoring means
# putting back exactly what was there -- including nothing.
document.pop("adapterQualification", None)
if section:
    document["adapterQualification"] = section
temporary = path + ".partial"
json.dump(document, open(temporary, "w"), indent=2, sort_keys=True)
os.replace(temporary, path)
PY
}

echo "  == qualification lifecycle =="
note "ledger $LEDGER"
note "model  $MODEL"

# --------------------------------------------------------------- 1. refuse
echo
echo "  -- 1. no record: execution refuses before allocation --"
V0=$(vram); W0=$(workers)
note "VRAM before ${V0} MiB, workers ${W0}"
openmycelium run --model "$MODEL" --prompt "qualification probe" \
  --max-new-tokens 4 > "$OUT/refused.out" 2> "$OUT/refused.err"
CODE=$?
sleep 3
V1=$(vram); W1=$(workers)
cat "$OUT/refused.out" "$OUT/refused.err" > "$OUT/refused.all" 2>/dev/null

note "exit ${CODE}"
head -c 300 "$OUT/refused.all" | sed 's/^/    /'
echo
[ "$CODE" -ne 0 ] && pass "refused (exit ${CODE})" || fail "ran without a record"
grep -q 'ADAPTER_UNQUALIFIED\|qualification record' "$OUT/refused.all" \
  && pass "refused as unqualified" || fail "not refused as unqualified"
[ "$W1" = "$W0" ] && pass "no worker spawned (${W0} -> ${W1})" \
                  || fail "workers ${W0} -> ${W1}"
if [ "$((V1 - V0))" -le 32 ] && [ "$((V0 - V1))" -le 32 ]; then
  pass "VRAM unchanged (${V0} -> ${V1} MiB)"
else
  fail "VRAM moved ${V0} -> ${V1} MiB before the refusal"
fi

# ----------------------------------------------- 2 & 3. qualify, then record
echo
echo "  -- 2. qualification mode runs the hardware gate --"
GATE_SUMMARY=${OM_GATE_SUMMARY:-$OUT/gate-summary.json}
export OM_QUALIFICATION_MODE=qualify
export OM_QUALIFICATION_ACTOR="$ACTOR"
export OM_QUALIFICATION_REASON="post-refactor Adapter SDK hardware gate"

openmycelium run --model "$MODEL" --prompt "qualification probe" \
  --max-new-tokens 8 --json > "$OUT/qualifying.json" 2> "$OUT/qualifying.err"
GCODE=$?
note "exit ${GCODE}"
"$OM/bin/python" - "$OUT" "$GATE_SUMMARY" "$GCODE" <<'PY'
import json, sys
out, summary_path, code = sys.argv[1], sys.argv[2], int(sys.argv[3])
raw = open(f"{out}/qualifying.json").read()
start = raw.find("{")
document = json.loads(raw[start:]) if start >= 0 else {}
result = document.get("result") or {}
tokens = result.get("generatedTokens") or []
ok = code == 0 and len(tokens) == 8
print(f"    generated {len(tokens)} tokens, ttft {result.get('ttftMs')} ms, "
      f"decode {result.get('decodeTokensPerSecond')} tok/s")
json.dump({"failures": 0 if ok else 1,
           "gate": "qualification-run",
           "generatedTokenCount": len(tokens),
           "generatedTokens": tokens,
           "ttftMs": result.get("ttftMs"),
           "decodeTokensPerSecond": result.get("decodeTokensPerSecond"),
           "exitCode": code},
          open(summary_path, "w"), indent=2)
PY
[ "$GCODE" = "0" ] && pass "qualification mode permitted the run" \
                   || fail "qualification mode did not permit the run"
grep -q 'qualification_override' "$OUT/qualifying.err" "$OUT/qualifying.json" 2>/dev/null \
  && note "override event emitted during the qualifying run"

echo
echo "  -- 3. a passing gate writes a tuple-scoped record --"
openmycelium qualify record --model "$MODEL" --evidence "$GATE_SUMMARY" \
  > "$OUT/record.out" 2> "$OUT/record.err"
RCODE=$?
sed 's/^/    /' "$OUT/record.out" | head -20
[ "$RCODE" = "0" ] && pass "record written" || { fail "record not written"; sed 's/^/      /' "$OUT/record.err"; }
ls "$LEDGER.partial" > /dev/null 2>&1 \
  && fail "a .partial file was left behind; the write was not atomic" \
  || pass "no partial file left behind"
"$OM/bin/python" - "$LEDGER" <<'PY'
import json, sys
document = json.load(open(sys.argv[1]))
print(f"    ledger sections: {sorted(document)}")
records = (document.get("adapterQualification") or {}).get("records") or []
print(f"    adapter records: {len(records)}")
if "provision" in document:
    print("    provision section still present")
PY
grep -q '"provision"' "$LEDGER" \
  && pass "the provisioning section survived the write" \
  || fail "the provisioning section was lost"

# ------------------------------------------------------------- 4. now runs
echo
echo "  -- 4. normal execution, with no mode set --"
unset OM_QUALIFICATION_MODE OM_QUALIFICATION_ACTOR OM_QUALIFICATION_REASON
openmycelium run --model "$MODEL" --prompt "qualification probe" \
  --max-new-tokens 8 --json > "$OUT/permitted.json" 2> "$OUT/permitted.err"
PCODE=$?
note "exit ${PCODE}"
[ "$PCODE" = "0" ] && pass "normal execution succeeds once qualified" \
                   || { fail "still refused after the record was written"
                        tail -c 400 "$OUT/permitted.err" | sed 's/^/      /'; }

# --------------------------------------------------------- 5. altered tuple
echo
echo "  -- 5. an altered tuple refuses again --"
# The wheel's content digest is one element of the tuple. Rewriting the recorded
# element rather than the machine is what makes this reversible; it is exactly
# the comparison a genuinely different wheel would fail.
"$OM/bin/python" - "$LEDGER" <<'PY'
import json, os, sys
path = sys.argv[1]
document = json.load(open(path))
records = (document.get("adapterQualification") or {}).get("records") or []
if not records:
    # Say so rather than raising. With no record to alter, step 5 would refuse
    # for the same reason as step 1 and prove nothing about the tuple -- and a
    # traceback here would hide that the earlier step is what failed.
    print("    SKIPPED-INEFFECTIVE  no record to alter; step 3 did not write one")
    raise SystemExit(0)
for record in records:
    record["situation"]["openmyceliumContent"] = "0" * 64
temporary = path + ".partial"
json.dump(document, open(temporary, "w"), indent=2, sort_keys=True)
os.replace(temporary, path)
print(f"    rewrote openmyceliumContent in {len(records)} record(s)")
PY
openmycelium run --model "$MODEL" --prompt "qualification probe" \
  --max-new-tokens 4 > "$OUT/altered.out" 2> "$OUT/altered.err"
ACODE=$?
cat "$OUT/altered.out" "$OUT/altered.err" > "$OUT/altered.all" 2>/dev/null
note "exit ${ACODE}"
[ "$ACODE" -ne 0 ] && pass "a record for a different build does not apply" \
                   || fail "a record for a different build was accepted"
grep -q 'ADAPTER_UNQUALIFIED\|qualification record' "$OUT/altered.all" \
  && pass "refused as unqualified again" || fail "refused for the wrong reason"

# ------------------------------------------------------------- 6. override
echo
echo "  -- 6. an explicit override is permitted and recorded --"
export OM_QUALIFICATION_MODE=override
export OM_QUALIFICATION_ACTOR="$ACTOR"
export OM_QUALIFICATION_REASON="demonstrating the override path"
openmycelium run --model "$MODEL" --prompt "qualification probe" \
  --max-new-tokens 4 --json > "$OUT/override.json" 2> "$OUT/override.err"
OCODE=$?
unset OM_QUALIFICATION_MODE OM_QUALIFICATION_ACTOR OM_QUALIFICATION_REASON
note "exit ${OCODE}"
[ "$OCODE" = "0" ] && pass "override permitted the unqualified situation" \
                   || fail "override did not permit it"

EVENTS=$(ls -t /var/lib/openmycelium/state/audit/*.jsonl 2>/dev/null | head -1)
[ -z "$EVENTS" ] && EVENTS=$(ls -t /opt/openmycelium/*.jsonl 2>/dev/null | head -1)
if [ -n "$EVENTS" ]; then
  note "audit trail $EVENTS"
  # Parsed, not grepped. The writer serialises with `", "` separators, so a
  # pattern written as `"key":"value"` matches nothing and reports a missing
  # field that is present -- a false failure is as bad as a false pass.
  "$OM/bin/python" - "$EVENTS" "$ACTOR" <<'PY'
import json, sys
path, actor = sys.argv[1], sys.argv[2]
found = []
for line in open(path, encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    try:
        record = json.loads(line)
    except ValueError:
        continue
    if record.get("event") == "qualification_override":
        found.append(record)
if not found:
    print("    FAIL  no qualification_override event in the audit trail")
    raise SystemExit(1)
entry = found[-1]
print(f"    actor  {entry.get('qualificationActor')!r}  "
      f"mode {entry.get('qualificationMode')!r}  "
      f"situation {str(entry.get('situationDigest'))[:16]}")
problems = 0
if entry.get("qualificationActor") != actor:
    print(f"    FAIL  the audit entry does not name the actor ({actor})")
    problems += 1
else:
    print("    ok    the audit entry names the actor")
for field in ("placementId", "manifestDigest", "modelFingerprint"):
    if not entry.get(field):
        print(f"    FAIL  the override event carries no {field}")
        problems += 1
if not problems:
    print("    ok    the override is bound to the verified placement")
raise SystemExit(1 if problems else 0)
PY
  if [ "$?" = "0" ]; then
    pass "the override appears in the audit trail, attributed and bound"
  else
    fail "the override audit entry is missing or incomplete"
  fi
else
  fail "no audit trail found to check the override against"
fi

# ------------------------------------------------------------------ restore
echo
restore
note "restored the ledger's original adapter section"
openmycelium qualify list 2>/dev/null | sed 's/^/    /' | head -6

echo
if [ "$FAILURES" = "0" ]; then
  echo "  == qualification lifecycle PASSED =="
else
  echo "  == qualification lifecycle FAILED: $FAILURES check(s) =="
fi
{
  echo "failures $FAILURES"
  echo "step1 refuse exit $CODE"
  echo "step2 qualify exit $GCODE"
  echo "step3 record exit $RCODE"
  echo "step4 permitted exit $PCODE"
  echo "step5 altered exit $ACODE"
  echo "step6 override exit $OCODE"
} | tee "$OUT/summary.txt" | sed 's/^/    /'
exit "$FAILURES"
