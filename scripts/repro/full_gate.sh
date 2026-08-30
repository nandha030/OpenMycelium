#!/usr/bin/env bash
# Every gate this milestone must pass, in the order their dependencies require.
#
# The order is not arbitrary. Qualification has to exist before anything that
# executes the model can run at all, and the qualification lifecycle has to run
# after the record it will set aside and restore. Running these by hand in the
# wrong order produces failures that look like defects and are not.
#
#   0  qualify        one qualifying run, then the record it is backed by
#   1  unit suite     every test in the repository, one verdict
#   2  performance    three independent sessions, judged by median
#   3  refusal        both refusal paths, VRAM measured either side
#   4  lifecycle      the six qualification states, in order
#   5  comparison     against the pre-refactor baseline
#
# Performance runs before the other GPU work, and after a settle period. The
# frozen baseline was captured on an otherwise idle machine, and "under the same
# conditions" is part of the procedure -- running the campaign as step three of a
# battery measures a machine that has just been worked, which is a real number
# about the wrong thing. Doing it in that order once widened the TTFT spread from
# 23% to 37% and left the median 0.9 ms inside a limit it should clear easily.
#
# Each writes its own evidence. A group that fails does not stop the rest --
# a partial picture is what makes a failure hard to diagnose.
set -uo pipefail

_here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)
OM=${OM_VENV:-/opt/om/venv}
H=${OM_HARNESS:-/root/repro}
OUT=${OM_OUT:-/var/log/om-full-gate}
MODEL=${OM_MODEL:-Mistral-Nemo-Instruct-2407}
ACTOR=${OM_ACTOR:-nandhavignesh}
BASELINE=${OM_BASELINE:-/var/log/om-baseline}

export PATH="$OM/bin:$PATH"
unset PYTHONPATH
mkdir -p "$OUT"
FAILED=""

report() { printf '  %-14s %s\n' "$1" "$2"; }

echo "  == full gate =="
openmycelium version --json 2>/dev/null \
  | grep -E '"openmycelium"|installedContentSha256|mcclContentSha256|mcclVersion' \
  | tr -d ' ' | sed 's/^/    /'

# ------------------------------------------------------------------ 0. qualify
echo
echo "  -- 0. qualification --"
if openmycelium qualify status --model "$MODEL" > "$OUT/qualify-before.txt" 2>&1; then
  report "qualify" "already qualified; no new record needed"
else
  OM_QUALIFICATION_MODE=qualify OM_QUALIFICATION_ACTOR="$ACTOR" \
  OM_QUALIFICATION_REASON="full gate" \
    openmycelium run --model "$MODEL" \
      --prompt "Explain cross-vendor GPU inference in one sentence." \
      --max-new-tokens 24 --json > "$OUT/qualify-run.json" 2> "$OUT/qualify-run.err"
  code=$?
  "$OM/bin/python" - "$OUT" "$code" <<'PY'
import json, sys
out, code = sys.argv[1], int(sys.argv[2])
raw = open(f"{out}/qualify-run.json").read()
start = raw.find("{")
document = json.loads(raw[start:]) if start >= 0 else {}
result = document.get("result") or {}
tokens = result.get("generatedTokens") or []
ok = code == 0 and len(tokens) == 24
print(f"    qualifying run: {len(tokens)} tokens, ttft {result.get('ttftMs')} ms, "
      f"decode {result.get('decodeTokensPerSecond')} tok/s, exit {code}")
json.dump({"failures": 0 if ok else 1, "gate": "full-gate-qualification",
           "generatedTokens": tokens, "text": result.get("text"),
           "ttftMs": result.get("ttftMs"),
           "decodeTokensPerSecond": result.get("decodeTokensPerSecond"),
           "exitCode": code},
          open(f"{out}/qualify-evidence.json", "w"), indent=2)
PY
  OM_QUALIFICATION_MODE=qualify OM_QUALIFICATION_ACTOR="$ACTOR" \
  OM_QUALIFICATION_REASON="full gate" \
    openmycelium qualify record --model "$MODEL" \
      --evidence "$OUT/qualify-evidence.json" > "$OUT/qualify-record.txt" 2>&1
  if [ $? = 0 ]; then
    report "qualify" "record written"
    sed 's/^/      /' "$OUT/qualify-record.txt" | head -16
  else
    report "qualify" "FAILED"
    sed 's/^/      /' "$OUT/qualify-record.txt" | tail -5
    FAILED="$FAILED qualify"
  fi
fi

