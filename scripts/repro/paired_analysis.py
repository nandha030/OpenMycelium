"""Judge a paired campaign, and be replayable years later from its raw record.

Separate from the harness that produces the data, so a preserved campaign can be
re-judged without re-running it, and so the acceptance rule lives somewhere a
reviewer can read without reading a shell script.

The acceptance rule is the position-balanced mean TTFT regression of the
candidate against the incumbent, and nothing else:

    regression = (candidate_mean - incumbent_mean) / incumbent_mean
    pass when regression <= 20%

Adjacent-pair differences are printed and never used as a threshold. An earlier
draft proposed accepting when the mixed-pair difference stayed under the
same-build pair spread; with two same-build pairs that spread is one observation
of a noisy quantity, and a campaign that happened to drift hard would have
licensed a real regression. They are diagnostics. They stay diagnostics.

Position balance is checked rather than assumed. The whole reason two three-run
blocks failed here is that TTFT drifts upward through a session, so a build
measured later looks worse for no reason. Equal mean position is what makes the
two means comparable at all; unequal positions are reported as a defect in the
campaign, not silently averaged.

    paired_analysis.py rows.jsonl --incumbent a4 --candidate a5
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from typing import Any, Dict, List

#: Frozen. Decode is a hard gate and held across every campaign run to date.
DECODE_LOW, DECODE_HIGH = 10.9, 11.3

#: The tolerance the contract already used -- baseline median +20% was 173.2 ms
#: -- applied to a paired comparison instead of an absolute number.
MAX_REGRESSION = 0.20

#: Retained as a historical observation. Not a pass/fail gate: the measured
#: within-build run-to-run range is 44-47 ms against a 28.9 ms band, so the
#: instrument cannot resolve it.
HISTORICAL_TTFT_LIMIT_MS = 173.2

#: Pre-registered campaigns below this are reported as under-powered. A campaign
#: that pre-registered fewer is judged as it was registered, never re-judged
#: after the fact.
RECOMMENDED_PER_BUILD = 5

FROZEN_TOKENS = [49256, 9332, 24227, 56455, 31587, 9985, 7523, 1750, 113422,
                 8832, 9055, 6056, 1408, 2801, 47910, 1307, 20534, 7176, 3816,
                 56309, 1317, 3398, 3486, 1505]


def load(path: str) -> List[Dict[str, Any]]:
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda row: row["index"])
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rows")
    parser.add_argument("--incumbent", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    rows = load(args.rows)
    usable = [row for row in rows if row.get("exitCode") == 0 and row.get("ttftMs")]
    problems: List[str] = []

    print(f"  campaign        {args.rows}")
    print(f"  observations    {len(rows)} recorded, {len(usable)} usable")
    if len(usable) != len(rows):
        # Never silently dropped. A failed run is a fact about the campaign.
        for row in rows:
            if row not in usable:
                print(f"    unusable: run {row.get('index')} {row.get('build')} "
                      f"exit {row.get('exitCode')}")

    # ---------------------------------------------------------- comparability
    boots = {row.get("bootId") for row in usable if row.get("bootId")}
    if not boots:
        print("  bootId          NOT RECORDED PER RUN -- cannot prove the "
              "machine did not restart mid-campaign")
    elif len(boots) > 1:
        problems.append(f"boot identity changed mid-campaign: {sorted(boots)}; "
                        "the campaign is invalid")
    else:
        print(f"  bootId          {boots.pop()} (constant)")

    counts = {len(row.get("generatedTokens") or []) for row in usable}
    sequences = {tuple(row.get("generatedTokens") or []) for row in usable}
    print(f"  token counts    {sorted(counts)}")
    if len(sequences) != 1:
        problems.append(f"{len(sequences)} distinct output sequences; the runs "
                        "are not comparable")
    elif sequences and list(next(iter(sequences))) != FROZEN_TOKENS:
        problems.append("output does not match the frozen token sequence")
    else:
        print("  output          identical, matches the frozen sequence")

    groups = {name: [row for row in usable if row["build"] == name]
              for name in (args.incumbent, args.candidate)}
    for name, group in groups.items():
        if not group:
            problems.append(f"no usable observations for {name}")
    if problems:
        return report(problems, {})

    # Equal mean position is what makes the two means comparable under drift.
    positions = {name: statistics.mean(row["index"] for row in group)
                 for name, group in groups.items()}
    imbalance = abs(positions[args.incumbent] - positions[args.candidate])
    print(f"  mean position   {args.incumbent} {positions[args.incumbent]:.2f}, "
          f"{args.candidate} {positions[args.candidate]:.2f} "
          f"(imbalance {imbalance:.2f})")
    if imbalance > 1.0:
        problems.append(
            f"positions are not balanced (imbalance {imbalance:.2f}); the later "
            "build is penalised by session drift and the means are not comparable")

    for name, group in groups.items():
        if len(group) < RECOMMENDED_PER_BUILD:
            print(f"  note            {name} has {len(group)} observations; "
                  f"{RECOMMENDED_PER_BUILD} per build is recommended for new "
                  "campaigns")

    # ------------------------------------------------------------- the gate
    print()
    print("  == primary gate: position-balanced mean TTFT regression ==")
    means = {name: statistics.mean(row["ttftMs"] for row in group)
             for name, group in groups.items()}
    for name, group in groups.items():
        values = sorted(row["ttftMs"] for row in group)
        deviation = statistics.stdev(values) if len(values) > 1 else 0.0
        print(f"    {name:<10} n={len(values)}  mean {means[name]:7.2f} ms  "
              f"sd {deviation:6.2f}  {values}")

    regression = (means[args.candidate] - means[args.incumbent]) / means[args.incumbent]
    print(f"    regression   {regression * 100:+.1f}%   allowed <= "
          f"{MAX_REGRESSION * 100:.1f}%")
    if regression > MAX_REGRESSION:
        problems.append(f"mean TTFT regression {regression * 100:.1f}% exceeds "
                        f"{MAX_REGRESSION * 100:.0f}%")

    print()
    print("  == hard gate: decode ==")
    decode_medians = {}
    for name, group in groups.items():
        values = sorted(row["decodeTokensPerSecond"] for row in group)
        median = statistics.median(values)
        decode_medians[name] = median
        inside = DECODE_LOW <= median <= DECODE_HIGH
        print(f"    {name:<10} median {median:6.3f} tok/s  {values}  "
              f"{'ok' if inside else 'OUTSIDE ' + str((DECODE_LOW, DECODE_HIGH))}")
        if not inside:
            problems.append(f"{name} decode median {median:.3f} is outside "
                            f"{DECODE_LOW}-{DECODE_HIGH} tok/s")

    # --------------------------------------------------------- diagnostics
    print()
    print("  == diagnostics: published, never a threshold ==")
    same, mixed = [], []
    for first, second in zip(usable, usable[1:]):
        delta = second["ttftMs"] - first["ttftMs"]
        label = f"{first['build']}->{second['build']}"
        if first["build"] == second["build"]:
            same.append((label, delta))
        else:
            signed = delta if second["build"] == args.candidate else -delta
            mixed.append((label, signed))
    print("    same-build adjacent (drift):")
    for label, delta in same:
        print(f"      {label:<9} {delta:+7.1f} ms")
    print(f"    oriented mixed adjacent (positive = {args.candidate} slower):")
    for label, delta in mixed:
        print(f"      {label:<9} {delta:+7.1f} ms")

    all_ttft = [row["ttftMs"] for row in usable]
    half = len(all_ttft) // 2
    if half:
        drift = statistics.median(all_ttft[half:]) - statistics.median(all_ttft[:half])
        print(f"    session drift, first half to second: {drift:+.1f} ms")
    over = [row["ttftMs"] for row in usable
            if row["ttftMs"] > HISTORICAL_TTFT_LIMIT_MS]
    print(f"    historical {HISTORICAL_TTFT_LIMIT_MS} ms line: {len(over)} of "
          f"{len(usable)} observations above it (observation, not a gate)")

    summary = {
        "incumbent": args.incumbent, "candidate": args.candidate,
        "means": means, "regression": regression,
        "maxRegression": MAX_REGRESSION,
        "decodeMedians": decode_medians,
        "sameBuildAdjacent": same, "orientedMixedAdjacent": mixed,
        "meanPositions": positions,
        "observations": len(usable), "failures": len(problems),
    }
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)
    return report(problems, summary)


def report(problems: List[str], summary: Dict[str, Any]) -> int:
    print()
    for problem in problems:
        print(f"    FAIL  {problem}")
    if not problems:
        print("  == paired campaign PASSED ==")
    else:
        print(f"  == paired campaign FAILED: {len(problems)} ==")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
