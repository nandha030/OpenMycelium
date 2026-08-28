# Mycelium Intelligence v0 — measurement contract

**Status: frozen for step 1. Definition only; no behavioural change.**

This document fixes notation, event audit fields, coordinator enforcement, and
benchmark conditions *before* any instrumentation is written, so that results
cannot be reinterpreted after the fact.

Intelligence v0 is scoped to one question: **given a model and two accelerators,
which contiguous CUDA-first boundary should the Scheduler choose?** It is not a
general cost model, and it does not schedule multiple jobs.

---

## 1. Canonical boundary notation

Every report, manifest, event and log line uses these fields together. A bare
`k`, or a phrase like "boundary 20", is not permitted — it is ambiguous between
a layer index and a layer count, and that ambiguity has already produced one
off-by-one in an analysis.

```
boundaryAfterLayer = 19
cudaLayerStart     = 0
cudaLayerEnd       = 19
cudaLayerCount     = 20
rocmLayerStart     = 20
rocmLayerEnd       = 39
rocmLayerCount     = 20
stageOrientation   = cuda-first
modelLayerCount    = 40
```

### Invariants (checked, not assumed)

```
cudaLayerCount  = boundaryAfterLayer + 1
cudaLayerEnd    = boundaryAfterLayer
rocmLayerStart  = boundaryAfterLayer + 1
rocmLayerEnd    = modelLayerCount - 1
rocmLayerCount  = modelLayerCount - cudaLayerCount
cudaLayerCount + rocmLayerCount = modelLayerCount
0 <= boundaryAfterLayer < modelLayerCount - 1
```

### Stage orientation

`cuda-first` is the only supported value. The CUDA worker holds the embedding
and the ROCm worker holds the final norm and LM head; the ROCm worker is always
the listener. Reversing this is a **runner capability that does not exist**, not
a Scheduler parameter, so the enumerator must not emit `rocm-first` candidates.
Any value other than `cuda-first` is rejected rather than ignored.

---

## 2. Event audit fields

### Minimal tuple — on every post-validation event

Repeated on every line so each JSONL record is independently attributable
without replaying the stream from its start.

```
eventSchemaVersion    integer, independent of the MCCL wire protocol version
runId                 issued once by the coordinator, shared by both workers
placementId
manifestDigest
modelFingerprint
workerRole            cuda | rocm
deviceIdentity        from Fabric, e.g. nvidia:GPU-... or amd:pci-0000:04:00.0
stageOrientation
boundaryAfterLayer
eventSequence         monotonic within (runId, writerId) — see §4
wallTimeUtc           for human correlation only, never for durations
monotonicNs           CLOCK_MONOTONIC
bootId                /proc/sys/kernel/random/boot_id
writerId              one worker-process incarnation — see §3b
```

`eventSchemaVersion` is deliberately separate from the MCCL wire protocol, which
stays at version 1. Audit format and wire format change for different reasons
and must be able to change independently.

### Why `bootId` is mandatory

`CLOCK_MONOTONIC` restarts near zero on every WSL VM boot — measured at 2.929 s
on a VM whose uptime was 2.91 s. Two events from different boots can therefore
carry the *same* `monotonicNs`. Cross-process transfer timing relies on both
workers sharing one monotonic timeline, and this runtime has already been
observed losing its VM mid-run, so the timeline can end without warning.

**Rule:** a duration may be computed from two `monotonicNs` values only when
their `bootId` values are equal. Samples spanning a `bootId` change are
discarded, never rescaled.

### Full placement summary event — additionally

Emitted once per worker, immediately after manifest validation succeeds.

This is a **summary, not a self-contained record.** The complete evidence object
is the placement manifest; the summary carries a pointer to it and the digest
that binds the pointer to specific content.

