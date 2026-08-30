# Mycelium Safety Governor — contract

**Status: `safety-contract-1.1`, FROZEN at Gate D.1.** Two identities that are
deliberately not the same thing.

| Identity | Covers | Status |
|---|---|---|
| `safety-contract-1.1` | states, transitions, semantics, audit shape | **FROZEN** — changing it needs the same review this had |
| `safety-policy-1-provisional` | the threshold *values* in §14 | **PROVISIONAL** — proposed defaults, not qualified limits |

### Revision history

Earlier versions are preserved, not reinterpreted. An audit event carries the
contract version in force when it was written, and reading it against a newer
table would silently misjudge the decision it records.

| Version | Change |
|---|---|
| `safety-contract-1` | Frozen at Gate C.1. `ADMITTED` had **no incident path**: a worker dying between admission and the canary, or a breaker opening on an incident recorded there, had nowhere to go. A Governor carrying this version raised in that window rather than inventing a transition. |
| `safety-contract-1.1` | Closes that gap. Adds `ADMITTED → FAILED` on `worker_died` and `unresponsive`, `ADMITTED → DRAINING` on `hard_limit_breached` in `immediate` mode, and `ADMITTED → QUARANTINED` on `breaker_opened`. Every exit from `ADMITTED` now declares what becomes of its lease. |

The gap was found by the implementation refusing to guess, not by review — which
is the argument for making a state machine raise rather than pick a plausible
target.

The split matters. The semantics were reviewed against ten blockers and are
settled. The numbers were reasoned from the qualified hardware — 16 GiB cards,
~11.4 GiB stages, 22.84 GiB weight loads taking minutes — and **not derived from
observed failures, because none have been observed**. `-provisional` is in the
version string rather than a footnote so that every audit event carrying it says
so, and no record can later be read as though the limits had been qualified.

A policy version becomes non-provisional only when its values are backed by
measurement on hardware. That is Gate E or later work with its own evidence.

The deterministic core is implemented at Gate D.1 against synthetic telemetry,
an injected clock and a fake actuator. It has no callers: integration is Gate E.

The Scheduler proposes. The Governor may veto. Nothing else in this milestone.

---

## 0. Non-goal, stated first

**The Governor reduces software-induced risk. It cannot guarantee that hardware
will never fail.**

Firmware, driver and physical protections remain authoritative and act on
timescales the Governor cannot: a thermal trip, a power-delivery fault or a
driver-level reset happens whether or not this software agrees. The Governor
sits above those and can only decline to start work, stop work it started, and
refuse to start again until an operator intervenes.

Specifically it does **not** claim to prevent: GPU hardware failure, VRAM bit
errors, driver crashes or resets, firmware faults, power-supply events, thermal
damage, or damage from workloads started outside OpenMycelium. It claims only
that *this* runtime will not knowingly start work it cannot measure, will stop
work that has stopped progressing, and will not retry into the same failure.

A Governor that passed every check is not evidence that hardware is healthy. It
is evidence that this software found no reason to refuse.

---

## 1. Scope

In scope: admission control, health monitoring, drain, cooldown, circuit
breaking, quarantine, manual reset, and the audit record of every decision.

Not in scope, and not in this branch: Memory Fabric, paging, virtual heap,
scheduling optimisation, training, MHub. The Governor decides *whether* work
runs; it never decides *how* work is placed.

### Handoff debts — paid at Gate D.1

Both were silent failures if forgotten, so they were written down and are now
checked by `scripts/repro/contract_tests.sh` rather than remembered.

**Packaging.** `runtime/safety/` is in `scripts/build_openmycelium_wheel.py`'s
`INCLUDE`. Without it the Governor passes every test from a checkout and is
absent from every wheel.

**Test suite.** `runtime/safety/` is in `scripts/repro/run_unit_suite.sh`'s
`UNITTEST_DIRS`, and its tests are required-green. Through Gate C.1 they were
expected-red — the contract existed and the implementation did not — and that
exclusion was a debt rather than a decision, because the suite's own rule is
that a group reporting zero failures must not silently skip anything. The suite
moved from 263 to 395 tests when the debt was paid.

### Still not integrated

