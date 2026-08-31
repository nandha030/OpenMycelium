# Safety Governor — enforcement contract

**Version `safety-enforcement-1`. Gate D.3. Contract only: no implementation
exists, and freezing this contract does not enable enforcement.**

`docs/SAFETY_GOVERNOR.md` and `runtime/safety/contract.py` say which state
transitions exist. This says which of them the Governor is permitted to
**cause**, under what evidence, in what order that permission is granted, and
how each grant is withdrawn.

`safety-contract-1.1` is unchanged by this document. Not one transition is
added, removed or retargeted. In shadow mode every transition in that table was
computed and none was applied; the question here is which may now be applied.

Every table below is generated from `runtime/safety/enforcement.py` by
`scripts/repro/render_enforcement_tables.py`. `test_enforcement_contract.py`
asserts both directions, so a stale paste is a test failure rather than a
discrepancy nobody notices.

---

## 1. Four versions, moving independently

| Version | What it means | Changes when |
|---|---|---|
| `safety-contract-1.1` | which transitions exist | the state machine changes |
| `safety-policy-1-provisional` | the value of a threshold | a limit is retuned |
| `SHADOW_SCHEMA_VERSION = 1` | the shape of an observation | the record gains a field |
| `safety-enforcement-1` | what the Governor may do | authority or the fault matrix changes |

Kept apart for the reason already written into the shadow module: a reader who
cannot tell which of them moved cannot safely interpret an old audit record.
Enforcement authority changes on its own schedule — most obviously, it will
change four times during the canary sequence while nothing else moves.

## 2. Authority, and what it is not

Authority is a **ladder, not a switch**. Each rung is authorised separately,
because each fails differently and evidence for one says nothing about the next.
Refusing an admission cannot corrupt an in-flight run; terminating a worker can.
They do not belong behind the same flag.

| Rung | Authority | Actions it adds |
|---|---|---|
| 0 | `off` | — |
| 1 | `shadow` | — |
| 2 | `enforce_admission` | `refuse_admission`, `degrade` |
| 3 | `enforce_drain` | `drain_graceful`, `drain_immediate` |
| 4 | `enforce_breaker` | `open_breaker` |
| 5 | `enforce_quarantine` | `quarantine` |
| 6 | `enforce_full` | — |

`enforce_full` adds no action of its own. It is the name for "every rung
enabled", and enabling default enforcement means setting it — which is not part
of D.3.

**The default is `off` and this contract does not change it.** Freezing an
enforcement contract is not enabling enforcement.

Two rules the implementation must obey:

- **The Governor refuses to act above its authority.** It does not silently
  downgrade to the strongest action it is allowed. A request to do something it
  may not do is a defect in the caller, and hiding it makes the ladder
  meaningless.
- **Authority never promotes an action.** `enforce_full` does not turn a refusal
  into a quarantine. Authority says what is switched on; the fault says what is
  warranted; an action needs both.

### Non-goals

- No default enforcement: the default authority stays `off` when this freezes
- **No Memory OS work of any kind on this branch** — no `MemoryObject`,
  `ResidencyManager`, `TransportBackend`, `PrefetchPolicy`, `EvictionPolicy`,
  `WorkingSet`, `bind_working_set` or paging
- No thermal or power enforcement: AMD power is unavailable under WSL, and no
  platform here has the telemetry such a claim would need
- No change to `safety-contract-1.1`
- No new package version, tag or GPU campaign while this contract is frozen
- No multi-node or MHub enforcement

## 3. Fault matrix

A fault not listed here justifies **no action**. The Governor reports it and
does nothing, because an unlisted fault is one whose consequences were never
argued through.

`Action` is the strongest response the fault may ever justify. The Governor may
take a weaker one; it may never take a stronger one, whatever its authority.