```
manifestPath          immutable path or artifact id of the manifest
manifestDigest        already in the minimal tuple; repeated here deliberately,
                      so path and content are bound within one record
cudaLayerStart / cudaLayerEnd / cudaLayerCount     (this worker's range)
rocmLayerStart / rocmLayerEnd / rocmLayerCount
assignedTensorCount
assignedWeightBytes
budgetBytes
runtime               cuda | rocm
runtimeVersion        torch version and CUDA/HIP build
transport             host-staged-xvendor
fabricSnapshotAt      the snapshot the manifest was compiled against
```

Full per-tensor assignments stay in the manifest. Repeating 363 tensor names on
every event would bloat the stream without making any line more attributable
than the digest already does — but the stream alone is therefore *not* complete
evidence, and must not be presented as such. Reconstructing a run requires the
stream and the manifest it names.

### Pre-validation failures

A worker that fails **before** the manifest is validated has no verified
identity to report. It must not echo `placementId`, `manifestDigest` or
`modelFingerprint` from an unverified file, because doing so would launder
untrusted input into the audit trail as evidence.

```
eventSchemaVersion
runId                 known from the command line, not from the manifest
workerRole
placementPath
placementValidation = failed
failurePhase          read | schema | digest | fingerprint | ownership | role
failureReason
wallTimeUtc / monotonicNs / bootId / writerId
```

---

## 3. Coordinator enforcement

The coordinator rejects a run when any of these hold:

* workers report different `placementId`
* workers report different `manifestDigest`
* workers report different `modelFingerprint`
* two workers claim the same `workerRole`
* a worker's `deviceIdentity` is absent from **the Fabric snapshot embedded in
  the manifest** — not from a live snapshot (see below)
* a worker's stage assignment disagrees with the manifest for its role
* `eventSequence` regresses or repeats within a `(runId, writerId)`
* a second `writerId` claims a `workerRole` already occupied
* a post-validation event omits any field of the minimal tuple
* `bootId` changes within a run
* `stageOrientation` is anything other than `cuda-first`

Rejection means the run is failed and reported, never repaired.

### Fabric validation is against the manifest's snapshot, not a live probe

Validating a worker's `deviceIdentity` against a *live* Fabric snapshot is a
time-of-check/time-of-use error. The manifest was compiled against a particular
snapshot; a later probe may legitimately differ — free VRAM moves constantly, a
device may be busy, and the cache may have been refreshed between planning and
launch. Failing a run because the world changed after the decision was made
would reject correct runs.

So there are two separate checks, and they are not interchangeable:

| Check | Against | Failure means |
|---|---|---|
| **Placement validity** | the Fabric snapshot embedded in the manifest | the manifest is internally inconsistent — reject the run |
| **Device liveness** | a live Fabric probe at launch | the hardware changed since planning — replan, or fail with that reason |

Liveness is a health check with its own outcome, not part of manifest
validation.

---

## 3b. `writerId` — ratified into event schema v1

Identifies **one worker-process incarnation**.

* generated by the worker at process start, UUIDv4
* shared by every event from that process, including pre-validation failures
* a worker restart produces a new `writerId`
* the coordinator permits exactly one `writerId` per `(runId, workerRole)`;
  a second writer claiming an occupied role fails the run
* correlation metadata, not authentication

It exists because the duplicate-role rule is otherwise unenforceable: a
duplicate worker shares `runId` and `bootId` with the legitimate one, so only a
per-process value separates them. A duplicate that also forged a
non-conflicting `eventSequence` would be invisible without it.

## 4. `eventSequence` scoping

Scoped per `(runId, writerId)`, starting at 1. Role uniqueness is enforced
separately, by the one-`writerId`-per-role rule above; the two checks answer
different questions and neither substitutes for the other. A single global counter is not
possible: two workers are separate processes with no shared state, and both
would emit sequence 1. The coordinator checks monotonicity **within** each
worker's stream and does not compare sequence numbers across workers — ordering
between workers comes from `monotonicNs` within one `bootId`.

---

## 5. Benchmark-condition record

Frozen once per benchmark run and attached to every measurement drawn from it.

