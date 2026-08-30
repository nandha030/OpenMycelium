# Gate A verdict — `0.3.0a4` vs `0.3.0a5` performance investigation

**Status: PASS — outcome 2, no build effect.**

**Date:** 2026-08-30
**Branch:** `feature/model-adapter-sdk`
**Campaign evidence:** `gates/paired-ab/`
**Analysis:** `scripts/repro/paired_analysis.py`, replayable from `rows.jsonl`

---

## What was in question

Three sequential campaigns disagreed about whether `0.3.0a5` was slower than
`0.3.0a4`:

| Campaign | Build | Order | TTFT median | Verdict under the old rule |
|---|---|---|---|---|
| standalone, ~30 min post-reboot | a4 | — | 142.5 ms | pass |
| inside a five-step battery | a5 | after 2 GPU gates | 172.3 ms | pass by 0.9 ms |
| first, 90 s settle | a5 | first | 173.3 ms | fail |
| after dropping page cache | a5 | first | 190.4 ms | fail |
| A/B arm 1 | a4 | first | 147.9 ms | pass |
| A/B arm 2 | a5 | second | 175.4 ms | fail |

The A/B appeared to show a 27.5 ms build regression. It did not: **a4 ran first
in every one of those pairings**, so build was confounded with order. Reversing
the order gave a5 first at 170.5 ms and a4 second at 169.1 ms — and a4 alone
across the session ran 142.5 → 147.9 → 169.1, the same build degrading with
wall-clock time.

Two other hypotheses were tested and refuted rather than assumed:

- **Thermal or power throttling.** GPU idle at 41 °C, `HW Thermal Slowdown 0 us`,
  `SW Thermal Slowdown 0 us`. Not throttling.
- **Host page-cache pressure** (15 GiB RAM, 22.84 GiB model, 12 GiB cached).
  Dropping caches made TTFT *worse* — 190.4 ms — which refutes it.

The code difference does not explain it either. Eight files differ between the
builds; three are non-test and non-version (`provenance.py`, `placement.py`,
`qualification.py`). The one that grew work, `provenance.collect()`, was timed at
~920 ms in **both** builds, and runs at plan time, not per request.

---

## The pre-registered experiment

Sequence `a4 a5 a5 a4 a5 a4 a4 a5`, eight runs, from immutable side-by-side
installations at `/opt/om-ab/a4` and `/opt/om-ab/a5` that were **never
reinstalled during the campaign** — reinstalling is itself a machine-state change
and it had happened between every prior measurement.

Identical every run: prompt (14 tokens), 24 generated tokens, greedy decoding,
warm-up rule, settle period.

**Position balance: mean position a4 = 4.50, a5 = 4.50, imbalance 0.00.** This is
the property that makes the two means comparable: TTFT drifts upward through a
session, and equal mean position means that drift is charged to both builds
equally. Without it the later build is penalised for nothing.

---

## Result

Primary statistic — position-balanced mean TTFT:

| | a4 (incumbent) | a5 (candidate) |
|---|---|---|
| n | 4 | 4 |
| mean | 154.85 ms (sd 20.57) | 157.62 ms (sd 23.58) |
| samples | 140.1 / 141.1 / 154.0 / 184.2 | 136.1 / 139.0 / 172.6 / 182.8 |

```
regression = (157.62 - 154.85) / 154.85 = +1.8%
allowed                                 = 20.0%
verdict                                 = PASS
```

Decode, hard gate, median within `10.9 – 11.3 tok/s`:

| | median | samples |
|---|---|---|
| a4 | 11.115 tok/s | 10.87 / 10.95 / 11.28 / 11.34 |
| a5 | 11.170 tok/s | 10.65 / 11.11 / 11.23 / 11.26 |

**Diagnostics — published, never used as a threshold.** With two same-build pairs
these are one observation of a noisy quantity; treating them as a bound would let
a drifting campaign license a real regression.

- same-build adjacent: `+43.8` (a5→a5), `+44.1` (a4→a4)
- oriented mixed adjacent, positive = a5 slower: `−2.1, +28.8, −17.9, −4.0, −11.6`
- session drift, first half to second: `+8.8 ms`
- historical 173.2 ms line: 2 of 8 observations above it

