#!/usr/bin/env bash
# Interleaved paired campaign: does the candidate move TTFT, or does the session?
#
# Two blocks of sessions cannot answer that. Measuring a4 then a5 found a5
# 27.5 ms slower; reversing found a5 first at 170.5 ms and a4 second at 169.1 ms.
# The same build measured 142.5 ms early in a session and 169.1 ms late. Order was
# confounded with build in both directions.
#
# So: one position-balanced interleaved sequence, from immutable side-by-side
# installations that are never reinstalled during it. Reinstalling is itself a
# machine-state change, and it had happened between every earlier measurement.
#
#   paired_ab.sh --incumbent 0.3.0a5 --candidate 0.3.0a6 [--per-build 5]
#
# Installations are expected at $OM_AB_ROOT/<label> and are never written to.
#
# Judgement lives in paired_analysis.py, deliberately not here: a preserved
# campaign must be re-judgeable from its raw record without re-running it, and
# the acceptance rule should be readable without reading a shell script.
set -uo pipefail

AB_ROOT=${OM_AB_ROOT:-/opt/om-ab}
OUT=${OM_OUT:-/var/log/om-paired}
MODEL=${OM_MODEL:-Mistral-Nemo-Instruct-2407}
PROMPT=${OM_PROMPT:-"Explain cross-vendor GPU inference in one sentence."}
TOKENS=${OM_TOKENS:-24}
SETTLE=${OM_SETTLE:-45}
PER_BUILD=${OM_PER_BUILD:-5}
INCUMBENT=""
CANDIDATE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --incumbent) INCUMBENT=$2; shift 2 ;;
    --candidate) CANDIDATE=$2; shift 2 ;;
    --per-build) PER_BUILD=$2; shift 2 ;;
    --out)       OUT=$2; shift 2 ;;
    *) echo "  unknown argument: $1" >&2; exit 64 ;;
  esac
done
if [ -z "$INCUMBENT" ] || [ -z "$CANDIDATE" ]; then
  echo "  usage: paired_ab.sh --incumbent LABEL --candidate LABEL [--per-build N]" >&2
  exit 64
fi
if [ "$PER_BUILD" -lt 5 ]; then
  # Not a hard refusal: a campaign may be pre-registered smaller deliberately,
  # and one already was. But it is stated, so an under-powered campaign is never
  # mistaken for a full one when the evidence is read back later.
  echo "  note: $PER_BUILD observations per build; the contract recommends 5"
fi

export OM_QUALIFICATION_MODE=${OM_QUALIFICATION_MODE:-override}
export OM_QUALIFICATION_ACTOR=${OM_QUALIFICATION_ACTOR:-paired-campaign}
export OM_QUALIFICATION_REASON="paired campaign; each installation has its own content digest"
unset PYTHONPATH
mkdir -p "$OUT"

# Position-balanced sequence, built from ICCI blocks.
#
# Equal mean position is the whole point: TTFT drifts upward through a session,
# so a build measured later is penalised for nothing, and that is exactly what
# made two earlier campaigns disagree.
#
# An ICCI block puts the incumbent at relative positions 1 and 4 and the
# candidate at 2 and 3 -- mean 2.5 each. Every block is internally balanced, so
# any whole number of them is balanced, and no alternating second pattern is
# needed. An odd count truncates mid-block and leaves an imbalance of 0.2, which
# is the least achievable when 2n positions cannot be split evenly.
sequence() {
  local n=$1 out="" i=0
  while [ "$i" -lt "$n" ]; do
    out="$out $INCUMBENT $CANDIDATE $CANDIDATE $INCUMBENT"
    i=$((i + 2))
  done
  echo $out | tr ' ' '\n' | head -n $((n * 2)) | tr '\n' ' '
}
SEQUENCE=$(sequence "$PER_BUILD")

gpu() {
  nvidia-smi --query-gpu=temperature.gpu,clocks.sm,clocks.mem,power.draw,memory.used,utilization.gpu \
    --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' '
}
workers() { local n; n=$(pgrep -fc "pipeline_run|forward_pass" 2>/dev/null || true); echo "${n:-0}"; }
boot() { cat /proc/sys/kernel/random/boot_id; }

BOOT_AT_START=$(boot)

echo "  == interleaved paired campaign =="
echo "    incumbent   $INCUMBENT"
echo "    candidate   $CANDIDATE"
echo "    sequence    $SEQUENCE"
echo "    prompt      $PROMPT"
echo "    tokens      $TOKENS"
echo "    bootId      $BOOT_AT_START"
echo "    uptime      $(cut -d. -f1 /proc/uptime)s"

for build in "$INCUMBENT" "$CANDIDATE"; do
  if [ ! -x "$AB_ROOT/$build/bin/openmycelium" ]; then
    echo "    missing immutable installation: $AB_ROOT/$build" >&2
    exit 78
  fi
  printf '    %-11s %s\n' "$build" \
    "$("$AB_ROOT/$build/bin/openmycelium" version --json 2>/dev/null \
       | grep -E '"openmycelium"|installedContentSha256|mcclContentSha256' | tr -d ' \n')"
done

# ------------------------------------------------- pre-registered machine state
echo
echo "  -- pre-registered machine state --"
BUSY=$(workers)
if [ "$BUSY" != "0" ]; then
  echo "    refusing: ${BUSY} worker process(es) already running; this would" >&2
  echo "    measure contention, which is a real number about the wrong thing" >&2
  exit 75
fi
echo "    workers     0"
echo "    gpu         $(gpu)"
echo "    settle      ${SETTLE}s between runs"