| Fault | Detected by | Confidence | Action | Trigger | Rollback to |
|---|---|---|---|---|---|
| `vram_reservation_unmet` | admission check against the reserve floor | `AVAILABLE` | `refuse_admission` | `admission_refused` | raise authority to SHADOW; the run proceeds as it does today |
| `vram_unreadable_at_admission` | free bytes absent with UNAVAILABLE_UNEXPECTED | `UNAVAILABLE_UNEXPECTED` | `refuse_admission` | `admission_refused` | raise authority to SHADOW |
| `soft_limit_sustained` | used fraction over soft limit for the dwell | `AVAILABLE` | `drain_graceful` | `soft_limit_sustained` | authority to ENFORCE_ADMISSION; in-flight work is left alone |
| `hard_limit_breached` | used fraction at or over the hard limit | `AVAILABLE` | `drain_immediate` | `hard_limit_breached` | authority to ENFORCE_ADMISSION |
| `telemetry_stale` | newest sample older than max_signal_age_seconds | `AVAILABLE` | `degrade` | — | authority to SHADOW |
| `telemetry_unavailable_unexpected` | a source that named itself returned nothing | `UNAVAILABLE_UNEXPECTED` | `degrade` | — | authority to SHADOW |
| `worker_died` | process exit observed with a matching identity | `AVAILABLE` | `drain_immediate` | `worker_died` | authority to ENFORCE_ADMISSION |
| `progress_stalled` | no heartbeat within the operation deadline | `AVAILABLE` | `drain_immediate` | `progress_stalled` | authority to ENFORCE_ADMISSION |
| `unresponsive` | no heartbeat within unresponsive_deadline_seconds | `AVAILABLE` | `drain_immediate` | `unresponsive` | authority to ENFORCE_ADMISSION |
| `canary_failed` | the compute canary did not return the expected value | `AVAILABLE` | `drain_immediate` | `canary_failed` | authority to ENFORCE_ADMISSION |
| `boundary_integrity_failed` | sent and received digests differ at the boundary | `AVAILABLE` | `quarantine` | `incident_recorded` | authority to ENFORCE_BREAKER; the record persists and is reset manually |
| `repeated_incidents` | breaker_threshold incidents within breaker_window_seconds | `AVAILABLE` | `open_breaker` | `breaker_opened` | authority to ENFORCE_DRAIN; the counter is not cleared by the rollback |
| `lease_stranded` | a lease outlives the run that took it | `AVAILABLE` | `degrade` | — | authority to SHADOW |
| `boot_changed` | bootId differs from the one the lease was taken under | `AVAILABLE` | `none` | `boot_changed` | none required; the Governor always revises its own belief |

### Faults with no trigger

`degrade` means *stop admitting new work and leave in-flight work alone*. That
is a change in what the Governor will agree to next, **not a move through the
state machine**, so those rows name no trigger. The frozen table has none for it
and must not grow one: inventing a transition to make a matrix tidy is how a
state machine stops describing the system.

### Belief revision is not enforcement

`boot_changed` is `READY → UNKNOWN`. The Governor is correcting what it believes,
not acting on the world, so it needs **no authority and happens at every rung
including `off`** — otherwise the Governor would go on believing a lease from a
previous boot is live. `CLOCK_MONOTONIC` restarts near zero on each WSL boot;
durations do not cross one, and a lease from a previous boot is not a lease.

### Expected unavailability is not a fault

AMD power under WSL reads `null` with `UNAVAILABLE_EXPECTED`, because `rocm-smi`
needs the `amdgpu` driver and WSL exposes `/dev/dxg`. That is a platform gap, not
a failure, and no row acts on it. A matrix that treated it as a fault would
degrade every run on this hardware.

## 4. State transitions and lease outcomes

The transition table is unchanged and lives in `docs/SAFETY_GOVERNOR.md`. What
enforcement adds is that a transition may now be **applied**, and applying one
settles its lease.

Every exit from `ADMITTED` already declares a lease outcome, because a lease that
is neither carried forward nor released is stranded — and a stranded lease is
capacity the Governor believes is in use forever. Under enforcement that
declaration becomes an obligation:

- an applied transition whose `lease_outcome` is `released` **must** release it,
  and the audit record must say so
- an applied transition whose `lease_outcome` is `retained` must not
- a fault whose action is `degrade` settles no lease, because it applies no
  transition

`lease_stranded` is itself a fault in the matrix, detected rather than assumed
away, and its action is `degrade`: the Governor stops admitting and reports,
rather than reclaiming capacity it cannot prove is free.

## 5. Admission refusal

Fail closed. Admission is refused when the reserve floor cannot be met, and when
free VRAM is unreadable with `UNAVAILABLE_UNEXPECTED` — **an unreadable card is
not an empty one.**

A refusal must:

- be deterministic for the same inputs
- carry a `reasonCode` naming a row of the fault matrix
- name the device it refused for
- leave no worker and take no lease
- carry the shadow prediction for the same inputs, and record whether it matched

## 6. Drain

Two modes, and the difference is what happens to work already running.

`drain_graceful` stops new admissions and lets in-flight work finish within
`drain_grace_deadline_seconds`. `drain_immediate` interrupts it within
`hard_drain_deadline_seconds` — the shorter deadline is deliberate, because the
grace period is what caused the failure.

A drain must release the lease, leave no surviving worker, and leave no compute
context on the device. **It is judged on those, not on a device memory total**
(§9).

Termination requires identity: pid **and** process start time **and** run id. A
pid alone can name a process the Governor never launched.

## 7. Circuit breaker

Opens at `breaker_threshold` incidents within `breaker_window_seconds`, and not
before. While open it refuses admission. Its state is durable and survives a
restart — a breaker whose state is lost on restart is a breaker that reopens the
door after the crash that tripped it.

Lowering authority to `enforce_drain` disables the breaker's action. **It does
not clear the counter**: the incidents happened.

## 8. Quarantine and manual reset

`boundary_integrity_failed` — sent and received digests differing at the vendor
boundary — is the **only** fault that quarantines on a single occurrence. Every
byte-exactness claim in this project rests on that boundary; one failure is
enough.

