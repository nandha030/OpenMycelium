# Mycelium Safety Governor — contract

**Status: DRAFT, for review.** Not frozen. No runtime behaviour exists. This
document and its failing tests are Gate C.1 in full; implementation is Gate D and
begins only on explicit authorization.

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

### Two handoff gaps for Gate D

Both are silent failures if forgotten, so they are written down rather than
remembered.

**Packaging.** `runtime/safety/` is a new subsystem and is **not** in
`scripts/build_openmycelium_wheel.py`'s `INCLUDE` tuple (`cli`, `serving`,
`scheduler`, `fabric`). Gate D must add `safety` there, or the Governor will pass
its tests from a checkout and be absent from every wheel.

**Test suite.** `runtime/safety/` is deliberately **not** in
`scripts/repro/run_unit_suite.sh`'s `UNITTEST_DIRS`. Its tests fail by design
until Gate D — they are the contract, written before the implementation — and
adding them now would turn the suite red and block everything behind it.

That exclusion is a debt, not a decision: the suite's own rule is that a group
reporting zero failures must not silently skip anything. It is tolerable only
because the omission is recorded here and the tests fail loudly when run
directly. **Gate D adds `runtime/safety` to `UNITTEST_DIRS` in the same commit
that makes them pass**, and the suite total moves from 263.

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

Actors: **G** Governor (automatic), **O** operator (explicit human action),
**W** worker (reports a fact the Governor acts on).

| From | To | Trigger | Actor | Timeout | Audit event |
|---|---|---|---|---|---|
| `UNKNOWN` | `READY` | preflight passed: device identities resolved, baseline VRAM sampled, required telemetry available | G | `preflightDeadline` 60 s | `safety_ready` |
| `UNKNOWN` | `FAILED` | preflight failed: identity unresolvable, no device, baseline unsamplable | G | — | `safety_preflight_failed` |
| `UNKNOWN` | `QUARANTINED` | a quarantine record from a previous boot is unresolved | G | — | `safety_quarantine_restored` |
| `READY` | `ADMITTED` | admission granted (§4) | G | `admissionDeadline` 30 s | `safety_admitted` |
| `READY` | `READY` | admission refused; the machine is not at fault | G | — | `safety_admission_refused` |
| `READY` | `QUARANTINED` | circuit breaker opened (§7) | G | — | `safety_quarantined` |
| `READY` | `UNKNOWN` | boot domain changed, or required telemetry became unavailable | G | — | `safety_reset_to_unknown` |
| `ADMITTED` | `RUNNING` | compute canary passed (§4.4) | G | `canaryDeadline` 120 s | `safety_running` |
| `ADMITTED` | `DRAINING` | soft limit breached before compute began, or operator cancel | G/O | — | `safety_drain_started` |
| `ADMITTED` | `FAILED` | canary failed or exceeded `canaryDeadline` | G | — | `safety_canary_failed` |
| `RUNNING` | `DRAINING` | soft limit sustained past hysteresis (§5), progress stalled (§6), or operator drain | G/O | — | `safety_drain_started` |
| `RUNNING` | `COOLDOWN` | work completed normally | W | — | `safety_completed` |
| `RUNNING` | `FAILED` | hard limit breached, worker died, or unresponsive past `unresponsiveDeadline` | G/W | — | `safety_incident` |
| `DRAINING` | `COOLDOWN` | drain completed within `drainDeadline` | G | `drainDeadline` 120 s | `safety_drained` |
| `DRAINING` | `FAILED` | `drainDeadline` exceeded; forced termination (§5.3) | G | — | `safety_drain_timeout` |
| `COOLDOWN` | `READY` | `cooldownPeriod` elapsed **and** baseline reverified (§8) | G | `cooldownPeriod` 60 s | `safety_recovered` |
| `COOLDOWN` | `QUARANTINED` | circuit breaker opened while evaluating the incident | G | — | `safety_quarantined` |
| `COOLDOWN` | `COOLDOWN` | baseline not yet reverified; retry until `recoveryDeadline` | G | `recoveryDeadline` 600 s | `safety_recovery_pending` |
| `COOLDOWN` | `QUARANTINED` | `recoveryDeadline` exceeded without reaching baseline | G | — | `safety_recovery_failed` |
| `FAILED` | `COOLDOWN` | incident recorded; breaker not open | G | — | `safety_incident_recorded` |
| `FAILED` | `QUARANTINED` | incident recorded; breaker threshold reached | G | — | `safety_quarantined` |
| `QUARANTINED` | `READY` | manual reset with evidence (§8) | **O** | — | `safety_manual_reset` |

