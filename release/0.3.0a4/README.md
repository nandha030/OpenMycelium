# 0.3.0a4 — Model Adapter SDK, placement schema v2

Wheel sha256 `0d668aefd9c9b37e5c46c2e324be9cb0895d7419421a1160cd38ac2b3d7e99cb`,
installed content sha256 `16c2df1cadadbd2061b265e1027cc44f47d3a2902fb35149abaeacfcacd960f5`,
MCCL `0.2.0a3` unchanged.

Six earlier wheels were built during this milestone and are recorded as
non-releasable in [docs/NONRELEASABLE_BUILDS.md](../../docs/NONRELEASABLE_BUILDS.md),
with the reason each one failed. This is the first that passed every gate.

## What this evidence is for

The milestone's claim is that it changes what the runtime **refuses**, not what
it **computes**. That claim is only checkable against a measurement of the
runtime before the change, so `baseline/` was captured at `d76334a` — the merge
commit, before any adapter code existed — and `refactor/` is the same capture
script run against `0.3.0a4` on the same machine.

`gates/comparison.txt` asserts both halves: what must be identical, and what
must have changed. Either one failing alone would mean the milestone did not do
what it says.

## Contents

| Path | What it is |
|---|---|
| `baseline/` | The pre-refactor capture at `d76334a` |
| `refactor/` | The same capture against `0.3.0a4` |
| `gates/comparison.txt` | Baseline vs refactor, asserted both ways |
| `gates/unit-suite.txt` | Every unit test in the repository, one verdict |
| `gates/adapter-refusal-summary.txt` | Both refusal paths, measured on hardware |
| `gates/qualification-lifecycle.txt` | The six-state qualification sequence |
| `gates/qualification-record.json` | The record the passing gate wrote |
| `gates/qualification-evidence.json` | The run that record is backed by |
| `gates/console-wheel-gate.txt` | The installed-wheel console gate |

## Results

| Gate | Result |
|---|---|
| Unit suite | 263 tests, 0 failures |
| Adapter refusal (planning + direct worker) | 0 failures |
| Qualification lifecycle (six states) | 0 failures |
| Installed-wheel console gate | 9 passed, 0 failed |
| Baseline comparison | passed |

### Identical to the baseline

Generated token ids, decoded text, model fingerprint
`ff74ccb7c5e616ddfa3ea53f4d201be9825fb02ce8673e45f863ab892adcc7be`, boundary
after layer 19, transport `host-staged-xvendor`, 10240 activation bytes per
token, 363 tensors, 24495564800 weight bytes, boundary byte digest
`c1467cd33c52032932ae4a39661a8136`, exclusive ownership `[181, 182]` with
overlap 0.

### Changed, as intended

Manifest schema `1 → 2`; manifest digest `48cb459d… → 2bae2059…`; the manifest
now pins `mistral@1` with `adapterConfigDigest`
`f230a7c1dea09ad930957d1fb7a446b69f85279941cb39a95bada043706c2d4a`.

### Timings

Reported, not asserted. TTFT and decode rate vary between single runs on a
machine that is also driving a display, and this project does not quote a
distribution from a handful of observations. The throughput campaign is the
instrument for that; a single run is not.

## Two notes on how this evidence was produced

**The console gate inside `refactor/gate.txt` was refused, not failed.** The
operator's own console held port 11501 at the time, and the gate declines to run
against a process it did not start rather than testing the wrong binary — a
guard added after that exact mistake was made once. It was re-run standalone
afterwards and is in `gates/console-wheel-gate.txt`; the gate now also accepts
`OM_CONSOLE_PORT` so it can run beside a console in use.

**The qualification record survived a reboot.** It was written before the
machine restarted and still resolved as `HARDWARE_QUALIFIED` afterwards, with
the same situation digest `9f64219e2479bc1e…`. That is the intended behaviour:
the tuple is scoped to the checkpoint, the build, the runtimes and the device
pair, none of which a reboot changes. Boot identity is deliberately not part of
it — requalifying after every restart would make qualification meaningless.
