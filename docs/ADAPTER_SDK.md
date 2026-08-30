# Model Adapter SDK — contract

**Status: FROZEN.** Reviewed, amended, and the manifest-schema question settled
in favour of separate readable and executable schema sets. Implementation may
begin against this document; changes to it require the same review.

**Amended after implementation** (reviewed and approved): §7 no longer
renumbers the four pre-existing manifest failures — they keep
`MANIFEST_ERROR`/66. §9 gains `ADAPTER_UNQUALIFIED` and `MANIFEST_ERROR`.

Baseline captured at `d76334a` on the qualified machine. Every number in
[Acceptance](#10-acceptance-criteria) comes from that run.

---

## 1. Scope and non-goals

### In scope

The first milestone is **internal restructuring plus fail-closed rejection**. It
adds no execution capability.

- A stable adapter interface, and Mistral moved behind it unchanged.
- Deterministic adapter resolution from the checkpoint.
- Rejection of unsupported architectures **before any GPU allocation**, on both
  the planning path and the direct-worker path.
- Adapter identity pinned into new placement manifests.
- Removal of the premature Llama declaration.

### Explicitly not in scope

Llama execution. Qwen2, Gemma, Phi. Quantization of any kind. Mixture-of-experts.
Multimodal. Encoder-decoder, embedding or reranking models. Alternative serving
backends. FP16. Sampling. Batching. Any change to CLI, API, console routes or UI
workflows.

### The state being corrected

`SUPPORTED_ARCHITECTURES = {"MistralForCausalLM", "LlamaForCausalLM"}` exists at
[model_inspect.py:179](../runtime/serving/model_inspect.py) and is **referenced
nowhere**. There is no architecture gate today. A Llama checkpoint currently
inspects, plans, allocates VRAM on both cards, loads weights, and fails when
`MistralStage` builds Mistral layers from a Llama config. This milestone makes
that a refusal before allocation.

---

## 2. Three status dimensions, never combined

A single "supported" flag cannot express "we understand this model, but it will
not fit" or "this adapter works but has never been qualified on hardware".

### `compatibilityStatus` — can this checkpoint be executed at all?

| Value | Meaning |
|---|---|
| `SUPPORTED` | An installed adapter claims this architecture and the checkpoint satisfies it |
| `UNSUPPORTED_ARCHITECTURE` | No installed adapter claims this architecture |
| `INVALID_CHECKPOINT` | Architecture recognised, checkpoint malformed: missing tensors, discontiguous layers, unreadable config |
| `ADAPTER_UNAVAILABLE` | An adapter is registered for this architecture but is not installed or failed to load |
| `CONVERSION_REQUIRED` | Recognised, but in a format this build cannot read directly |
| `CUSTOM_CODE_REFUSED` | The checkpoint requires executing repository-supplied Python |

### `qualificationStatus` — has this adapter been proven on hardware?

| Value | Meaning |
|---|---|
| `HARDWARE_QUALIFIED` | A qualification record matches this exact situation |
| `UNQUALIFIED` | No matching record |

### Qualification is default-deny

An `UNQUALIFIED` adapter may be **inspected and planned**. It may **not** be
executed by `run`, `chat`, `serve` or the console. Execution requires an
explicit qualification mode or policy override, and the audit trail records that
the override was used, by whom, and against which record.

This inverts the previous draft, where unqualified adapters could run freely.

### Qualification is scoped, not a label on a name

A record is **not** "mistral@1 is qualified". It is a tuple, and a change to any
element makes it no longer apply:

```
modelFingerprint      ff74ccb7…       the checkpoint
adapterId             mistral
adapterVersion        "1"
adapterConfigDigest   …               the config that drove construction
openmyceliumVersion   0.3.0a4
openmyceliumContent   …               sha256 of the installed .py files
mcclVersion           0.2.0a3
mcclContent           …               sha256 of the installed .py files
transport             host-staged-xvendor
cudaRuntime           torch 2.11.0+cu128
rocmRuntime           torch 2.10.0+rocm7.0
topology              nvidia:<identity> + amd:<identity>, boundary after layer 19
```

### Amendment: content digests, not version labels

`openmyceliumContent` and `mcclContent` are additions to the tuple as originally
frozen, and they close the same hole the scoping exists to close.

**A version string does not identify a build.** The Adapter SDK milestone alone
produced seven wheels; six of them are recorded as non-releasable. A wheel
rebuilt under a version that already has a record would inherit qualification it
was never measured against — which is exactly "mistral@1 is qualified" wearing a
different hat.

**MCCL needs it more than OpenMycelium does.** MCCL owns the wire protocol and
the transport. A change there moves the boundary bytes, and every byte-exactness
claim in this project — the `c1467cd33c52032932ae4a39661a8136` digest and
everything resting on it — is a claim about those bytes. Carrying only
`mcclVersion` would have left the label-shaped hole open in precisely the layer
where it does the most damage.

Both digests are computed the way `installedContentSha256` already was: over the
installed distribution's `RECORD`-listed `.py` files, name and content, sorted.
Not over the wheel, which is usually deleted after installation.

A source checkout has no installed distribution to digest and receives
`source-checkout:<commit>` for both. That is a sentinel, not a blank: every field
of the tuple must be *something*, a checkout genuinely is a different build from
any wheel, and a blank would be indistinguishable from "not filled in yet".

### Boot identity stays out

`bootId` is deliberately **not** in the tuple. Qualification must survive a
reboot — nothing a restart changes is part of what was measured, and requalifying
after every restart would make qualification a formality rather than evidence.
This was checked rather than assumed: the `0.3.0a4` record was written before a
restart and resolved to the same situation digest afterwards.

`bootId` remains in event records, where it belongs. Event timestamps are only
comparable within one boot, so the boot identity is what makes a monotonic clock
reading meaningful — a different concern from qualification, and one that would
be broken by conflating the two.

A different checkpoint of the same architecture is unqualified. The same
checkpoint after a torch upgrade is unqualified. The same everything on a
different pair of cards is unqualified. That is the point: the previous draft's
label would have claimed qualification for situations never tested.

### Bootstrapping

`mistral@1` is **`UNQUALIFIED` until the post-refactor hardware gate passes**.
The gate itself must therefore run in qualification mode — that is what
qualification mode is for. The record is written only after the gate passes, and
the acceptance run is the evidence for it.

### `feasibilityStatus` — will it fit and run here, now?

| Value | Meaning |
|---|---|
| `FEASIBLE` | A placement compiles within the current budgets |
| `INSUFFICIENT_MEMORY` | Aggregated capacity across the available GPUs is not enough |
| `BACKEND_UNAVAILABLE` | A required runtime or device is missing |

Feasibility is a property of this machine at this moment. It changes when a GPU
is busy; compatibility does not.

**A model may be `SUPPORTED` + `HARDWARE_QUALIFIED` + `INSUFFICIENT_MEMORY`.**
That is a normal, well-formed answer.

---

## 3. Deterministic adapter resolution

Resolution reads **only the checkpoint**:

1. **Every entry** in `config["architectures"]`, not just the first
2. `config["model_type"]`
3. Architecture-driving configuration (§4)
4. Required tensor names and structure

The whole list is evaluated. A checkpoint declaring
`["FooForCausalLM", "MistralForCausalLM"]` is a checkpoint that claims to be
both, and taking only element zero would silently ignore half of what it says.
If more than one installed adapter claims any entry, the result is
`AMBIGUOUS_ADAPTER`.

It **never** reads the model directory name, the repository name, a user-supplied
adapter id, a CLI flag, or an environment variable. A checkpoint copied to a
directory called `llama-7b` resolves by its contents.

### Fail closed

| Matches | Outcome |
|---|---|
| Exactly one | That adapter |
| Zero | `UNSUPPORTED_ARCHITECTURE` |
| More than one | `AMBIGUOUS_ADAPTER` — a registry defect, never resolved by precedence |

Ambiguity is not broken by ordering, registration time or specificity. Two
adapters claiming one architecture is a bug in the registry, and silently
picking one would hide it.

### Interface

```python
class ModelAdapter:
    adapter_id: str           # "mistral"
    adapter_version: str      # "1"  -- persisted, digest-covered
    adapter_api_version: int  # 1    -- which SDK interface this implements
    architectures: frozenset  # {"MistralForCausalLM"}

    def claims(self, config: dict) -> bool: ...
    def validate_checkpoint(self, config: dict, tensors: Sequence) -> list[str]: ...
    def config_digest(self, config: dict) -> str: ...
    def identify_layers(self, tensors: Sequence) -> dict: ...
    def build_stage(self, config_path: str, spec, torch) -> Any: ...
    def create_cache(self, config: dict, spec) -> Any: ...
    def tokenizer(self, model_path: str) -> Any: ...
    def numerical_contract(self) -> dict: ...
```

`build_stage` returns today's `MistralStage`, unchanged.

---

## 4. Adapter identity and `adapterConfigDigest`

### Identity

```
adapterId          "mistral"     string
adapterVersion     "1"           string: persisted and digest-covered
adapterApiVersion  1             integer: the SDK interface version
```

`adapterVersion` is a string because it is written into manifests and covered
by `manifestDigest`; a string will not be reformatted by a JSON round-trip.
`adapterApiVersion` is an integer describing which SDK interface an adapter
implements, and is not persisted in manifests.

Written as `mistral@1` in messages. `adapterVersion` increments when the adapter
changes in a way that could alter numerical output — a different attention
implementation, a changed RoPE construction, a different default dtype. It does
**not** increment for refactoring, logging or error-message changes.

### `adapterConfigDigest`

`MistralStage` passes the entire `config.json` to `MistralConfig(**raw)`, so
**every key is architecture-driving by construction**. Digesting a hand-picked
subset would let an unlisted key change behaviour without changing the digest.

The digest therefore covers the whole config with a small, explicit exclusion
list of keys that provably do not affect construction:

```
excluded: transformers_version, _name_or_path
```

Only these two. An earlier draft also excluded `torch_dtype_str` and
`architectures_note`; neither exists in the validated checkpoint and neither was
proven metadata-only by reading executable code, so they are hashed like any
other key. Hashing an unknown key is safe; skipping one is not.

`architectures`, `model_type` and `torch_dtype` are **included**: they select the
adapter and the compute dtype.

Canonical form:

```
1. Load config.json as JSON.
2. Remove the excluded keys, if present.
3. Serialise:  json.dumps(obj, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True)
4. Encode UTF-8.
5. sha256, lowercase hex.
```

This matches `_canonical()` in `placement.py`, so the project has one canonical
JSON form rather than two.

For the validated checkpoint the digest covers these 22 keys:

```
architectures, attention_dropout, bos_token_id, eos_token_id, head_dim,
hidden_act, hidden_size, initializer_range, intermediate_size,
max_position_embeddings, model_type, num_attention_heads, num_hidden_layers,
num_key_value_heads, rms_norm_eps, rope_theta, sliding_window,
tie_word_embeddings, torch_dtype, use_cache, vocab_size
```

minus `transformers_version`.

---

## 5. Enforcement order

```
inspect                    read-only, always answers, never raises on unsupported
  ↓
resolve adapter            from checkpoint contents only
  ↓
require_executable_adapter ← REJECTS HERE, before any allocation
  ↓
create placement
  ↓
digest manifest            adapter fields covered
  ↓
spawn worker
  ↓
verify manifest digest     exactly as stored, nothing injected
  ↓
validate_for_worker        ← REJECTS HERE, before stage construction
  ↓
build stage / load weights
```

### Two enforcement points, because there are two ways in

`create_placement()` alone is **not sufficient**. A worker can be started
directly with an existing manifest — `pipeline_run.py` does exactly that via
`load_placement()` at [pipeline_run.py:1278](../runtime/serving/pipeline_run.py),
and `scripts/repro/boundary_exact_clean.sh` invokes `forward_pass.py` that way
in this project's own qualification harness.

| Point | Guards | Rejects |
|---|---|---|
| `require_executable_adapter()` | `plan`, `run`, `chat`, `serve`, console | unsupported architecture |
| `validate_for_worker()` | direct worker invocation | manifest with no pinned adapter identity |

### What each rejection actually guarantees

The two are not observable in the same way, and an earlier draft wrongly claimed
they were.

**Planning rejection** — `require_executable_adapter()`:

- No worker process is spawned.
- No device context is created and no VRAM is allocated.

**Direct-worker rejection** — `validate_for_worker()`:

- A worker process **necessarily exists**; it was invoked directly. "No worker spawned" cannot apply here.
- It must exit **before stage construction and before any weight allocation**.
- No orphan process and no persistent VRAM allocation may remain afterwards.

The test for the direct-worker path therefore samples VRAM before and after,
waits for the process to exit, and asserts a non-zero exit code with residency
returned to its pre-invocation level — not that nothing was started.

---

## 6. Manifest behaviour

### New manifests

```json
{
  "schemaVersion": 2,
  "adapterId": "mistral",
  "adapterVersion": "1",
  "adapterConfigDigest": "…64 hex…"
}
```

Top-level `schemaVersion` goes from `1` to `2`. The nested `fabric.schemaVersion`
is independent and stays as it is — it is already `2` for unrelated reasons, and
the two version the two different things.

These are **covered by `manifestDigest`**. `_digest()` in `placement.py` hashes
the whole manifest minus `manifestDigest` itself, so no allowlist needs
updating, and a manifest with the adapter fields stripped fails validation
rather than executing.

### Legacy manifests

A legacy manifest is one with `schemaVersion: 1` and no `adapterId`.

**`validate_manifest()` must accept both schema versions.** It currently does an
exact equality check:

```python
if manifest.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
    raise ManifestError(...)
```

Bumping `MANIFEST_SCHEMA_VERSION` to `2` without changing that line makes every
v1 manifest fail validation outright — including `release/0.1.0a5/placement.json`
and every manifest inside the frozen gate evidence, all of which are v1. That
would break "legacy manifests remain readable and audit-replayable" in the same
change that promises it.

Required behaviour:

```python
CURRENT_MANIFEST_SCHEMA_VERSION    = 2
READABLE_MANIFEST_SCHEMA_VERSIONS  = frozenset({1, 2})
EXECUTABLE_MANIFEST_SCHEMA_VERSIONS = frozenset({2})
```

| Situation | Outcome |
|---|---|
| Producers write a manifest | always schema `2` |
| Schema `1`, integrity | digest verified against the file **as stored**; readable and audit-replayable |
| Schema `1`, execution | `LEGACY_UNPINNED_MANIFEST`, `remediation: REPLAN_REQUIRED` |
| Schema `2` missing adapter fields | `INVALID_MANIFEST` — an invalid v2, **not** treated as legacy |
| Any other schema version | `UNSUPPORTED_MANIFEST_SCHEMA` |
| Any version | no adapter defaults are ever injected |

The v2-missing-fields rule matters: silently demoting an incomplete v2 to
"legacy" would let a truncated or hand-edited manifest present itself as merely
old, and the remedy offered would be wrong.

### Validation order

Fixed, because the order is what protects the frozen evidence:

```
1. parse the raw manifest
2. recompute and verify its digest, without modifying it in any way
3. check membership of READABLE_MANIFEST_SCHEMA_VERSIONS
4. apply schema-specific structural validation
5. for worker execution, check EXECUTABLE_MANIFEST_SCHEMA_VERSIONS
6. validate adapter identity against the checkpoint
```

Step 2 precedes every other decision. Any step that added, defaulted or
normalised a field before it would change what is hashed and make a legitimate
file fail its own integrity check.

Integrity and executability are separate questions, and conflating them is what
would have destroyed the frozen evidence's validity.

- **Verified exactly as stored.** `validate_manifest()` recomputes the digest over the file as written.
- **No defaults are ever injected before verification.** Defaulting `adapterId` to `"mistral"` on load would make `_digest()` hash the injected field and the manifest would fail its own integrity check. This is the single most important rule here.
- **Inspectable.** `plan` output, the console's Plan screen and audit replay all read them.
- **Not executable.** `validate_for_worker()` raises a single error:

  ```json
  { "errorCode": "LEGACY_UNPINNED_MANIFEST", "remediation": "REPLAN_REQUIRED" }
  ```

  One code for the failure, remediation as a field. Two competing codes for one
  condition would make callers guess which to match on.

Frozen evidence — `release/0.1.0a5/placement.json` and the manifests inside the
gate evidence — continues to pass `validate_manifest()` byte-for-byte, because
nothing rewrites it.

Normal workflows plan before executing, so Mistral users see no change.

---

## 7. Worker checks

`validate_for_worker(manifest, inspection, spec, role)` requires all of:

| Check | Failure | Exit | New? |
|---|---|---|---|
| Manifest pins an adapter | `LEGACY_UNPINNED_MANIFEST` | 65 | new |
| Pinned `adapterId`/`adapterVersion` is installed | `ADAPTER_UNAVAILABLE` | 69 | new |
| Pinned adapter matches the resolved architecture | `ADAPTER_MISMATCH` | 65 | new |
| `adapterConfigDigest` matches the checkpoint now | `ADAPTER_MISMATCH` | 65 | new |
| `modelFingerprint` matches | `MANIFEST_ERROR` | 66 | **pre-existing** |
| Exclusive tensor ownership holds | `MANIFEST_ERROR` | 66 | **pre-existing** |
| Stage assignment exists for this role | `MANIFEST_ERROR` | 66 | **pre-existing** |
| Runtime role matches the stage's declared runtime | `MANIFEST_ERROR` | 66 | **pre-existing** |

### Amendment: pre-existing failures keep their existing code and exit status

An earlier revision of this table mapped the last four rows to
`ADAPTER_MISMATCH`/65 as well. That is withdrawn.

Those four conditions ship today as `ManifestError` with exit 66, and
`test_model_drift_is_rejected` asserts the current wording. Renumbering them
would change behaviour that already shipped in v0.1.0, for no gain: the
refusal, the message and the guarantee are unchanged, and only the label would
move. A caller that already branches on 66 would silently stop matching.

The rule is therefore: **`ADAPTER_MISMATCH`/65 applies only to the conditions
this milestone introduces** — a pinned identity no installed adapter provides,
and a pinned identity that no longer describes the checkpoint. Everything the
manifest could already fail on keeps `MANIFEST_ERROR`/66.

Ordering follows from the same decision. The model fingerprint already covers
the whole config, so it is checked **first** and keeps reporting config drift in
the words it always has; adapter identity is checked after it, and adds only the
case the fingerprint cannot see.

The `adapterConfigDigest` check is what catches a checkpoint edited between
planning and execution when the fingerprint is unchanged.

---

## 8. UI and API compatibility

**No existing route, request body, response field, gate or workflow changes.**

Additive only:

- `model inspect --json` gains `architecture`, `adapterId`, `adapterVersion`, `compatibilityStatus`, `qualificationStatus`, `compatibilityReason`.
- Placement manifests gain the three adapter fields.
- Audit events gain `adapterId`, `adapterVersion`.
- Console `/api/models` and `/api/placement` carry them through.

The console ignores unknown fields and renders `gates` by iteration rather than
by hard-coded names, so a future compatibility gate would appear without a UI
change. `schemaVersion` stays `1`: additive fields do not break a cached bundle.

**Acceptance:** every existing console workflow works unchanged with
Mistral-Nemo — same buttons, same routes, same request shapes, nothing removed
or renamed.

---

## 9. Stable error codes

Machine-readable, stable across versions, carried in JSON as `errorCode`.

| Code | Meaning | Exit |
|---|---|---|
| `UNSUPPORTED_ARCHITECTURE` | No installed adapter claims this architecture | 65 |
| `AMBIGUOUS_ADAPTER` | More than one adapter claims it — registry defect | 70 |
| `ADAPTER_UNAVAILABLE` | Registered but not installed or failed to load | 69 |
| `ADAPTER_MISMATCH` | Pinned identity disagrees with the checkpoint | 65 |
| `LEGACY_UNPINNED_MANIFEST` | Schema 1 manifest; `remediation: REPLAN_REQUIRED` | 65 |
| `INVALID_MANIFEST` | Schema 2 missing required adapter fields | 65 |
| `UNSUPPORTED_MANIFEST_SCHEMA` | Schema version outside the readable set | 65 |
| `INVALID_CHECKPOINT` | Architecture recognised, checkpoint malformed | 66 |
| `CUSTOM_CODE_REFUSED` | Requires executing repository-supplied Python | 77 |
| `ADAPTER_UNQUALIFIED` | No qualification record covers this situation; `remediation: QUALIFICATION_REQUIRED` | 65 |
| `MANIFEST_ERROR` | Pre-existing manifest failures (§7), unchanged from v0.1.0 | 66 |

Each message names the architecture found, the adapters installed, and the one
action that resolves it.

---

## 10. Acceptance criteria

Baseline captured at `d76334a`, boot `96203ff6-c5e7-4037-a0ab-d689c863e501`,
`openmycelium 0.2.0a5` / `mccl 0.2.0a3`, content
`74729745a44f79b13845786134852c3513cb6d89867d52009c8440ed488b0999`.

### Must reproduce exactly

| | Baseline |
|---|---|
| Greedy token ids | `[49256, 9332, 24227, 56455, 31587, 9985, 7523, 1750, 113422, 8832, 9055, 6056, 1408, 2801, 47910, 1307, 20534, 7176, 3816, 56309, 1317, 3398, 3486, 1505]` |
| Text | `Cross-vendor GPU inference allows running pre-trained machine learning models on different brands of GPUs without needing to retrain or` |
| Ownership | `[181, 182]`, 363 total, overlap 0 |
| Boundary | 2 transfers, `[1,1,5120]` bfloat16, 10240 B, sent = received = `c1467cd33c52032932ae4a39661a8136` |
| Model fingerprint | `ff74ccb7c5e616ddfa3ea53f4d201be9825fb02ce8673e45f863ab892adcc7be` |
| Boundary layer | after 19 |
| Orphan workers | 0 |
| VRAM released | CUDA ≤ ~1.3 GiB, ROCm ≤ ~100 MiB |

### Must fall in range

| | Baseline | Acceptance |
|---|---|---|
| Decode | 11.06 tok/s | 10.9 – 11.3 tok/s |
| TTFT | median **144.3 ms** of 142.4 / 144.3 / 161.8 | **median of ≥ 3 post-refactor sessions ≤ 173.2 ms** |

TTFT: at least three independent post-refactor sessions. Accept when the median
is no more than 20% above the baseline median of 144.3 ms — that is **173.2 ms**.
All samples are preserved, not just the median, so a change in spread is visible
even when the median passes.

The three unchanged-code runs spanned 14%, which is why equality would fail on
noise. Decode was 11.03–11.07 across the same runs and is held tightly.

### Amendment: TTFT is judged paired against the incumbent, not absolutely

The absolute rule above is **superseded as a gate** and retained as a historical
observation. The number 173.2 ms is not changed and not widened.

**Why.** The band it defines is 28.9 ms wide (144.3 → 173.2). The measured
within-build, run-to-run range on the qualified machine is **44–47 ms**. The
instrument's own noise exceeds the interval it is asked to resolve, and identical
code duly produced both a pass and a fail in different campaigns. TTFT also
drifts upward through a session — the same build measured 142.5 ms early and
169.1 ms late — so any protocol that measures one build before another charges
the second for the first's warm-up.

**The gate.** Measure the candidate **interleaved against the last sealed build**
in one campaign, in a position-balanced sequence, and accept on:

```
regression = (candidate_mean_ttft - incumbent_mean_ttft) / incumbent_mean_ttft
pass when regression <= 20%
```

The 20% is the tolerance this contract already used; it is applied to a paired
comparison instead of an absolute number. Position balance is checked, not
assumed: the two builds must have equal mean position in the sequence, or drift
is charged unevenly and the means are not comparable.

**Decode is unchanged** — median within `10.9 – 11.3 tok/s`, still a hard gate.
It held across every campaign and is a stable instrument.

**Adjacent-pair differences are diagnostics and never a threshold.** An earlier
draft of this amendment proposed accepting when the mixed-pair difference stayed
below the same-build pair spread. That is unsafe: a campaign yields two
same-build pairs, so that spread is a single observation of a noisy quantity, and
a campaign that happened to drift hard would have licensed a real regression.

**Campaign requirements.** At least **five observations per build**,
position-balanced interleaving, immutable side-by-side installations that are
never reinstalled mid-campaign, and a pre-registered machine state: idle GPUs, no
live workers, a settle period, and `bootId` captured **per run** with the campaign
invalidated if it changes. Every observation preserves its boot id, placement id
and digest, writer and sequence identity, package and content hashes, worker
outcome, exact output and ownership, and cleanup state.

Campaigns pre-registered with fewer observations stand as registered and are not
re-judged against this minimum.

**This is not a threshold widened after a failure.** The justification is the
same-build adjacent-pair measurements, which were pre-registered as drift
diagnostics before any candidate outcome was known and are independent of whether
any particular candidate passed. See `release/0.3.0a5/gates/gate-a-verdict.md`
for the campaign that established it.

`scripts/repro/paired_analysis.py` implements this rule and replays a preserved
campaign from its raw record without re-running it.

### Must newly hold

- A Llama checkpoint is rejected with `UNSUPPORTED_ARCHITECTURE` **and no GPU memory is allocated** — asserted by sampling VRAM before and after, and by no worker process appearing.
- An unknown architecture is likewise rejected.
- A legacy manifest passes `validate_manifest()` unchanged, is inspectable, and refuses to execute with `LEGACY_UNPINNED_MANIFEST` / `REPLAN_REQUIRED`.
- A direct `forward_pass.py` / `pipeline_run.py` invocation with a legacy manifest is refused **before stage construction**.
- `release/0.1.0a5/placement.json` and every manifest in frozen evidence still verifies.
- `manifestDigest` changes for new manifests; frozen digests do not.

### Method

`scripts/repro/console_wheel_gate.sh` runs **unchanged**, with `OM_REPO` set so
wheel paths resolve. If the gate needs editing to pass, the extraction was
wrong.

### Clarification: "under the same conditions" includes an idle machine

No threshold changes. This states what the baseline capture already did and the
procedure left implicit.

The baseline was measured on an otherwise idle machine. A campaign run
immediately after other GPU work measures a machine that has just been worked —
weights still being released, caches still warm or still cold in the wrong
places. That is a real number about the wrong thing.

Measured, not assumed. The same build, same machine, same three-session
procedure gave:

| Order | TTFT samples | Median | Spread |
|---|---|---|---|
| Campaign standalone | 140.3 / 142.5 / 172.5 | 142.5 ms | 23% |
| Campaign after two other GPU gates | 139.7 / 172.3 / 192.0 | 172.3 ms | 37% |

Both pass. The second passes by 0.9 ms on a metric whose own spread is 52 ms,
which is not a result to build on. The first sample is near-identical in both
runs; only the tail moves, which is what contention looks like.

The campaign therefore runs **first among the GPU gates and after a settle
period**, and refuses to start while any worker process is alive.
`scripts/repro/performance_campaign.sh` enforces both.

Baseline and comparison are preserved together under the new alpha's release
evidence once accepted — not before.

---

## 11. Resolved: the manifest-schema conflict

Bumping `schemaVersion` to `2` would have broken legacy readability, because
`validate_manifest()` did an exact equality check against a single constant.
Every v1 manifest would have failed **integrity** validation, not merely
execution — including `release/0.1.0a5/placement.json`, every manifest in the
frozen gate evidence, and the captured baseline, all confirmed top-level v1 with
`fabric.schemaVersion` independently at 2.

**Resolved in favour of separate readable and executable schema sets**, as
specified in §6. Integrity accepts `{1, 2}`; execution accepts `{2}`. A schema
version is thereby free to describe the manifest's shape, which is its purpose,
without integrity and executability being forced into one answer.

---

## Preserving imports

If `MistralStage` moves to `runtime/serving/adapters/mistral.py`, its current
module must re-export it:

```python
# runtime/serving/stage_model.py
from adapters.mistral import MistralStage  # noqa: F401  (compatibility re-export)
```

`pipeline_run.py` imports it at line 49, and anything outside this repository
may too. The re-export is temporary and removed only in a release that says so.

---

## Applied from review

| # | Amendment | Where |
|---|---|---|
| 1 | `adapterVersion` string, `adapterApiVersion` integer, manifest `schemaVersion` → 2 | §4, §6 |
| 2 | Qualification default-deny, scoped to a tuple, override audited | §2 |
| 3 | Direct-worker rejection: a worker exists and must exit before allocation | §5 |
| 4 | Every entry in `architectures`, not just the first | §3 |
| 5 | Digest excludes only `transformers_version` and `_name_or_path` | §4 |
| 6 | One error code, remediation as a field | §6, §9 |
| 7 | TTFT: median of ≥ 3 sessions ≤ 173.2 ms | §10 |
| 8 | `MistralStage` re-exported from its old module | above |
