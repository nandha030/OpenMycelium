# 0.3.0a5 — Adapter SDK baseline, sealed

| | |
|---|---|
| Installed content | `e2eccbbe6de9aa8fdc342bbed015cede325a84161269dfa719c6f3f97c0011bf` |
| Canonical wheel sha256 | `3eeaed3e229226f4d1408826fc42934057878fa48a91fc7fbb89900a88a04871` |
| MCCL | `0.2.0a3`, content `5f2028695fa830417793ddcfecf90aa33bfd0b9e64775115058839d94d12631d` |
| Placement schema | 2 |

`0.3.0a5` is `0.3.0a4` plus one change: the MCCL content digest in the
qualification tuple. `0.3.0a4` recorded only `mcclVersion`, which left
qualification-by-label open in the layer that decides what crosses the activation
boundary.

## Identity is installed content, not wheel bytes

Rebuilding this version from the same committed source produced wheel files
hashing `3eeaed3e…`, `495a3fde…` and `b99adac0…` — three byte streams at identical
size, differing in ZIP timestamps and packaging metadata. All three install to the
same `installedContentSha256`.

So the wheel that the Gate A paired campaign actually measured is preserved as
canonical, and a rebuild is used only to confirm the digest, never to replace it.
Equal installed-content digests mean the product content is identical; they do
**not** mean the wheels are byte-identical, and are not described that way.

## Gate A — performance attribution

**PASS, outcome 2: no build effect.** See [`gates/gate-a-verdict.md`](gates/gate-a-verdict.md).

Eight runs, `a4 a5 a5 a4 a5 a4 a4 a5`, immutable side-by-side installations never
reinstalled during the campaign, position balance 0.00.

```
a4 mean TTFT   154.85 ms (sd 20.57)
a5 mean TTFT   157.62 ms (sd 23.58)
regression     +1.8%   allowed <= 20%
```

Three earlier campaigns disagreed because a4 was always measured first, so build
was confounded with order. Thermal throttling and host page-cache pressure were
both tested and refuted; dropping caches made TTFT *worse*.

The absolute 173.2 ms TTFT line is now a historical observation rather than a
gate: its band is 28.9 ms wide and the measured within-build run-to-run range is
44–47 ms, so it cannot resolve what it was asked to. The number is unchanged and
not widened. Decode remains a hard gate and both builds pass it.

## Gate B — sealing battery

| Gate | Result |
|---|---|
| Unit suite | 263 tests, 0 failures, no group skipped |
| Adapter refusal (planning + direct worker) | PASS |
| Qualification lifecycle (six states) | PASS |
| Baseline comparison vs `d76334a` | PASS |
| Performance | replayed from the Gate A record, PASS |
| Installed-wheel console gate | PASS (inside the campaign sessions) |

Performance was **replayed, not re-measured**. The Gate A campaign already
measured this exact installed content; taking a second sample of a noisy
instrument and presenting it as confirmation would add nothing.

### Correctness, identical to baseline

Exact frozen 24-token sequence; ownership `181/182`, 363 tensors, overlap 0;
boundary after layer 19; boundary byte digest
`c1467cd33c52032932ae4a39661a8136`; fingerprint `ff74ccb7c5e6…`; zero orphan
workers; VRAM returned to baseline.

### Changed, as intended

Placement schema 1 → 2; `mistral@1` pinned with `adapterConfigDigest`
`f230a7c1…`; the qualification tuple gains `mcclContent`.

## Contents

| Path | What it is |
|---|---|
| `gates/gate-a-verdict.md` | The written Gate A verdict and contract disposition |
| `gates/paired-ab/` | The eight-run raw record, per-run JSON, and campaign log |
| `gates/performance-replay.json` | The replayed analysis of that record |
| `gates/unit-suite.txt` | 263 tests |
| `gates/refusal.txt`, `gates/lifecycle.txt`, `gates/comparison.txt` | Hardware gates |
| `gates/qualification-record.json` | The ledger's adapter records |
| `gates/harness-smoke/` | Two-run proof that per-run evidence is now preserved |
| `baseline/`, `refactor/` | Pre-refactor capture at `d76334a`, and this build |

## Harness defects this milestone fixed

The Gate A campaign preserved the audit evidence of exactly one of its eight
runs: every run wrote to the same `work_dir` and overwrote `events.jsonl`. That
the survivor happened to be the run whose boot identity needed proving was luck.

Each run now gets its own `work_dir`. `gates/harness-smoke/` shows two runs each
retaining their own `events.jsonl`, `placement.json` and both worker logs. Boot
identity is captured per run and the campaign aborts if it changes, rather than
that being discovered afterwards.

## Status

Sealed candidate, gates passed. **Not merged and not tagged** — both require
explicit owner authorization.