```
runId, placementId, manifestDigest, modelFingerprint
promptTokenCount
promptIdsHash            sha256 over the *token ids* after templating
tokenizerIdentity        tokenizer files hash + fix_mistral_regex flag
chatTemplateApplied      bool
batchSize
contextLength
warmState                cold-start | first-request | steady-state   (§6)
discardedWarmups         integer
precision                bf16
attentionBackendRequested
attentionBackendObserved  what the kernel actually used
timingMethod             global-sync-control | stream-event
torchVersionCuda, torchVersionRocm
cudaDriverVersion, rocmVersion
fabricSnapshotAt, fabricDeviceIdentities[], fabricIdentityConfidence[]
bootId
randomSeed
decodingPolicy           greedy-argmax (temperature 0, do_sample false)
gpuClockMHzStart / End, gpuTempCStart / End   (per device, where available)
powerSource              per device: nvidia-smi | unavailable
```

### Canonical hashing

Two implementations must produce the same digest for the same workload, so the
encoding is specified rather than left to whoever writes the code first.

**`promptIdsHash`**

```
bytes  = b"".join(struct.pack("<I", int(id)) for id in token_ids)
digest = sha256(bytes).hexdigest()
```

Token ids after templating, as **unsigned 32-bit little-endian**, in sequence
order, with no separators, length prefix or trailing padding. Ids outside
`[0, 2**32)` are a fault, not a wraparound.

**`tokenizerIdentity`**

```
files  = ["tokenizer.json", "tokenizer_config.json",
          "special_tokens_map.json"]            # this fixed order
parts  = []
for name in files:                              # missing files contribute
    parts.append(name.encode("utf-8"))          # their name and a zero length
    blob = read(name) if exists(name) else b""
    parts.append(struct.pack("<Q", len(blob)))
    parts.append(blob)
parts.append(b"fix_mistral_regex=" + (b"1" if flag else b"0"))
digest = sha256(b"".join(parts)).hexdigest()
```

Name and length are hashed alongside content so that moving bytes between files
cannot produce a collision, and a missing file is distinguishable from an empty
one.

`promptIdsHash` hashes token ids rather than raw text on purpose: the same
string tokenises differently under different tokenizer settings. This project
already shipped a run where `fix_mistral_regex` changed the ids for identical
text, so text alone does not identify the workload — which is also why the flag
is folded into `tokenizerIdentity`.

`attentionBackendObserved` must be read back from the runtime, not inferred from
what was requested. PyTorch falls back per call, and a silent fallback to eager
would invalidate a workspace measurement while appearing to succeed.

Energy is recorded where available and **excluded from any scoring function**.
AMD power telemetry does not exist under WSL — no `amdgpu` module, no
`/dev/kfd`, no hwmon — so a joules-per-token objective would be computed from
one of two devices.

---

## 6. Warm-state populations

Three distinct populations, never pooled:

| State | Definition |
|---|---|
| `cold-start` | process start through both stages resident and READY, including BF16 kernel selection (measured 2.5 s – 62.6 s) |
| `first-request` | the first request after READY |
| `steady-state` | requests after `discardedWarmups` further requests |

These are separated because they are not the same distribution. One observed
session recorded first-request TTFT of 1001 ms on a 14-token prompt and 259 ms
on the following 72-token prompt — a *longer* prompt four times faster. Pooling
those into a single "noise" figure produced a variance estimate that was
measuring session position, not measurement error.

The steady-state noise floor is established on the existing eager `b=19`
configuration and **published before any candidate is run**.

---

## 7. Prediction model

```
predicted TTFT(b, S) =
      CUDA embedding(S)
    + Σ CUDA prefill layer time(i, S)   for i in 0..b
    + transfer(S * hidden * 2 bytes, cuda -> rocm)
    + Σ ROCm prefill layer time(i, S)   for i in b+1..39
    + ROCm final norm + LM head(1)
    + fixed runtime overhead

predicted decodeStepTime(b, C) =
      CUDA embedding(1)
    + Σ CUDA decode layer time(i, cacheLength = C)   for i in 0..b
    + transfer(hidden * 2 bytes, cuda -> rocm)
    + Σ ROCm decode layer time(i, cacheLength = C)   for i in b+1..39
    + ROCm final norm + LM head(1)
    + fixed runtime overhead
```