The Governor is a library with no callers. Nothing in `runtime/cli/`,
`runtime/serving/` or `runtime/scheduler/` imports it, and `launcher.py` does not
put `safety` on the path. Wiring it into the coordinator, workers, CLI, API or
console is Gate E and is not authorized.

---

## 2. States

```
UNKNOWN      nothing has been established about this machine yet
READY        preflight passed; may admit work
ADMITTED     admission granted, resources reserved, not yet computing
RUNNING      a compute canary succeeded; real work in progress
DRAINING     stopping deliberately, bounded by a deadline
COOLDOWN     work stopped; waiting for the machine to return to baseline
QUARANTINED  refusing all work until an operator resets, with evidence
FAILED       an incident occurred and has been recorded
```

`UNKNOWN` is the start state and is entered again whenever the boot domain
changes, because nothing measured in a previous boot describes this one (§10).

`FAILED` is a recording state, not a resting one: it always advances to
`COOLDOWN` or `QUARANTINED`. A Governor left sitting in `FAILED` would be
indistinguishable from one that had crashed.

---

## 3. Transition table

`Source` is who reports the trigger: `governor` (it noticed), `operator` (a
human asked) or `worker` (it reported a fact about itself). It is never who
applies the transition — see *Source is not executor* below.

**Generated from `runtime/safety/contract.py`.** Do not edit by hand:
`scripts/repro/render_safety_table.py` prints it and `--check` verifies the
document against the data. `test_contract_data.py` asserts both directions, so a
stale paste is a test failure rather than a discrepancy nobody notices.

| From | To | Trigger | Source | Timeout | Drain | Lease | Audit event |
|---|---|---|---|---|---|---|---|
| `UNKNOWN` | `READY` | `preflight_passed` | governor | `preflight_deadline_seconds` | — | — | `safety_ready` |
| `UNKNOWN` | `FAILED` | `preflight_failed` | governor | — | — | — | `safety_preflight_failed` |
| `UNKNOWN` | `QUARANTINED` | `quarantine_restored` | governor | — | — | — | `safety_quarantine_restored` |
| `READY` | `ADMITTED` | `admission_granted` | governor | `admission_deadline_seconds` | — | — | `safety_admitted` |
| `READY` | `READY` | `admission_refused` | governor | — | — | — | `safety_admission_refused` |
| `READY` | `QUARANTINED` | `breaker_opened` | governor | — | — | — | `safety_quarantined` |
| `READY` | `UNKNOWN` | `boot_changed` | governor | — | — | — | `safety_reset_to_unknown` |
| `ADMITTED` | `RUNNING` | `canary_passed` | governor | `canary_deadline_seconds` | — | `retained` | `safety_running` |
| `ADMITTED` | `DRAINING` | `operator_cancel` | operator | — | `graceful` | `released` | `safety_drain_started` |
| `ADMITTED` | `DRAINING` | `soft_limit_sustained` | governor | — | `graceful` | `released` | `safety_drain_started` |
| `ADMITTED` | `FAILED` | `canary_failed` | governor | `canary_deadline_seconds` | — | `released` | `safety_canary_failed` |
| `ADMITTED` | `FAILED` | `worker_died` | worker | — | — | `released` | `safety_incident` |
| `ADMITTED` | `FAILED` | `unresponsive` | governor | `unresponsive_deadline_seconds` | — | `released` | `safety_incident` |
| `ADMITTED` | `DRAINING` | `hard_limit_breached` | governor | — | `immediate` | `released` | `safety_drain_started` |
| `ADMITTED` | `QUARANTINED` | `breaker_opened` | governor | — | — | `released` | `safety_quarantined` |
| `RUNNING` | `DRAINING` | `soft_limit_sustained` | governor | — | `graceful` | — | `safety_drain_started` |
| `RUNNING` | `DRAINING` | `hard_limit_breached` | governor | — | `immediate` | — | `safety_drain_started` |
| `RUNNING` | `DRAINING` | `progress_stalled` | governor | — | `graceful` | — | `safety_drain_started` |
| `RUNNING` | `DRAINING` | `operator_drain` | operator | — | `graceful` | — | `safety_drain_started` |
| `RUNNING` | `COOLDOWN` | `work_completed` | worker | — | — | — | `safety_completed` |
| `RUNNING` | `FAILED` | `worker_died` | worker | — | — | — | `safety_incident` |
| `RUNNING` | `FAILED` | `unresponsive` | governor | `unresponsive_deadline_seconds` | — | — | `safety_incident` |
| `DRAINING` | `COOLDOWN` | `drain_completed` | governor | `drain_deadline_seconds` | — | — | `safety_drained` |
| `DRAINING` | `FAILED` | `drain_timeout` | governor | `drain_deadline_seconds` | — | — | `safety_drain_timeout` |
| `COOLDOWN` | `READY` | `recovered` | governor | `cooldown_period_seconds` | — | — | `safety_recovered` |
| `COOLDOWN` | `COOLDOWN` | `recovery_pending` | governor | — | — | — | `safety_recovery_pending` |
| `COOLDOWN` | `QUARANTINED` | `recovery_failed` | governor | `recovery_deadline_seconds` | — | — | `safety_recovery_failed` |
| `COOLDOWN` | `QUARANTINED` | `breaker_opened` | governor | — | — | — | `safety_quarantined` |
| `FAILED` | `COOLDOWN` | `incident_recorded` | governor | — | — | — | `safety_incident_recorded` |
| `FAILED` | `QUARANTINED` | `breaker_opened` | governor | — | — | — | `safety_quarantined` |
| `QUARANTINED` | `READY` | `manual_reset` | operator | — | — | — | `safety_manual_reset` |