# --------------------------------------------------------------- 1. unit suite
echo
echo "  -- 1. unit suite --"
# PYTEST_PY names a separate virtualenv, never the product venv: installing a
# test runner into the environment under test changes the thing being tested.
PYTEST_PY="${PYTEST_PY:-/opt/om-test-tools/bin/python}" \
  bash "$H/run_unit_suite.sh" > "$OUT/unit-suite.txt" 2>&1 \
  && report "unit" "$(grep -E '== unit suite' "$OUT/unit-suite.txt" | tail -1 | sed 's/^ *//')" \
  || { report "unit" "FAILED"; tail -12 "$OUT/unit-suite.txt" | sed 's/^/      /'
       FAILED="$FAILED unit"; }

# ------------------------------------------------------------- 2. performance
echo
echo "  -- 2. performance --"
# Two modes, because re-measuring an artifact that has already been measured is
# not more rigorous -- it is a second sample of a noisy instrument presented as
# confirmation. When a preserved paired campaign already covers this exact
# installed content, replay its analysis; the campaign is the evidence and the
# analysis is reproducible from its raw record.
#
#   OM_PERFORMANCE=replay OM_PAIRED_ROWS=... OM_INCUMBENT=... OM_CANDIDATE=...
#   OM_PERFORMANCE=run                    (measures a new campaign)
PERFORMANCE=${OM_PERFORMANCE:-run}
if [ "$PERFORMANCE" = "replay" ]; then
  ROWS=${OM_PAIRED_ROWS:?set OM_PAIRED_ROWS to the preserved rows.jsonl}
  "$OM/bin/python" "$H/paired_analysis.py" "$ROWS" \
    --incumbent "${OM_INCUMBENT:?set OM_INCUMBENT}" \
    --candidate "${OM_CANDIDATE:?set OM_CANDIDATE}" \
    --json "$OUT/performance-replay.json" > "$OUT/performance.txt" 2>&1
  if [ $? = 0 ]; then
    report "performance" "REPLAYED, PASSED"
  else
    report "performance" "REPLAYED, FAILED"
    FAILED="$FAILED performance"
  fi
  grep -E 'mean |regression|median|bootId|output|position|PASSED|FAILED' \
    "$OUT/performance.txt" | sed 's/^/      /'
else
  # Settle first, and go before the other GPU gates. VRAM released by the
  # qualifying run is not returned the instant the process exits, and a session
  # started into that measures recovery rather than steady state.
  printf '    settling %ss, then measuring on an idle machine\n' "${OM_SETTLE:-90}"
  sleep "${OM_SETTLE:-90}"
  OM_OUT="$OUT/performance" bash "$H/performance_campaign.sh" \
    > "$OUT/performance.txt" 2>&1
  if [ $? = 0 ]; then
    report "performance" "PASSED"
  else
    report "performance" "FAILED"
    FAILED="$FAILED performance"
  fi
  grep -E 'samples|median|spread|token counts|PASSED|FAILED' "$OUT/performance.txt" \
    | sed 's/^/      /'
fi

# ----------------------------------------------------------------- 3. refusal
echo
echo "  -- 3. adapter refusal --"
bash "$H/adapter_refusal_gate.sh" > "$OUT/refusal.txt" 2>&1 \
  && report "refusal" "PASSED" \
  || { report "refusal" "FAILED"; grep FAIL "$OUT/refusal.txt" | sed 's/^/      /'
       FAILED="$FAILED refusal"; }

# --------------------------------------------------------------- 4. lifecycle
echo
echo "  -- 4. qualification lifecycle --"
bash "$H/qualification_gate.sh" > "$OUT/lifecycle.txt" 2>&1 \
  && report "lifecycle" "PASSED" \
  || { report "lifecycle" "FAILED"; grep FAIL "$OUT/lifecycle.txt" | sed 's/^/      /'
       FAILED="$FAILED lifecycle"; }

# -------------------------------------------------------------- 5. comparison
echo
echo "  -- 5. baseline comparison --"
rm -rf "$OUT/refactor"
OM_OUT="$OUT/refactor" bash "$H/capture_baseline.sh" > "$OUT/capture.txt" 2>&1
"$OM/bin/python" "$H/compare_refactor.py" "$BASELINE" "$OUT/refactor" \
  > "$OUT/comparison.txt" 2>&1
if [ $? = 0 ]; then
  report "comparison" "PASSED"
else
  report "comparison" "FAILED"
  FAILED="$FAILED comparison"
fi
grep -E 'CHANGED|MISSING|UNCHANGED|PASSED|FAILED' "$OUT/comparison.txt" \
  | sed 's/^/      /'

echo
if [ -z "$FAILED" ]; then
  echo "  == full gate PASSED =="
else
  echo "  == full gate FAILED:$FAILED =="
fi
echo "  evidence in $OUT"
[ -z "$FAILED" ]