Prefill and decode are profiled separately: prefill is query length `S` against
an empty cache, decode is query length 1 against a cache of length `C`. One
curve cannot model both.

**This formula assumes no stage overlap.** Both stages are serial today. If
pipelining or batching is introduced, the sums become maxima and every
prediction in this contract is void until refitted.

---

## 8. Profiling method

Per-layer timing uses **stream events** on the executing stream, meaning exactly
this and nothing looser:

```python
start = torch.cuda.Event(enable_timing=True)
end   = torch.cuda.Event(enable_timing=True)
start.record(stream)          # the operation's actual stream, not the default
operation(stream)
end.record(stream)
end.synchronize()             # event synchronisation only
elapsed_ms = start.elapsed_time(end)
```

Both events must be recorded on the stream the operation actually runs on.
Recording on the default stream while the work executes elsewhere times an
unrelated queue. **Event synchronisation is permitted; device-wide
synchronisation is not** — `torch.cuda.synchronize()` appears only in the
control path.

The existing code path uses `torch.cuda.synchronize()` — a full device
synchronisation — on every timed region. Event-based timing is therefore a *new*
path, not the preservation of an existing property. The global-sync path is
retained as `timingMethod = global-sync-control`, and the difference between the
two methods is measured and reported before either is trusted for candidate
selection.

Layer-to-layer differences are **not** assumed to be noise. Layers share shapes
but not weights or activation distributions. Recorded per layer:

* per-repetition latency (not only an aggregate)
* within-layer variance — the only valid estimator of measurement noise
* between-layer variance — reported separately, as possible signal
* clock and temperature at the sample

---

## 9. Execution discipline

Candidates are executed **interleaved and in randomised order**, with warm-up
rounds between them. Running all repetitions of one boundary before the next
would confound boundary with time, temperature, clock state and WSL activity.

### Statistical unit: the session, not the request

Repeated requests inside one loaded session are **not independent replicates**.
They share a kernel cache, an allocator state, a thermal state and one VM
lifetime, so their errors are correlated. Treating them as independent would
shrink confidence intervals by roughly the square root of the request count and
manufacture significance that is not there.

The design is hierarchical:

```
top level     independent worker/session launches per candidate  (replicates)
within        R steady-state requests after D discarded warm-ups (clustered)
ordering      candidate sessions randomised and interleaved
```

* **Replicate** = one independent session launch (both workers started fresh).
* **Observation** = one steady-state request within a session.
* Confidence intervals use a **cluster bootstrap that resamples sessions**, not
  requests. Requests are resampled only within a resampled session, or a
  mixed-effects model with session as a random intercept is used.
* Minimum: **≥5 independent sessions per candidate**, ≥10 steady-state requests
  per session. A candidate measured in a single session has no usable interval,
  however many requests it contains.
* `cold-start` and `first-request` observations are excluded from steady-state
  statistics entirely (§6), not down-weighted.

---

## 10. Acceptance criteria

Pre-registered.

1. **Noise floor published first** — steady-state p50 and 95% CI for TTFT and
   decode rate at `b=19`, before any candidate runs.
2. **Prediction accuracy** — predicted TTFT and decode within ±15% of observed
   for every feasible candidate.
3. **Ranking** — the predicted best is a member of the observed statistically
   tied best set, defined below. Exact ordering is *not* required where
   confidence intervals overlap.
4. **Improvement** — claimed only when the improvement's 95% CI excludes zero.
5. **Feasibility** — every executed candidate stays within its admissible
   budget, verified by peak-memory measurement, with zero OOMs.
6. **Correctness** — the 32-step KV oracle passes at the selected boundary. A
   faster split that changes the output is not an optimisation.

### Tied-best set and rank statistic — fixed before any results

