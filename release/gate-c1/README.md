# Gate C.1 — Safety Governor contract, frozen

**Status: CLOSED.** Contract and failing tests only. No runtime behaviour, no
integration, no GPU.

## Two identities, deliberately not the same thing

| Identity | Covers | Status |
|---|---|---|
| `safety-contract-1` | states, transitions, semantics, audit shape | **FROZEN** |
| `safety-policy-1-provisional` | the threshold values in §14 | **PROVISIONAL** |

The semantics were reviewed against ten blockers and are settled. The numbers
were reasoned from the qualified hardware — 16 GiB cards, ~11.4 GiB stages,
22.84 GiB weight loads taking minutes — and **not derived from observed
failures, because none have been observed.**

`-provisional` is in the version string rather than a footnote, so every audit
event carrying it says so and no record can later be read as though the limits
had been qualified. A policy version becomes non-provisional only when its values
are backed by measurement.

## Evidence

| Claim | File | Result |
|---|---|---|
| Contract-data tests pass | `contract-tests.txt` | **48 tests, PASS** |
| Document table matches canonical data | `contract-tests.txt` | **27 rows present and identical** |
| Expected-red detects only the missing implementation | `contract-tests.txt` | **red for the right reason** |
| Existing suite stays green | `unit-suite.txt` | **263 tests, 0 failures** |
| No GPU touched | `no-gpu-imports.txt`, `gpu-before.txt`, `gpu-after.txt` | **no GPU library imported** |
| No runtime path wired | below | **no caller exists** |

### On "no GPU was touched"

VRAM moved 1100 → 1097 MiB and temperature 38 → 36 °C across the run. Those are
the display compositor and the card cooling, not this code — and citing them as
proof would be citing noise.

The load-bearing evidence is static: **`runtime/safety/` imports no GPU library
at all.** The complete import set across all four modules is `__future__`,
`dataclasses`, `enum`, `typing`, `os`, `re`, `sys`, `unittest`, and the local
`contract` and `governor` modules. No `torch`, `pynvml`, `nvidia`, `rocm`,
`cupy` or `pycuda`. No `subprocess`, no `os.system`, no `nvidia-smi` or
`rocm-smi` invocation — the single textual mention of `rocm-smi` is inside a
comment explaining why AMD power telemetry is absent under WSL.

The code cannot touch a GPU, because it imports nothing that can.

### On "no runtime execution path was wired"

`runtime/safety/governor.py` does not exist. Nothing in `runtime/cli/`,
`runtime/serving/` or `runtime/scheduler/` imports anything from
`runtime/safety/`. The 263-test suite is byte-for-byte the same suite that
passed before this work, which is the check that would fail first if a call site
had appeared.

## Policy v1 provides no thermal and no power protection

Stated plainly, because the opposite is easy to assume of anything called a
safety governor.

`power`, `temperature` and `throttle_reasons` are **recorded and never acted
on** — on every platform, NVIDIA included, where they are in fact available.

The reason is the qualified hardware. The AMD RX 9060 XT under WSL has **no
power or thermal signal at all**: `rocm-smi` requires the `amdgpu` kernel module
and WSL exposes `/dev/dxg` instead. The fabric layer already reports this
correctly as `power_source: "unavailable"` with `null` values, never zero. A
policy acting on thermal data would therefore protect one card and not the
other, while describing itself as thermal protection.

Declared as data in `contract.ADVISORY_ONLY_SIGNALS` and asserted by
`test_contract_data.py`, so the boundary is checkable rather than remembered.

Firmware, driver and physical protections remain the only thermal authority.
§0 of the contract states the broader non-goal: the Governor reduces
software-induced risk and cannot guarantee hardware will never fail.

## The expected-red arrangement

`runtime/safety/` is deliberately outside `run_unit_suite.sh` — its behaviour
tests fail by design until Gate D, and adding them would turn the suite red and
block everything behind it.

That exclusion is a debt, not a decision, because the suite's own rule is that a
group reporting zero failures must not silently skip anything. It is made
visible by `scripts/repro/contract_tests.sh`, which asserts the *expected*
outcome in both directions: the data tests must pass, and the behaviour tests
must fail **only** with `No module named 'governor'`. If they start passing, an
implementation arrived without the packaging and suite debts being paid, and the
command fails so that is noticed.

## Contents

| Path | What it is |
|---|---|
| `contract-tests.txt` | table check, 48 data tests, expected-red verification |
| `unit-suite.txt` | the unchanged 263-test suite |
| `no-gpu-imports.txt` | the complete import set and the absence of GPU libraries |
| `gpu-before.txt`, `gpu-after.txt` | device state either side, for completeness |

Contract: [`docs/SAFETY_GOVERNOR.md`](../../docs/SAFETY_GOVERNOR.md).
Canonical data: [`runtime/safety/contract.py`](../../runtime/safety/contract.py).