A quarantine record is durable, names the device and the incident class, refuses
the quarantined path, and is cleared **only by a named actor through a manual
reset**, which is recorded. It is the one action no automatic path may undo.

## 9. Rollback

| Mechanism | Scope | Restart | Survives crash |
|---|---|---|---|
| lower the authority to SHADOW or OFF | the next run | no | yes |
| an operator kill switch that pins authority to OFF | immediate, all runs | no | yes |
| manual reset of a quarantine record | one device or path | no | yes |

The ladder **is** the rollback: every fault row names the authority to drop to,
and dropping one rung disables exactly that fault's action.

The kill switch must not depend on the Governor being healthy. **A rollback that
needs the thing that failed is not a rollback.**

## 10. Hardware stopping conditions

A canary campaign aborts, and is not retried, on any of these. They are not
failures of the thing under test — they are conditions under which the test stops
meaning anything, so continuing produces evidence about nothing.

- a worker process survives a termination the Governor believes succeeded
- a compute context remains on a device after a run the Governor believes ended
- `bootId` changes during a campaign
- two consecutive unexplained device faults
- a telemetry source that was `AVAILABLE` becomes `UNAVAILABLE_UNEXPECTED` mid-campaign
- an enforcement action fires with no matching shadow prediction
- an enforcement action fires whose reason code is not in the fault matrix
- the run count for the campaign is reached
- any action is taken above the authority under test

### What is deliberately not a stopping condition

**A device memory total above the pre-run reading.** Under WSL `nvidia-smi`
reports the whole physical card including Windows host usage and cannot
enumerate host processes. Across one sealing run it read 722 MiB, then 824, then
678 with nothing of ours alive — ending 44 MiB *below* where it started. A number
that moves on its own cannot abort a campaign without aborting valid ones.

Cleanup is judged on **surviving workers and compute contexts**, which are
attributable.

## 11. Canary sequence

Ordered, and each stage separately authorised. A stage may not begin before the
one before it has passed; passing stage one is evidence about stage one only.
Every stage has a synthetic gate before its hardware gate.

| # | Authority | Accept when | Reject when |
|---|---|---|---|
| 1 | `enforce_admission` | every refusal is deterministic, carries a reason code, matches the shadow prediction for the same inputs, and leaves no worker and no lease | any refusal without a matching shadow prediction, any false refusal in the clean control runs, or any run that starts after a refusal |
| 2 | `enforce_drain` | in-flight work either completes or is interrupted as the mode declares, the lease is released, no worker survives, no compute context remains | a drain that leaves a worker, strands a lease, or interrupts work a graceful drain promised to let finish |
| 3 | `enforce_breaker` | the breaker opens exactly at threshold, refuses admission while open, and its state survives a restart | an open breaker that admits, a breaker that opens below threshold, or a breaker whose state is lost on restart |
| 4 | `enforce_quarantine` | the record is durable, names the device and the incident class, refuses the quarantined path, and is cleared only by a named actor | a quarantine that is lost, that is cleared without an actor, or that refuses a path it does not name |

**Why admission refusal is first.** It is the only action that prevents work
rather than interrupting it. A wrong refusal costs a run; a wrong termination
costs a run and whatever state it held.

**Why quarantine is last.** It persists across runs and needs a human to undo.

## 12. Evidence schema

`ENFORCEMENT_SCHEMA_VERSION = 1`. Every enforcement action writes one record
carrying:

| Group | Fields |
|---|---|
| Schema and contracts | `schemaVersion`, `enforcementContractVersion`, `safetyContractVersion`, `safetyPolicyVersion` |
| Authority | `authority`, `rollbackAvailable` |
| Correlation | `runId`, `placementId`, `manifestDigest`, `bootId`, `wallTimeUtc`, `monotonicNs`, `deviceIdentity` |
| Decision | `faultName`, `reasonCode`, `actionTaken`, `transition`, `leaseOutcome` |
| Comparison | `shadowPredictedAction`, `shadowPredictedTransition`, `predictionMatched` |
| Attribution | `actor` |

The correlation group is exactly what `0.3.0a9` lacked and `0.3.0a11` added. It
is why that build exists: **an action that cannot be tied to the prediction it
was meant to match is not evidence of anything**, and comparing shadow against
enforcement is the entire method of D.3.

**A prediction mismatch is fatal and is never suppressed.** The comparison exists
to find the cases where the two disagree; a run that hides them has destroyed its
own purpose.

## 13. What "frozen" will require

By the C.1 precedent, freezing means: the table is data, the document is
generated from it, both directions are asserted, every row is reachable in a test
without a GPU, and the version is recorded in `ENFORCEMENT_REVISIONS` with the
reason it supersedes its predecessor.

Freezing this contract authorises **no implementation**. Bounded canary
enforcement is separately authorised, stage by stage, and default enforcement is
not authorised at all.