**Statistically tied.** Two candidates `a` and `b` are tied when the 95%
cluster-bootstrap interval of their *paired difference* `metric(a) - metric(b)`
contains zero. The difference is computed on the session-level means, using the
same resampled session draw for both candidates so the pairing is preserved.

**Observed tied-best set.** Rank candidates by session-level mean. The set
contains the observed best candidate and every candidate tied with it under the
test above. Acceptance criterion 3 requires only that the *predicted* best falls
inside this set.

**Rank statistic: Kendall's tau-b**, reported with a p-value, between predicted
and observed orderings across all feasible candidates. Tau-b is chosen over
Spearman because the candidate count is small (3–13) and ties are expected;
tau-b handles ties in both orderings explicitly. Spearman is used elsewhere in
this project for logit-candidate agreement over 50+ items, which is a different
regime — the two are not interchangeable and the choice is recorded here so it
cannot be swapped after seeing results.

### Admissible budget

Free VRAM is a snapshot, not an allocation limit:

```
admissibleBudget = freeVRAMAtBaseline
                 - runtimeReserve
                 - measuredPeakWorkspace
                 - safetyMargin
                 - existingLeases
```

**`freeVRAMAtBaseline` is sampled before the worker process allocates
anything** — before the runtime context is created, before weights load. Reading
free VRAM after allocation and then subtracting `runtimeReserve` or
`measuredPeakWorkspace` double-counts memory that the reading already excludes,
and silently produces a budget smaller than reality.

The sampling point is recorded alongside the value, so a budget derived from a
post-allocation reading is identifiable as invalid rather than merely wrong.

Enumeration uses `admissibleBudget`. Using raw free VRAM widens the candidate
set by moving candidates closer to OOM, which is not the same as making them
admissible.

### Failed-session policy

Every failure is recorded. Only qualified infrastructure failures are replaced.

Per candidate:

* target **≥5 complete successful sessions**; maximum **8 total attempts**
* every attempt receives a new `runId` and stays in the dataset
* a retry is queued at the end of the randomised round, never run immediately
* primary latency and throughput analysis uses complete successful sessions
* reliability analysis uses every attempted session
* partial-session measurements are diagnostic only

| Failure | Handling |
|---|---|
| WSL/DXG fault, worker crash, infrastructure timeout | attempted failure; retry within the 8-attempt limit |
| OOM | candidate fails feasibility immediately — never retried into passing |
| Wrong output or failed KV oracle | candidate disqualified — never retried away |
| Manifest or audit violation | campaign invalidated until the cause is corrected |
| <5 successes after 8 attempts | `insufficient-reliability`; excluded from best-placement selection |

Every result reports **both**:

```
conditional performance among successful sessions
successful sessions / total attempted sessions
```

Reporting only the first would let a fast but unreliable boundary win. This
matters concretely: a two-GPU run failed once during warm-up with
`dxgkio_query_adapter_info: Ioctl failed: -2` and succeeded on the next attempt
with identical code and inputs, so infrastructure failure is a known hazard of
this campaign, not a hypothetical one.

### Negative result policy

If no feasible split is statistically distinguishable from `b=19`, the finding
is reported as: *per-layer cost is near-symmetric across these two devices, so
layer-count balance is already near-optimal; the value of Intelligence v0 is its
feasibility filter and calibrated prediction, not the split it chooses.*

That is a successful milestone. Intelligence v0 succeeds by predicting
feasibility and performance honestly, not by producing a positive number.

---

## 11. Prerequisites tracked separately

**SDPA is a prerequisite, not part of this contract.** The claim that it removes
the 1385 MiB eager-attention workspace is currently a hypothesis. It must
independently pass:

* kernel availability on both the CUDA and ROCm builds
* prefill at sequence lengths through 2048
* cached-decode correctness
* peak-workspace measurement on both vendors
* the 32-step KV oracle
* no silent fallback to eager, asserted from the observed backend

Until it does, the feasible candidate set under eager attention and a 14 GiB
budget is `boundaryAfterLayer ∈ {18, 19, 20}` — a 5% shift in layer ownership,
which may be too narrow for any effect to be resolvable.