**Every transition not in this table is forbidden.** An implementation that
finds itself asked to make one raises rather than choosing a plausible target;
a state machine that repairs itself silently cannot be reasoned about.

### Closed in 1.1: the ADMITTED incident window

`ADMITTED` is the window between capacity being reserved and the compute canary
passing. It is short, but allocation and weight-load setup happen in it, which is
exactly when an OOM is most likely.

Under `safety-contract-1` its only exits were `canary_passed`, `canary_failed`,
`operator_cancel` and `soft_limit_sustained`. A worker dying there, or a breaker
opening on an incident recorded there, had nowhere to go, and the Governor raised
`GovernorError` naming the permitted triggers rather than routing the incident
through a neighbouring transition.

That raise is what surfaced the gap: the implementation refused to guess, and the
refusal was the report. It is the argument for a state machine that raises rather
than picking a plausible target.

`safety-contract-1.1` adds the four rows above. **Every exit from `ADMITTED` now
declares a lease outcome**, because a lease that is neither carried forward nor
released is stranded, and a stranded lease is capacity the Governor believes is
in use forever. The outcome is applied under the same revision as the state
change, so there is no window in which the state says the work is over and the
capacity is still booked.

### Source is not executor

**Only the Governor mutates state.** A worker reporting its own death does not
move the machine; it reports a fact the Governor acts on. The `Source` column is
who reported the trigger. The executor is always `governor`, recorded on every
event as `safetyTransitionExecutor`.

Keeping them separate is what makes an audit trail answerable to *who did this*.
Collapsed into one "actor" field — as an earlier draft had it — a worker-sourced
transition and a worker-executed one become indistinguishable, and the second
never happens.

`QUARANTINED → READY` is the only transition an operator alone can cause. The
Governor never releases its own quarantine; that is the entire point of having
one.

### State revision and idempotence

Every state carries a **monotonically increasing `stateRevision`**, incremented
on each applied transition and recorded on every event.

A trigger names the revision it observed. The Governor applies it only if that
revision is still current — compare-and-swap. Otherwise it is **rejected
idempotently**: no state change, no error to the caller, one
`safety_trigger_stale` event recording what was rejected and why.

