# Gate D.1 — deterministic Safety Governor core

**Status: CLOSED.** Contract `safety-contract-1.1`, candidate `0.3.0a7`.

Library only. No coordinator, worker, CLI, API or console integration; that is
Gate E and is not authorized.

## The amendment

`safety-contract-1` had no incident path out of `ADMITTED` — the window between
capacity being reserved and the compute canary passing, which is where
allocation and weight-load setup happen and therefore where an OOM is most
likely. A worker dying there, or a breaker opening on an incident recorded
there, had nowhere to go.

The gap was found by the implementation refusing to guess: `SafetyGovernor`
raised `GovernorError` naming the permitted triggers rather than routing the
incident through a neighbouring transition. That refusal *was* the report.

`safety-contract-1.1` adds four rows and keeps `safety-contract-1` in the
revision history rather than reinterpreting it — an audit event carries the
version in force when it was written.

| From | To | Trigger | Drain | Lease |
|---|---|---|---|---|
| `ADMITTED` | `FAILED` | `worker_died` | — | released |
| `ADMITTED` | `FAILED` | `unresponsive` | — | released |
| `ADMITTED` | `DRAINING` | `hard_limit_breached` | `immediate` | released |
| `ADMITTED` | `QUARANTINED` | `breaker_opened` | — | released |

**Every exit from `ADMITTED` now declares a lease outcome** — `retained` for
`canary_passed`, where the work is starting and the capacity is still needed;
`released` for all seven others. A lease that is neither carried forward nor
released is stranded, and a stranded lease is capacity the Governor books
forever.

The outcome is applied **under the same revision as the state change**. Doing it
afterwards leaves a window in which the state says the work is over and the
capacity is still booked — and a process dying in that window strands the lease
permanently.

## Gates

| Gate | Result | File |
|---|---|---|
| Canonical contract data | **50 tests, PASS** | `contract-tests.txt` |
| Safety behaviour | **91 tests, PASS** | `contract-tests.txt` |
| Generated table vs document | **31 rows identical** | `contract-tests.txt` |
| Full unit suite, Safety included | **404 tests, 0 failures** | `unit-suite.txt` |
| Installed wheel, neutral directory | **129 tests, 1 skip** | `installed-wheel.txt` |
| Static no-GPU / no-subprocess | **clean** | `static-verification.txt` |
| Integration | **no caller anywhere** | `static-verification.txt` |

### The one skip, positively identified

```
skipped 'prose-vs-data consistency is a property of the repository. An installed
wheel ships no document for the data to drift from, so there is nothing here to
check. The repository suite runs these.'
```

It is not a skip-on-file-missing, which is how a gate comes to pass because its
evidence vanished — this project has shipped one of those. The installed context
is proven **positively** before anything is skipped: the module path is under
`site-packages` **and** a real package `__init__.py` sits above it. A missing
document anywhere else is a hard assertion failure.

### No GPU, no subprocess, no signal

`runtime/safety/` imports no `torch`, `pynvml`, `nvidia`, `rocm`, `cupy` or
`pycuda`; no `subprocess`, `os.system`, `os.popen`, `os.kill` or `signal`. The
complete import set is `__future__`, `dataclasses`, `enum`, `typing`, `hashlib`,
`json`, `os`, `re`, `statistics`, `sys`, `unittest`, plus the local modules.

Termination is proven through `FakeProcessActuator`, which records what would
have been signalled and delivers nothing. That is the only way to test "we
refused to kill the wrong process" without occasionally killing the wrong
process.

### No integration

Nothing in `runtime/cli/`, `runtime/serving/`, `runtime/scheduler/`,
`runtime/fabric/` or `runtime/mycelium/` imports the Governor, and
`launcher.py` does not put `safety` on the path. It is a library with no callers.

## Artifacts

| Version | Disposition |
|---|---|
| `0.3.0a5` | sealed and tagged `v0.3.0a5`; **untouched** |
| `0.3.0a6` | **non-releasable** — shipped the Governor on `safety-contract-1` with the `ADMITTED` gap |
| `0.3.0a7` | this candidate, on `safety-contract-1.1` |

`0.3.0a6` is recorded in [docs/NONRELEASABLE_BUILDS.md](../../docs/NONRELEASABLE_BUILDS.md)
with its hash and reason.

The wheel file hash differs per build from ZIP metadata; identity is the
installed-content digest, as established at `v0.3.0a5`.

## The five tests the amendment required

| Requirement | Test |
|---|---|
| Worker death cannot strand a lease | `test_worker_death_cannot_strand_a_lease` |
| Hard-limit drain uses immediate mode | `test_hard_limit_while_admitted_drains_in_immediate_mode` |
| Breaker opening prevents another admission | `test_a_quarantine_prevents_another_admission` |
| Duplicate/stale triggers cannot release a newer lease | `test_a_stale_trigger_cannot_release_a_newer_lease` |
| All new rows match the generated documentation | `test_every_transition_row_appears_in_the_document` |

Plus `test_every_admitted_exit_declares_a_lease_outcome`, which fails if a future
row is added to `ADMITTED` without saying what becomes of the lease.
