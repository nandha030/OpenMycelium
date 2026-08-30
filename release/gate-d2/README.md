# Gate D.2 — shadow-mode integration

**Status: SEALED. The canonical artifact is `0.3.0a10`, recorded in
[`seal/`](seal/README.md) — read that first.**

This file is the review record for candidate **`0.3.0a9`**, which is
**non-releasable**: its observations carried no schema version, run id,
placement id or timestamp, so they could not be correlated with the audit trail
of the run they described. `seal/` records the build that carries them, the
identities D.2 was missing, and the scope review.

Two statements below are corrected in `seal/`: the byte-exact boundary was not
measured under shadow mode, and the `off` path had lost a statement it promised
to keep. Both are left in place here rather than edited, because a review record
that is quietly corrected after the fact is not a record.

Contract `safety-contract-1.1`, policy `safety-policy-1-provisional`.

## What shadow mode is, and is not

It watches a real run and records what the Governor *would* have done. That
evidence is only worth having if shadow mode provably cannot act, so the
observer is not given the ability rather than merely instructed not to use it:

- `observe()` **returns nothing**. A return value is how an observer becomes a
  decision-maker — the first caller to branch on it turns shadow mode into
  enforcement without anyone deciding to.
- The Governor it drives is constructed with a **no-op actuator** and a
  **throwaway in-memory quarantine store** that does not outlive the call, so it
  cannot write a production record even by mistake.
- The class exposes no `admit`, `refuse`, `drain`, `terminate`, `quarantine`,
  `release`, `enforce`, `veto` or `report_incident` — asserted by a test over
  its public surface.
- Observations are written to their **own file**, `safety-shadow.jsonl` in the
  run's work directory, never the production audit trail.

**Shadow-mode safety is not enforcement.** Bounded canary enforcement is a
separate milestone requiring separate authorization.

## `off` is the previous code path

`safetyMode` is `off` unless `OM_SAFETY_MODE=shadow`. An unrecognised value —
including `enforce` — resolves to `off`, deliberately: a typo in an environment
variable must not stop a production run.

With the flag unset the integration is **one dictionary lookup and a return**.
No `sys.path` change, no import, nothing loaded. An earlier revision imported
the shadow module before checking the mode; importing a module to discover you
are switched off is already a difference, and "preserves current behaviour
exactly" has to mean exactly.

The measured run confirms it: the `off` arm produced **no `safety-shadow.jsonl`
and no observation lines**.

## Bounded polling

The observer issues **no device query of its own**. It consumes the Fabric
snapshot the coordinator has already taken, so it cannot contend with the
workload it is watching, whatever the interval. That is checked on the parsed
import graph — no `subprocess`, `torch`, `pynvml`, `cupy` or `pycuda` — rather
than on the source text, so a mention in a comment does not fail it and a real
import cannot hide in one.

A `MIN_POLL_INTERVAL_SECONDS = 5.0` floor bounds repeat observation on top of
that. A test drives 50 back-to-back calls and asserts exactly one observation.

## Hardware smoke — one paired 24-token run

Run after the synthetic tests passed, on the qualified machine.

| Invariant | `off` | `shadow` | Equal |
|---|---|---|---|
| Frozen 24-token sequence | matches | matches | yes |
| Decoded text | — | — | yes |
| Ownership | `[181, 182]` | `[181, 182]` | yes |
| Tensors / overlap | 363 / 0 | 363 / 0 | yes |
| Boundary layer | 19 | 19 | yes |
| Model fingerprint | `ff74ccb7c5e6…` | `ff74ccb7c5e6…` | yes |

**Byte-exact boundary** under shadow mode: two transfers, sent = received =
`c1467cd33c52032932ae4a39661a8136`, `byteExact=True`.

**Zero orphan workers.** **VRAM returned** — 1046 MiB against a ~1073 MiB
baseline.

The observation recorded during the run:

```
admission: would none (READY->ADMITTED) -- admission would be granted
```

carrying `safetyMode=shadow`, `safetyContractVersion=safety-contract-1.1`,
`safetyPolicyVersion=safety-policy-1-provisional`, `safetyEnforced=false`, and
per-device signals with confidence alongside every value.

**AMD power reads `null` with `UNAVAILABLE_EXPECTED`, never zero.** NVIDIA power
reads 23.72 W with `AVAILABLE`. The distinction survives the integration, which
is the thing most likely to be lost when telemetry passes through an adapter.

## Overhead: below the measurement floor, not measured

Two paired observations, and **they disagree in sign**:

| Build | `off` TTFT | `shadow` TTFT | Delta |
|---|---|---|---|
| `0.3.0a8` | 175.5 ms | 145.1 ms | **−30.4 ms** (shadow faster) |
| `0.3.0a9` | 174.3 ms | 184.0 ms | **+9.7 ms** (shadow slower) |

Both deltas sit well inside the 44–47 ms within-build run-to-run range
established by the Gate A paired campaign. Two observations that disagree in
sign do not bound an overhead; they show it is smaller than this instrument can
resolve.

**Reported, and no threshold is changed from it.** Bounding shadow-mode overhead
properly needs a position-balanced paired campaign of the kind Gate A defines —
`off` and `shadow` interleaved, five observations per arm — which is not part of
this milestone.

## No control surface

`OM_SAFETY_MODE` is a development environment variable. It is deliberately not a
CLI option, a config key or a console control: shadow mode exists to gather
evidence, and a surface that invites operators to turn it on invites them to
expect it to do something.

## Qualification note

The smoke ran under an explicit `OM_QUALIFICATION_MODE=override` with actor
`d2-shadow-smoke`, recorded in the audit trail. `0.3.0a9` is a new build, so its
content digest does not match the qualified record — the tuple refusing it is
the qualification mechanism working, and the override is the audited escape
rather than a way around it.

## Artifacts

| Version | Disposition |
|---|---|
| `0.3.0a7` | Gate D.1 candidate; canonical wheel preserved |
| `0.3.0a8` | **non-releasable** — shadow observations reached stderr but were not persisted |
| `0.3.0a9` | this candidate, wheel `4f78d005fc4a1e1c5fc8299082a0351eb0dd028045928e221ae33cd970d2ca79`, installed content `3e8557ff4bd70b5ffa086293d5072b26f268223ae38a138d4765a3da3677e084` |

## Tests

437 unit tests, 0 failures, Safety group included — 174 safety tests of which 33
are shadow mode.

## Contents

| Path | What it is |
|---|---|
| `run-off.json`, `run-off.err` | the `off` arm, producing no observations |
| `run-shadow.json`, `run-shadow.err` | the `shadow` arm |
| `safety-shadow.jsonl` | the persisted observation |
| `boundary.txt` | byte-exact boundary under shadow mode |
| `gpu-after.txt` | device state after both runs |

## Not done, and not authorized

No enforcement. No worker or CLI or API or console integration beyond the one
coordinator observation point. No Memory Fabric, pager, MHub or training.