This is what makes duplicate, out-of-order and late-arriving triggers safe.
Without it, a worker's `work_completed` delivered twice moves `RUNNING →
COOLDOWN` and then attempts it again from `COOLDOWN`, and a stalled worker's
late heartbeat can revive a machine that has already drained.

**Lease admission is atomic**: the capacity check and the lease record are
applied under the same revision, so two admissions racing between telemetry
samples cannot both observe enough room. §4.3.

---

## 4. Admission

Admission is evaluated against four inputs. **All four must pass.** A missing
input is not a pass (§11).

### 4.1 Baseline VRAM

The idle VRAM floor per device, sampled during preflight in `UNKNOWN → READY`,
before any OpenMycelium work exists.

Sampled as the **median of 5 samples at 1 s intervals**, not a single reading:
a display compositor allocating during a single sample would raise the baseline
permanently and silently shrink every later budget.

Recorded per device identity and per boot domain. A baseline from another boot is
not reused (§10).

### 4.2 Runtime reserve

Admission requires, per device:

```
free_bytes - requested_bytes >= max(reserve_floor_bytes, reserve_fraction * total_bytes)
```

with `reserve_floor_bytes` = 512 MiB and `reserve_fraction` = 0.03. On the qualified
16 GiB cards that is ~512 MiB, since 3% is ~490 MiB.

The reserve is not spare capacity for the workload. It is headroom for the
driver, the compositor and transient allocation spikes during weight load, and it
is what makes the difference between refusing admission and discovering the
shortfall as an OOM three minutes into a load.

### 4.3 Active leases

A lease is an admitted or running unit of work holding device capacity. The
Governor refuses admission when granting it would exceed capacity **counting
leases it has already granted**, not merely observed free memory: two admissions
racing between samples would each see enough room.

This build serves one request at a time, so `max_concurrent_leases` is 1 per
device. The check is written against a count, not against that constant, because
the constant is the thing most likely to change.

### 4.4 Telemetry confidence

Each signal carries one of three states — never a value that implies a
measurement was taken:

```
AVAILABLE    sampled within max_signal_age_seconds, from a working source
STALE        last sample older than max_signal_age_seconds (30 s)
UNAVAILABLE  no source, or the source failed
```

`UNAVAILABLE` subdivides, and the distinction decides whether admission fails:

```
UNAVAILABLE_EXPECTED    known absent on this platform, with a recorded reason
UNAVAILABLE_UNEXPECTED  a source that should work did not
```

An expected absence is a documented capability gap. An unexpected absence is a
fault, because something that worked has stopped.

### 4.5 Compute canary

Before `ADMITTED → RUNNING`: a small BF16 matmul on each device, result checked
finite. It costs milliseconds and catches a device that enumerates, reports
memory, and cannot compute.

The canary is also the readmission test after any incident (§8).

---

## 5. Limits

### 5.1 Soft and hard

| | Threshold | Meaning | Action |
|---|---|---|---|
| Soft | device VRAM utilisation ≥ `soft_limit_fraction` 0.90 | approaching exhaustion | stop admitting; if sustained past dwell, `RUNNING → DRAINING` (`graceful`) |
| Hard | device VRAM utilisation ≥ `hard_limit_fraction` 0.97 | exhaustion imminent | `RUNNING → DRAINING` (`immediate`), no dwell |

**Both limits drain. Neither jumps to `FAILED`.** An earlier draft said the hard
limit drained immediately while the transition table sent `RUNNING → FAILED`,
which are two different sequences and cannot both be the contract.

The resolution: exhaustion goes `RUNNING → DRAINING` with drain mode
`immediate` — SIGTERM at once, no finish-current-unit grace — and reaches
`FAILED` only if `hard_drain_deadline_seconds` (30 s) elapses without the workers
exiting. `RUNNING → FAILED` directly is reserved for `worker_died` and
`unresponsive`, the two cases where there is nothing left to drain.

Utilisation is `(total - free) / total` on the device, not the process's own
allocation: another process's memory is equally capable of causing an OOM.

### 5.2 Hysteresis

A single threshold produces flapping — one sample over, one under, admission
oscillating and the audit trail filling with noise that hides real events.

```
softLimitFraction        0.90   enter soft-limited
softReleaseFraction      0.85   leave soft-limited
softDwellSeconds         10     sustained above enter before acting
softReleaseDwellSeconds  30     sustained below release before clearing
```

Two mechanisms, both required: a **release threshold below the enter
threshold**, and a **dwell time on each edge**. Either alone still flaps — a gap
with no dwell flaps on a noisy signal that crosses both, and a dwell with no gap
flaps on a signal sitting exactly at the threshold.

The hard limit has **no dwell and no hysteresis**. Waiting 10 s to confirm
imminent exhaustion is how the exhaustion happens.

### 5.3 Drain and forced termination

Drain is deliberate, bounded stopping. Two modes, chosen by the trigger:

**`graceful`** — soft limit, stall, operator drain:

```
0 s      stop admitting; signal workers to finish the current unit
30 s     drain_grace_deadline_seconds   SIGTERM to workers still running
120 s    drain_deadline_seconds         SIGKILL, transition to FAILED
```

**`immediate`** — hard limit only:

```
0 s      stop admitting; SIGTERM at once, no finish-current-unit grace
30 s     hard_drain_deadline_seconds    SIGKILL, transition to FAILED
```

The grace period is the thing that causes the failure when memory is already
exhausted, so exhaustion does not get one.

Either deadline is a **deadline, not a target**. Exceeding it is an incident and
is recorded as one (`safety_drain_timeout`), even though the outcome — workers
gone — matches the successful case. A drain that needed SIGKILL and one that did
not are different facts about the system.

After forced termination the Governor verifies device residency returned to
baseline before `COOLDOWN → READY` (§8). A killed process does not always release
VRAM promptly, and treating the kill as completion is how a leak becomes the next
run's baseline.

### Who may be signalled

A signal may target **only a process the Governor owns**, and ownership is
re-checked immediately before the signal against all of:

```
pid                  the process id
processStartTime     /proc/<pid>/stat field 22, in jiffies
runId                the run that spawned it
placementId          the placement it is executing
bootId               the boot domain both were recorded in
```

**PIDs are reused.** A worker that exited during the drain window frees its PID,
and the kernel may hand it to something else within milliseconds. Signalling on
PID alone kills whatever inherited it — on a developer machine as likely an
editor as a worker. `processStartTime` is what makes the check sound: it cannot
be reused, because a recycled PID belongs to a process that started later.

If any element fails to match, **the signal is not sent** and a
`safety_termination_abandoned` event records which element diverged. A worker
that cannot be identified is a worker that has already gone.

The Governor records `safety_termination_intent` **before** signalling and
`safety_termination_outcome` after. Recording only the outcome loses the case
where the Governor died between deciding and acting, which is precisely the case
where an unexplained process death needs explaining.

---

## 6. Progress watchdog

### Definitions

**Making progress** — within `progress_interval_seconds` (15 s), at least one of:
a heartbeat with an advanced monotonic counter; a completed compute step; or
transport bytes moved. All three are counters that only increase, so "progress"
never depends on a process claiming to be healthy.

**Stalled** — no progress signal for `stall_deadline_seconds` (60 s), while the
process still responds. The work is not advancing; the process is alive.
→ `RUNNING → DRAINING`.

**Unresponsive** — no signal of any kind for `unresponsive_deadline_seconds` (180 s), or
the process is gone. Nothing to drain gracefully.
→ `RUNNING → FAILED`.

Stalled and unresponsive are deliberately different: a stalled worker may still
release VRAM and exit cleanly if asked, and asking is cheaper than killing.

### Deadlines are per operation

A deadline calibrated to token generation would fire during weight loading, which
legitimately takes minutes. Operation-specific:

```
weight_load          900 s   22.84 GiB across two devices
prefill               60 s
decode_step           15 s
boundary_transfer     30 s
canary                10 s
```

The Governor knows which operation is in flight because the worker declares it
in its heartbeat. A heartbeat with no declared operation is treated as
`unresponsive` at the shortest deadline: a worker that cannot say what it is
doing is not evidence that it is doing something.

---

## 7. Circuit breaker and quarantine

### Threshold

The breaker opens when **3 incidents sharing a signature occur within 3600 s**.

The signature is `(incidentClass, deviceIdentity, adapterId)` — not the run id,
which differs every time, and not the message text, which varies with formatting.
Three unrelated failures on three devices are three problems; three identical
failures on one device is a pattern, and retrying into it is what turns a fault
into damage.

`incidentClass` is one of: `oom`, `worker_death`, `stall`, `transport_failure`,
`canary_failure`, `drain_timeout`, `telemetry_loss`.

### Quarantine scope

Quarantine is **scoped to the failing capability**, never global:

```
device       that device identity refuses admission; the other may still run
adapter      that adapter refuses; other adapters unaffected
transport    that transport direction refuses
machine      only when preflight itself cannot complete
```

A cross-vendor pipeline needs both devices, so a device quarantine does stop
pipeline work — but it stops it with an accurate reason, and it leaves
single-device work possible.

### Persistence

Quarantine outlives the process and the boot: it is written to the state
directory and restored during preflight (`UNKNOWN → QUARANTINED`). A quarantine
that a restart clears is not a quarantine, and restarting is the first thing
anyone does.

### Durability of the write

A quarantine that is lost to a crash mid-write is worse than one never taken:
the machine comes back believing itself healthy, having decided otherwise.

```
1. serialise the record, including a sha256 `digest` over every other field
2. write to <path>.partial
3. flush(), then os.fsync(fd)          -- the data reaches the device
4. os.replace(<path>.partial, <path>)  -- atomic on POSIX and on NTFS
5. fsync the containing directory      -- the rename itself is durable
```

Step 5 is the one usually omitted. Without it the file's contents survive a
crash and the directory entry pointing at them may not.

Required fields: `recordId`, `incidentClass`, `scope`, `scopeIdentity`,
`reason`, `openedAt`, `bootId`, `policyVersion`, `digest`.

**Recovery.** On preflight the Governor reads the store and, for each record:

| Condition | Treated as |
|---|---|
| digest matches, fields complete | a valid quarantine — restore it |
| digest mismatches | corrupt — **quarantine**, class `telemetry_loss`, reason recorded |
| a required field is missing | corrupt — **quarantine** |
| the file is unreadable or unparseable | **quarantine** (§11) |
| a `.partial` file is present | the previous write was interrupted; delete it and quarantine |

Every corruption path ends in quarantine, never in "assume healthy". Otherwise
the cheapest way to clear a quarantine is to damage the file that records it,
and a crash during the write becomes a silent release.

A `.partial` left behind is not itself proof the record was needed — the crash
may have happened before anything was decided — but the Governor cannot tell
which, and between "quarantine something that was fine" and "release something
that was not", only the first is recoverable by an operator.

---

## 8. Manual reset

Only an operator clears a quarantine. The Governor never clears its own.

Required, all of them, or the reset is refused:

1. **Attribution** — an actor identity, from the same explicit-and-attributed
   mechanism qualification override uses. A reset that records no one is not a
   reset.
2. **Acknowledged incidents** — the reset names the quarantine record id it
   clears. A blanket reset cannot clear an incident nobody read.
3. **Baseline reverified** — every device back within
   `baseline_tolerance_bytes` (256 MiB) of its recorded baseline.
4. **Canary passed** — §4.5, on every device in scope, after the baseline check.

Recorded as `safety_manual_reset` carrying the actor, the cleared record ids, the
baseline readings, and the canary result. The evidence is the point: a reset is a
human overriding a refusal, and the trail must show what they knew.

Resetting does **not** delete incident history. The breaker's window continues to
count cleared incidents, so an operator repeatedly clearing the same fault
re-opens the breaker rather than escaping it.

---

## 9. Telemetry by vendor

### NVIDIA

`nvidia-smi` provides VRAM used and total, temperature, power draw and limit,
SM and memory clocks, and throttle reasons. All `AVAILABLE` on the qualified
platform.

### AMD under WSL

VRAM total and free are available through the ROCm runtime.

**Power and temperature are `UNAVAILABLE_EXPECTED` and must never be reported as
zero.** `rocm-smi` requires the `amdgpu` kernel module; WSL exposes `/dev/dxg`
instead. The fabric layer already records this correctly — `power_source:
"unavailable"`, `power_watts: null`, with the reason in `notes` — and the
Governor must preserve it rather than defaulting.

Zero watts is a *reading*. It says the device is drawing no power, which would be
alarming and false. The distinction between "measured zero" and "not measured" is
the whole reason confidence is tracked, and collapsing it is the single most
likely way this contract gets violated in implementation.

### Policy v1 claim boundary

**In policy v1 these signals are recorded and never acted on — on any platform,
including NVIDIA where they are available:**

```
power              temperature              throttle_reasons
```

Declared as data in `contract.ADVISORY_ONLY_SIGNALS`, and asserted by
`test_contract_data.py`, so the boundary is checkable rather than a sentence
someone has to remember.

The reason is not that the signals are useless. It is that the qualified
platform is one NVIDIA card plus one AMD card under WSL, and **the AMD card has
no power or thermal signal at all** — `rocm-smi` needs the `amdgpu` kernel
module and WSL exposes `/dev/dxg`. A policy that acted on thermal data would
protect one card and not the other, while describing itself as thermal
protection.

So policy v1 states plainly: **it performs no thermal protection and no power
protection.** It cannot, on half its hardware. Firmware and driver protections
remain the only thermal authority, as §0 says.

A future policy version may gate on these signals for a device that has them,
after that device's telemetry is qualified. That is a policy version bump with
its own evidence, not a default that quietly arrives.

### Identity confidence

NVIDIA identity is a UUID (`identityConfidence: strong`). AMD identity is the PCI
bus (`slot-stable`) because the ROCm-reported UUID is not stable across
processes. A baseline or quarantine keyed to a `slot-stable` identity is
invalidated by physical reseating, which the operator must know when moving
cards.

---

## 10. Boot domain and clocks

`CLOCK_MONOTONIC` restarts near zero on every WSL VM boot — measured at 2.929 s
on a VM whose uptime was 2.91 s. Two events from different boots can therefore
carry the same `monotonicNs`.

Rules:

1. Every sample, incident and baseline records the `bootId` it was taken in.
2. **Durations are computed only between samples sharing a `bootId`.** Across
   boots the result is meaningless, not merely imprecise.
3. On a boot change the Governor enters `UNKNOWN` and re-runs preflight.
   Baselines do not carry across; VRAM baseline is a property of a running
   system.
4. Quarantine **does** carry across boots (§7). It is a decision, not a
   measurement.
5. `wallTimeUtc` orders events for humans and is never used for deadlines: it
   can step backwards.

This is already implemented in `runtime/serving/audit.py` (`boot_id()`,
`monotonic_ns()`) and enforced by `MINIMAL_TUPLE`. The Governor uses it rather
than introducing a second clock discipline.

---

## 11. Fail-closed and fail-safe, per signal

**Fail-closed** — the absence of the signal blocks the action.
**Fail-safe** — the absence is tolerated, degrading rather than stopping.

The rule: **admission fails closed, continuation fails safe, exhaustion fails
closed.** Refusing to start costs a refusal; stopping a healthy run costs the
work; failing to stop an exhausting one costs more than either.

| Signal | Missing or stale at admission | Missing or stale while `RUNNING` |
|---|---|---|
| Device enumeration | **closed** — refuse | **closed** — incident |
| Device identity | **closed** — refuse | **closed** — incident |
| VRAM free/total | **closed** — refuse | **safe → closed**: mark degraded, stop admitting, keep running; if unavailable > `degraded_deadline_seconds` 120 s, drain |
| Baseline VRAM | **closed** — refuse | n/a, sampled at preflight |
| Compute canary | **closed** — refuse | n/a, admission only |
| Worker heartbeat | n/a | **closed** — stall then unresponsive (§6) |
| Transport progress | n/a | **safe** — heartbeat is authoritative; absence alone is not a stall |
| Power, `UNAVAILABLE_EXPECTED` | **safe** — advisory | **safe** — advisory |
| Power, `UNAVAILABLE_UNEXPECTED` | **closed** — a working source stopped | **safe → closed** as VRAM above |
| Temperature | as power | as power |
| Throttle reasons | **safe** — advisory | **safe** — advisory |
| Quarantine record readable | **closed** — an unreadable quarantine store is treated as quarantined | **closed** |

The last row is deliberate. If the Governor cannot read its own quarantine
state, it cannot know whether it is quarantined, and the safe assumption is that
it is. Anything else makes corrupting one file the way to clear a quarantine.

---

## 12. Audit

Every decision emits an event carrying the existing `MINIMAL_TUPLE` — `runId`,
`placementId`, `manifestDigest`, `modelFingerprint`, `workerRole`,
`deviceIdentity`, `stageOrientation`, `boundaryAfterLayer`, `eventSequence`,
`wallTimeUtc`, `monotonicNs`, `bootId`, `writerId`, `eventSchemaVersion` — plus:

```
safetyState          the state after the transition
safetyPreviousState  the state before it
safetyTrigger        which row of the table fired
safetyActor          governor | operator | worker
safetyPolicyVersion  the policy document version these thresholds came from
safetySignals        per-signal value and confidence at decision time
```

`safetyPolicyVersion` matters because a decision is only reviewable against the
thresholds in force when it was made. Without it, reading last month's incident
against this month's limits silently misjudges it.

`safetySignals` records confidence alongside value, so a decision made with
`UNAVAILABLE_EXPECTED` power is distinguishable afterwards from one made with a
real reading — including a real reading of zero.

### Pre-binding events

Preflight and admission happen before a placement manifest is bound, and
`audit.emit()` correctly refuses to attribute events to an unverified manifest.
Governor events before binding use the `PRE_VALIDATION_FIELDS` path or the
deferral mechanism added for qualification overrides. **No new bypass is
introduced**, and the existing invariant stands: no event carries an identity
taken from a manifest that has not been verified.

---

## 13. Simulation and fault injection

Every threshold, deadline and transition must be reachable in tests **without
touching a GPU**.

```
SafetyGovernor(clock=..., telemetry=..., policy=...)
```

- **Clock** — injected; tests advance it. Real deadlines are 60–900 s and a suite
  that waited them out would take hours and would be skipped.
- **Telemetry** — a source interface. `SyntheticTelemetry` returns scripted
  readings, including `STALE`, `UNAVAILABLE_EXPECTED` and
  `UNAVAILABLE_UNEXPECTED`, which are difficult to produce on demand from real
  hardware and trivial to get wrong.
- **Policy** — thresholds are data, so a test can construct a boundary case
  without editing the contract.

Faults injectable without hardware: OOM, worker death, stall, unresponsive,
transport failure, canary failure, drain timeout, telemetry loss, boot change.

**Simulation never runs in production.** A Governor constructed with synthetic
telemetry records `safetySimulated: true` on every event, and a run whose audit
trail carries that flag is not qualification evidence. The Adapter SDK's
qualification override is the precedent: the escape exists, and it is impossible
to use without leaving a mark.

Hardware validation belongs to Gate D. This milestone stresses no GPU.

---

## 14. Policy defaults

Version `safety-policy-1-provisional`. Thresholds are data; changing one is a policy version
bump, not a code change.

Field names are those of `contract.Policy`, which is the authority. This block is
a transcription of it; `test_contract_data.py` fails if a timeout named by a
transition is not a real field.

```
version                          safety-policy-1-provisional

