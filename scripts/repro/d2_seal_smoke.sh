#!/usr/bin/env bash
# Gate D.2 seal: one paired 24-token smoke, `off` then `shadow`.
#
# This is not a performance measurement and must not be read as one. Position is
# not balanced, and there is one observation per arm; bounding shadow-mode
# overhead needs the position-balanced campaign Gate A defines, which is
# pre-registered for D.3 rather than done here.
#
# What it does establish, on real hardware and from the installed wheel:
#   - the correlation fields (schema, run, placement, boot, time) are present
#   - `off` still produces no observation file at all
#   - correctness is unchanged across the pair
#
# Per-run work directory, so neither arm overwrites the other's events. Boot id
# recorded per run, because CLOCK_MONOTONIC restarts near zero on each WSL boot
# and a duration is only meaningful inside one boot domain.
set -uo pipefail

OUT=${OM_OUT:-/var/log/om-d2-seal}
OM=${OM_BIN:-/opt/om/venv/bin/openmycelium}
MODEL=${OM_MODEL:-Mistral-Nemo-Instruct-2407}
PROMPT=${OM_PROMPT:-"Explain cross-vendor GPU inference in one sentence."}
SETTLE=${OM_SETTLE:-20}

rm -rf "$OUT"
mkdir -p "$OUT"

# 0.3.0a10 is a new build, so its content digest does not match the qualified
# record. The tuple refusing it is the qualification mechanism working; the
# override is the audited escape, not a way around it.
export OM_QUALIFICATION_MODE=override
export OM_QUALIFICATION_ACTOR=${OM_ACTOR:-d2-seal-smoke}

cat /proc/sys/kernel/random/boot_id > "$OUT/boot-before.txt"
nvidia-smi --query-gpu=memory.used,memory.total,power.draw --format=csv \
    > "$OUT/gpu-baseline.txt" 2>&1

run_arm() {
    local arm=$1 mode=$2 work status
    work="$OUT/work-$arm"
    mkdir -p "$work"
    cat /proc/sys/kernel/random/boot_id > "$OUT/boot-$arm.txt"

    if [ "$mode" = "off" ]; then
        unset OM_SAFETY_MODE
    else
        export OM_SAFETY_MODE="$mode"
    fi

    "$OM" run --model "$MODEL" --prompt "$PROMPT" \
        --max-new-tokens 24 --work-dir "$work" --json \
        > "$OUT/run-$arm.json" 2> "$OUT/run-$arm.err"
    status=$?
    printf '  %-7s exit %s\n' "$arm" "$status"

    if [ -f "$work/safety-shadow.jsonl" ]; then
        cp "$work/safety-shadow.jsonl" "$OUT/safety-shadow-$arm.jsonl"
        printf '  %-7s observations: %s\n' "$arm" \
            "$(wc -l < "$work/safety-shadow.jsonl")"
    else
        printf '  %-7s observations: none (no safety-shadow.jsonl)\n' "$arm"
    fi
    sleep "$SETTLE"
}

run_arm off off
run_arm shadow shadow

cat /proc/sys/kernel/random/boot_id > "$OUT/boot-after.txt"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv \
    > "$OUT/gpu-after.txt" 2>&1

# What the device total can and cannot show.
#
# Under WSL `nvidia-smi` reports the whole physical card, which includes
# Windows host usage, and it cannot enumerate Windows processes. So a total
# above the pre-run reading does not by itself mean this runtime kept memory,
# and a total equal to it does not prove this runtime released it. Earlier
# gates reported that number alone as "VRAM returned"; it does not carry that
# claim on its own.
#
# What is attributable is recorded beside it: whether any worker process
# survives, and whether any compute context remains on the device.
# A worker is a python interpreter running one of these modules. Matching the
# bare module names matches any shell whose command line happens to mention
# them -- including this script, and including the pgrep that looks for them.
# That turns "zero orphan workers" into a claim about nothing, so the pattern
# requires an interpreter and the current process tree is excluded.
pgrep -a -f "python[^ ]* .*(stage_model|pipeline_run|forward_pass)" 2>/dev/null \
    | awk -v self="$$" -v parent="$PPID" '$1 != self && $1 != parent' \
    | grep -v "d2_seal_smoke" > "$OUT/orphans.txt"
[ -s "$OUT/orphans.txt" ] || echo "none" > "$OUT/orphans.txt"
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv \
    > "$OUT/gpu-compute-apps.txt" 2>&1
{
    echo "Device totals under WSL include Windows host usage and cannot be"
    echo "attributed by this instrument. Read gpu-after.txt with orphans.txt"
    echo "and gpu-compute-apps.txt, not on its own."
} > "$OUT/gpu-attribution.txt"

printf '  evidence in %s\n' "$OUT"