**Every transition not in this table is forbidden.** An implementation that
finds itself asked to make one raises rather than choosing a plausible target;
a state machine that repairs itself silently cannot be reasoned about.

`QUARANTINED → READY` is the only transition whose actor is the operator alone.
The Governor never releases its own quarantine — that is the entire point of
having one.

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
free_bytes - requested_bytes >= max(reserveFloorBytes, reserveFraction * total_bytes)
```

with `reserveFloorBytes` = 512 MiB and `reserveFraction` = 0.03. On the qualified
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

This build serves one request at a time, so `maxConcurrentLeases` is 1 per
device. The check is written against a count, not against that constant, because
the constant is the thing most likely to change.

### 4.4 Telemetry confidence

Each signal carries one of three states — never a value that implies a
measurement was taken:

```
AVAILABLE    sampled within maxSignalAgeSeconds, from a working source
STALE        last sample older than maxSignalAgeSeconds (30 s)
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
| Soft | device VRAM utilisation ≥ `softLimitFraction` 0.90 | approaching exhaustion | stop admitting; if sustained, drain |
| Hard | device VRAM utilisation ≥ `hardLimitFraction` 0.97 | exhaustion imminent | drain immediately, no dwell |

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

Drain is deliberate, bounded stopping. Escalation, at these deadlines from drain
start:

```
0 s      stop admitting; signal workers to finish the current unit
30 s     drainGraceDeadline   -- SIGTERM to workers that have not exited
120 s    drainDeadline        -- SIGKILL, transition to FAILED
```

`drainDeadline` is a **deadline, not a target**. Exceeding it is an incident and
is recorded as one (`safety_drain_timeout`), even though the outcome — workers
gone — matches the successful case. A drain that needed SIGKILL and one that did
not are different facts about the system.

After forced termination the Governor verifies device residency returned to
baseline before `COOLDOWN → READY` (§8). A killed process does not always release
VRAM promptly, and treating the kill as completion is how a leak becomes the next
run's baseline.

---

## 6. Progress watchdog

### Definitions

**Making progress** — within `progressIntervalSeconds` (15 s), at least one of:
a heartbeat with an advanced monotonic counter; a completed compute step; or
transport bytes moved. All three are counters that only increase, so "progress"
never depends on a process claiming to be healthy.

**Stalled** — no progress signal for `stallDeadlineSeconds` (60 s), while the
process still responds. The work is not advancing; the process is alive.
→ `RUNNING → DRAINING`.

**Unresponsive** — no signal of any kind for `unresponsiveDeadline` (180 s), or
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
   `baselineToleranceBytes` (256 MiB) of its recorded baseline.
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

Consequently **power and temperature are advisory, not admission gates**, on any
platform where they are `UNAVAILABLE_EXPECTED`. Gating on them would make the
Governor refuse all work on its only qualified platform.

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
| VRAM free/total | **closed** — refuse | **safe → closed**: mark degraded, stop admitting, keep running; if unavailable > `degradedDeadline` 120 s, drain |
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

Version `safety-policy-1`. Thresholds are data; changing one is a policy version
bump, not a code change.

```
preflightDeadlineSeconds        60
admissionDeadlineSeconds        30
canaryDeadlineSeconds          120
drainGraceDeadlineSeconds       30
drainDeadlineSeconds           120
cooldownPeriodSeconds           60
recoveryDeadlineSeconds        600
degradedDeadlineSeconds        120

reserveFloorBytes              536870912      512 MiB
reserveFraction                0.03
baselineToleranceBytes         268435456      256 MiB
baselineSampleCount            5
baselineSampleIntervalSeconds  1

softLimitFraction              0.90
softReleaseFraction            0.85
softDwellSeconds               10
softReleaseDwellSeconds        30
hardLimitFraction              0.97

maxSignalAgeSeconds            30
progressIntervalSeconds        15
stallDeadlineSeconds           60
unresponsiveDeadlineSeconds   180

breakerThreshold               3
breakerWindowSeconds        3600
maxConcurrentLeases            1
```

Operation deadlines are in §6.

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