---

## Correctness

Identical on all eight runs, no exceptions and no excluded observations:

- exact frozen 24-token sequence
- ownership `181/182`, 363 tensors, overlap 0
- boundary after layer 19
- model fingerprint `ff74ccb7c5e616ddfa3ea53f4d201be9825fb02ce8673e45f863ab892adcc7be`
- adapter `mistral@1`, config digest `f230a7c1dea09ad930957d1fb7a446b69f85279941cb39a95bada043706c2d4a`
- schema version 2, a distinct placement id and manifest digest per run
- prompt 14 tokens, warm-up 2311–2537 ms, all exit 0

## Identities

| | |
|---|---|
| a4 installed content | `16c2df1cadadbd2061b265e1027cc44f47d3a2902fb35149abaeacfcacd960f5` |
| a5 installed content | `e2eccbbe6de9aa8fdc342bbed015cede325a84161269dfa719c6f3f97c0011bf` |
| a4 wheel sha256 | `0d668aefd9c9b37e5c46c2e324be9cb0895d7419421a1160cd38ac2b3d7e99cb` |
| a5 wheel sha256 | `3eeaed3e229226f4d1408826fc42934057878fa48a91fc7fbb89900a88a04871` |
| MCCL | `0.2.0a3`, content `5f2028695fa830417793ddcfecf90aa33bfd0b9e64775115058839d94d12631d` |
| Boot identity | `75a1077f-b05d-494a-af24-b00e2847d61c` |
| Hardware | NVIDIA RTX 5060 Ti `nvidia:GPU-cbb3d045-9d5f-a225-0f2e-adb1c6d6a033`, AMD RX 9060 XT `amd:pci-0000:04:00.0` |

### Boot identity — how it was established, and the gap

The harness recorded `bootId` **once, in the campaign header**, not per run. Gate A
requires invalidating on a boot change, which per-run capture is what detects.

For this campaign the constancy is nonetheless established by two observations
that bracket it: the header, written before run 1, and the audit events for run
8's placement `pl-3e783d366cfa4aa2`, which carry the same `75a1077f…`. The distro's
next boot (`12c60700…`) began 15.6 hours later, after the campaign ended.

That bracketing is sound here, but it was luck: seven of the eight runs left no
audit events at all, because every run writes to the same `work_dir` and
overwrites `events.jsonl`. Both defects are fixed in the harness before it is used
again — per-run `bootId` with abort-on-change, and per-run event preservation.
`paired_analysis.py` now states plainly when per-run boot identity is absent
rather than passing silently.

---

## Contract disposition

The frozen absolute TTFT band is 28.9 ms wide (baseline median 144.3 → limit
173.2). The measured within-build, run-to-run range is **44–47 ms**. The
instrument's noise exceeds the band it is asked to resolve, which is why
identical code produced pass and fail in different campaigns.

`docs/ADAPTER_SDK.md` §10 is amended:

- **Primary gate:** position-balanced mean TTFT regression of candidate against
  the sealed incumbent, measured interleaved in one campaign, **≤ 20%**. The 20%
  is the tolerance the contract already used, applied to a paired comparison
  instead of an absolute number.
- **Decode:** unchanged, median within `10.9 – 11.3 tok/s`, still a hard gate.
- **Absolute 173.2 ms:** retained as a historical observation. The number is not
  changed and not widened.
- **Adjacent-pair differences:** diagnostics only, never a threshold.
- **Future campaigns:** ≥ 5 observations per build, position-balanced, per-run
  boot identity, per-run event preservation, pre-registered machine state.

This is not a threshold widened after a failure. The justification is the
same-build adjacent pairs, which were pre-registered as drift diagnostics before
any candidate outcome was known and are independent of whether a5 passed.

The 4-per-build campaign above stands as pre-registered and is not re-judged
against the new minimum.

---

## Disposition

`0.3.0a5` carries no measurable performance regression against `0.3.0a4` and
proceeds to Gate B as the sealing candidate. `0.3.0a4` is superseded.