preflight_deadline_seconds       60
admission_deadline_seconds       30
canary_deadline_seconds          10
drain_grace_deadline_seconds     30
drain_deadline_seconds           120
hard_drain_deadline_seconds      30
cooldown_period_seconds          60
recovery_deadline_seconds        600
degraded_deadline_seconds        120

reserve_floor_bytes              536870912    512 MiB
reserve_fraction                 0.03
baseline_tolerance_bytes         268435456    256 MiB
baseline_sample_count            5
baseline_sample_interval_seconds 1

soft_limit_fraction              0.90
soft_release_fraction            0.85
soft_dwell_seconds               10
soft_release_dwell_seconds       30
hard_limit_fraction              0.97

max_signal_age_seconds           30
progress_interval_seconds        15
stall_deadline_seconds           60
unresponsive_deadline_seconds    180

breaker_threshold                3
breaker_window_seconds           3600
max_concurrent_leases            1
```

`canary_deadline_seconds` is **10**, and is the single value used by the
`ADMITTED → RUNNING` and `ADMITTED → FAILED` transitions and by the `canary`
operation deadline. An earlier draft carried 10 s in §6 and 120 s here and in the
transition table, which is three places for one number and two of them wrong. The
canary is a small BF16 matmul; 120 s was the time allowed to *reach* `RUNNING`,
which is a different thing that no longer has a separate name because nothing
needed one.

Operation deadlines are in §6. Contract version: `safety-contract-1`.

---

## 15. What "frozen" will require

Before this contract may be frozen and Gate D authorized:

1. Every transition in §3 is exercised by a test.
2. Every fail-closed and fail-safe cell in §11 is exercised.
3. Two independent implementations cannot interpret a transition differently —
   the acceptance bar the ledger sets.
4. No runtime behaviour has changed. The Adapter SDK gates still pass unmodified.
5. The `runtime/safety/` packaging gap in §1 is either fixed or explicitly
   deferred with a named owner.