index=0
for build in $SEQUENCE; do
  index=$((index + 1))
  venv="$AB_ROOT/$build"

  # Every run gets its own work_dir. Without this each run's events.jsonl and
  # both worker logs are overwritten by the next one -- an eight-run campaign
  # kept the evidence of exactly one run, and only luck made that the run whose
  # boot identity needed proving.
  work="$OUT/work/run-$index-$build"
  rm -rf "$work"; mkdir -p "$work"

  sleep "$SETTLE"
  boot_before=$(boot)
  before=$(gpu)
  started=$(date -Is)

  "$venv/bin/openmycelium" run --model "$MODEL" --prompt "$PROMPT" \
    --max-new-tokens "$TOKENS" --work-dir "$work" --json \
    > "$OUT/run-$index-$build.json" 2> "$OUT/run-$index-$build.err"
  code=$?
  sleep 5
  after=$(gpu)
  boot_after=$(boot)
  orphans=$(workers)

  # A restart mid-campaign invalidates everything measured against a different
  # machine, so it stops here rather than being discovered afterwards.
  if [ "$boot_before" != "$BOOT_AT_START" ] || [ "$boot_after" != "$BOOT_AT_START" ]; then
    echo "    boot identity changed during run $index" >&2
    echo "      campaign start $BOOT_AT_START" >&2
    echo "      run $index      $boot_before -> $boot_after" >&2
    echo "    CAMPAIGN INVALID" >&2
    exit 75
  fi

  "$venv/bin/python" - "$OUT" "$index" "$build" "$code" "$before" "$after" \
      "$started" "$boot_after" "$work" "$orphans" <<'PY'
import json, os, sys
(out, index, build, code, before, after, started, boot, work, orphans) = (
    sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4]), sys.argv[5],
    sys.argv[6], sys.argv[7], sys.argv[8], sys.argv[9], int(sys.argv[10]))

raw = open(f"{out}/run-{index}-{build}.json").read()
start = raw.find("{")
document = json.loads(raw[start:]) if start >= 0 else {}
result = document.get("result") or {}
placement = document.get("placement") or {}
stages = placement.get("stages") or []
owned = [set(stage.get("tensors") or []) for stage in stages]
tokens = result.get("generatedTokens") or []

# Writer and sequence identity from the run's own events, which survive because
# this run had its own work_dir.
writers, sequences, event_boots, runids = set(), [], set(), set()
events_path = os.path.join(work, "events.jsonl")
if os.path.isfile(events_path):
    for line in open(events_path, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("writerId"):
            writers.add(event["writerId"])
        if event.get("eventSequence") is not None:
            sequences.append(int(event["eventSequence"]))
        if event.get("bootId"):
            event_boots.add(event["bootId"])
        if event.get("runId"):
            runids.add(event["runId"])

fields = ["temperatureC", "smClockMHz", "memClockMHz", "powerW", "vramMiB", "utilPct"]
row = {
    "index": index, "build": build, "exitCode": code, "startedAt": started,
    "bootId": boot,
    "eventBootIds": sorted(event_boots),
    "runIds": sorted(runids),
    "writerIds": sorted(writers),
    "eventCount": len(sequences),
    "eventSequenceMax": max(sequences) if sequences else None,
    "ttftMs": result.get("ttftMs"),
    "decodeTokensPerSecond": result.get("decodeTokensPerSecond"),
    "prefillTokensPerSecond": result.get("prefillTokensPerSecond"),
    "interTokenMs": result.get("interTokenMs"),
    "warmupMs": result.get("warmupMs"),
    "totalSeconds": result.get("totalSeconds"),
    "promptTokens": result.get("promptTokens"),
    "generatedTokenCount": len(tokens),
    "generatedTokens": tokens,
    "placementId": placement.get("placementId"),
    "manifestDigest": placement.get("manifestDigest"),
    "modelFingerprint": (placement.get("model") or {}).get("fingerprint"),
    "adapterId": placement.get("adapterId"),
    "adapterVersion": placement.get("adapterVersion"),
    "adapterConfigDigest": placement.get("adapterConfigDigest"),
    "buildIdentity": placement.get("build") or {},
    "ownershipCounts": sorted(len(names) for names in owned),
    "ownershipTotal": sum(len(names) for names in owned),
    "ownershipOverlap": len(owned[0] & owned[1]) if len(owned) == 2 else None,
    "boundaryAfterLayer": (placement.get("pipeline") or {}).get("boundaryAfterLayer"),
    # Cleanup state: orphaned workers after the run, and VRAM either side.
    "orphanWorkers": orphans,
    "gpuBefore": dict(zip(fields, before.split(","))),
    "gpuAfter": dict(zip(fields, after.split(","))),
    "decomposition": result.get("decomposition") or {},
    "workDir": work,
}
with open(f"{out}/rows.jsonl", "a", encoding="utf-8") as handle:
    handle.write(json.dumps(row, sort_keys=True) + "\n")

print(f"    {index}. {build:<9} exit {code}  TTFT {row['ttftMs']} ms  "
      f"decode {row['decodeTokensPerSecond']} tok/s  "
      f"{row['generatedTokenCount']} tok  "
      f"own {row['ownershipCounts']} overlap {row['ownershipOverlap']}  "
      f"events {row['eventCount']}  orphans {orphans}  "
      f"{row['gpuBefore'].get('temperatureC')}C")
PY
done

echo
echo "  campaign complete; boot identity constant at $BOOT_AT_START"
echo "  raw record  $OUT/rows.jsonl"
echo "  per-run evidence under $OUT/work/"
echo
echo "  judge it with:"
echo "    paired_analysis.py $OUT/rows.jsonl --incumbent $INCUMBENT --candidate $CANDIDATE"
