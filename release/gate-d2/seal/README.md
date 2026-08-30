# Gate D.2 — seal

**Status: SEALED.** Canonical artifact `0.3.0a10`. This directory records the
identities D.2 was missing and the evidence for the build that carries them.

`0.3.0a9`, the build D.2 was reviewed on, is **non-releasable** and recorded as
such. It is not the sealed artifact.

---

## Why there is a new build

D.2 was accepted pending sealing, and sealing required a confirmation the
`0.3.0a9` record could not support: that every shadow observation carries a
schema version, a run id, a placement id, a boot id, device identity and a
timestamp. It carried two of those.

```
field              0.3.0a9   0.3.0a10
  schemaVersion      no        yes  (SHADOW_SCHEMA_VERSION = 1)
  runId              no        yes
  placementId        no        yes
  manifestDigest     no        yes
  modelFingerprint   no        yes
  bootId             yes       yes
  wallTimeUtc        no        yes
  monotonicNs        no        yes
  device identity    yes       yes
```

An observation that cannot be tied to a run, a placement, a boot and a moment
cannot be matched against the audit trail of the run it describes — and Gate D.3
compares each enforcement action against the shadow prediction for the same run.
Without correlation the record is an anecdote, not evidence, so this is a
prerequisite for D.3 rather than a tidy-up.

`REQUIRED_FIELDS` is now canonical in `runtime/safety/shadow.py`, and the writer
**refuses to emit** a record missing any of it. Fields are present and empty
rather than absent when no manifest is bound yet, so a reader can tell "no
placement yet" from "this writer did not know about placements".

### A second defect, found while sealing

Inserting `_shadow_observe` as a new module-level function had **split
`prepare_placement`**, stranding its `run_id` assignment after a `return` where
nothing reached it.

It was **dead code, not a live fault**: `Coordinator.__init__` also issues a run
id, and every caller constructs a coordinator before reading `args.run_id`. But
D.2 promised the `off` path was unchanged, and a milestone that silently deletes
a statement from the path it promised not to touch has not kept that promise.
The block is now placed before the observation — where the observation can carry
the id — and two scope-review checks assert it stays there, both of which fail if
the defect is reintroduced.

---

## Identities

| | |
|---|---|
| Version | `0.3.0a10` |
| Commit | `44b09d2b450d553a109fbbd12d8b2564f23d45af` (see `commit.txt`) |
| Merge commit | `5287154e59ade01d2eea8cbcf35cfa53933945ad` |
| Tag | `v0.3.0a10` (annotated, on the merge commit) |
| Wheel sha256 | `36e6cd8f5a1047df47f84c19237a170f22453d8d5d67df0d0be610c8de076ac8` |
| Wheel size | 823 571 bytes |
| Installed content sha256 | `9053f992c39cbbb41a0af0969b03f8c6de19659f45d1a19ccd29d9c2a74f3466` |
| Installed python files | 60 |
| MCCL version | `0.2.0a3` |
| MCCL content sha256 | `5f2028695fa830417793ddcfecf90aa33bfd0b9e64775115058839d94d12631d` |
| MCCL python files | 19 |
| Safety contract | `safety-contract-1.1` |
| Safety policy | `safety-policy-1-provisional` |
| Shadow observation schema | `SHADOW_SCHEMA_VERSION = 1` |

The MCCL content digest is unchanged from the sealed `0.3.0a5` baseline: the
transport was not touched by this milestone, and that is asserted by the digest
rather than by the version label.

**Identity is asserted on installed content, never on the wheel file.** Three
rebuilds of one source give three wheel byte streams at identical size and one
content digest. The wheel hash identifies a file; the content digest identifies
the product.

---

## Tests

| Suite | Result |
|---|---|
| Checkout unit suite | **443 tests, 0 failures** — `unit-suite.txt` |
| — of which safety | 180 |
| — of which shadow mode | 39 |
| Installed-wheel suite | **220 tests, 0 failures** — `installed-wheel-tests.txt` |

The two totals differ for reasons that are all accounted for, and the accounting
is exact:

```
443  checkout
-111  mccl            ships as its own distribution
- 52  event gate      runtime/cli tests are not shipped
- 23  mycelium        not shipped
= 257 shipped tests
-  25  test_manifest_schema.py   needs release/0.1.0a5/placement.json,
                                 frozen evidence a wheel must not ship
-  12  ProseMatchesDataTests     checks the safety document against the
                                 transition table; a wheel ships no document
= 220 ran against the installed wheel
```

Both exclusions are **scope, not waiver**, and both are printed by the runner
rather than absorbed into a green total. `ProseMatchesDataTests` skips itself
only after proving positively that it is inside an installed package; a missing
document anywhere else is a hard failure. A class-level skip removes its tests
from unittest's "Ran" count entirely, so the runner reports skips explicitly —
a total that shrinks with nothing to show for it is how a gate comes to pass
because its evidence vanished.

---

## Merge and tag

The order `GATE_PROCESS.md` fixes — scope review, merge, ancestry verification,
tag — was followed, and each step has preserved output rather than a recollection
that it happened.

| Step | Evidence |
|---|---|
| Branch scope review | `scope-review-branch.txt`, 9 checks, **before** the merge — see the correction below |
| Merge | `5287154`, `--no-ff`; the gates validated these exact commit identities, so a squash would produce ids nothing has validated |
| Ancestry verification | `ancestry.txt`, 8 checks — 12 validated commits reachable by their own ids, all four tags reachable, merged tree identical to the branch tree |
| Tag | `v0.3.0a10`, annotated, on the merge commit — the same convention as `v0.3.0a5` |

### Two harness corrections, after the fact

**The branch scope review matched its own definition.** It holds the list of
symbols it forbids, so it is a code file containing every one of them. Its first
run passed only because it was still untracked and absent from the diff; once
committed it failed on itself. Worse, I regenerated its output and committed
that as evidence without reading it, so the file merged under this seal recorded
`FAILED` while the summary above cited it as passing. Corrected in `d3f37a0`:
three exact paths are exempt — the rule document, the checker, the output of the
checker — a ninth check asserts there are exactly three, and re-running the
corrected checker over the same range `ab6f2554...c230057` passes.

**The merged content was never in question.** The finding was the reviewer
matching itself, not scope creep, and nothing about the artifact, the merge or
the tag changes. What does change is the honesty of the record: a gate result
produced and filed unread is not a result.

**The ancestry script** needed a fix on its second run: it had taken the tip of
`main` as the merge commit, and `main` had already advanced by one commit — the
evidence for that very check — so it reported three failures for a correct
merge. A gate whose verdict depends on how much has landed since is not
verifying what it claims to. It now finds the merge commit that has the branch
tip as a parent, and names which commit it judged.

**Not published.** `v0.3.0a10` is an alpha baseline; no binary is published, and
nothing here authorizes one.

---

## Scope review — no enforcement path entered D.2

`scripts/repro/scope_review_d2.py`, **14 checks, all pass** (`scope-review.txt`).
It reads the parsed syntax tree, not the source text, so a mention in a comment
does not fail it and a real call cannot hide in one.

| Check | |
|---|---|
| `ShadowObserver` exposes no acting method | public surface is `active`, `observe`, `summary` |
| `observe()` returns nothing on every path | a return value is how an observer becomes a decision-maker |
| The Governor it builds has a no-op actuator | `FakeProcessActuator` |
| …a throwaway in-memory quarantine store | literal empty dict, does not outlive the call |
| …is marked `simulated` | |
| Shadow calls no acting Governor method | none of 14 acting methods |
| Shadow imports nothing that touches a device | no `subprocess`, `torch`, `pynvml`, `cupy`, `pycuda` |
| No production module calls an acting Governor method | |
| The `off` path is the first thing `_shadow_observe` does | |
| Nothing is imported before the `off` check | importing a module to discover you are switched off is already a difference |
| `prepare_placement` assigns `args.run_id` | |
| …before the shadow observation | encodes the defect found above |
| The observation schema declares run, placement, boot and time | |

**The gate is not vacuous.** Reintroducing either defect — moving the `run_id`
block back after the observation, or dropping `schemaVersion` from
`REQUIRED_FIELDS` — was tested and produces `== scope review FAILED ==` and a
non-zero exit.

---

## Hardware — one paired 24-token smoke

`0.3.0a10` installed at `/opt/om/venv`, run from the installed console script.
Machine idle beforehand: no compute processes, 721 MiB baseline on the NVIDIA
card. Boot `02484ee7-d706-4725-a7aa-163e3e294c86`, **identical in all four
recordings** (before, each arm, after) — the campaign is valid.

`smoke-compare.txt` — every check passes.

### Correctness is identical across the arms

| | |
|---|---|
| 24-token sequence | equal |
| Decoded text | equal |
| Prompt tokens | 14 |
| Stop reason | `length` |
| Tensor count | 363 |
| Model fingerprint | `ff74ccb7c5e616ddfa3ea53f4d201be9825fb02ce8673e45f863ab892adcc7be` |
| Boundary layer | 19 |
| Transport | `host-staged-xvendor` |
| Adapter | `mistral`, config digest `f230a7c1dea0…` |
| Stage ownership | cuda 181 tensors layers 0–19; rocm 182 tensors layers 20–39 |
| Layer overlap | 0 |
| Tensor overlap | 0 |
| Every model tensor owned exactly once | 363 owned, model reports 363 |
| Worker exit codes | `{rocm: 0, cuda: 0}` both arms |

Ownership is checked as a partition, not as two equal lists: disjoint *layers*
with a duplicated embedding would pass a layer check and still be two copies on
two devices.

`manifestDigest` is deliberately **not** compared across arms. The digest hashes
the whole manifest minus itself, and the manifest carries a fresh `placementId`
and a `createdAt`; two runs must produce two digests. Equal ones would mean
placement identity had stopped being unique.

### The observation correlates to its own run

```json
{"schemaVersion": 1,
 "runId": "a60c4c73-b0e8-47ab-a88d-767b8d4e757c",
 "placementId": "pl-df30f5976b95405b",
 "manifestDigest": "1cbeb21a0fdd92ebbe071d7c6d137748782b3b046e981f3ecbca69e613ce6d71",
 "modelFingerprint": "ff74ccb7c5e6…",
 "bootId": "02484ee7-d706-4725-a7aa-163e3e294c86",
 "wallTimeUtc": 1788074697.206, "monotonicNs": 668694061397,
 "wouldTransition": "READY->ADMITTED", "wouldAction": "none",
 "safetySimulated": true, "safetyEnforced": false}
```

Checked against the run's own records rather than against itself:

- `placementId` equals the `placementId` in `work-shadow/placement.json`
- `manifestDigest` equals that manifest's digest
- `runId` is the run id the audit trail attributes its events to — the only run
  id in `events-shadow.jsonl`
- `bootId` is this boot

### Telemetry confidence survives the integration

**AMD power reads `null` with `UNAVAILABLE_EXPECTED` — never zero.** NVIDIA
reads 13.64 W with `AVAILABLE`. This is the distinction most likely to be lost
when telemetry passes through an adapter, and reporting a real card as drawing
0 W would be a false safety signal, not a missing one.

### `off` is unchanged

The `off` arm produced **no `safety-shadow.jsonl` and no `[safety/shadow]`
line**. Both are asserted.

### Cleanup

**VRAM returned exactly to baseline** — 721 MiB before, 721 MiB after. **Zero
orphan workers.**

### Timing — reported, not judged

| Arm | TTFT | Decode |
|---|---|---|
| `off` | 174.5 ms | 10.85 tok/s |
| `shadow` | 181.2 ms | 11.11 tok/s |

One observation per arm, position not balanced. The +6.7 ms difference sits well
inside the 44–47 ms within-build run-to-run range the Gate A paired campaign
established, and **no threshold is set or changed from it**.

The `off` arm's 10.85 tok/s is **marginally below the frozen 10.9–11.3 tok/s
band, and is reported rather than passed over.** That band is a gate on a
*campaign median*, and a single 24-token run is not that instrument; the shadow
arm reads 11.11 tok/s in the same pair, which is inside it. It is noted here so
that if a real decode regression appears later, this reading is already on the
record rather than discovered retrospectively. Bounding it needs the
position-balanced campaign pre-registered for D.3.

---

## Correction to the D.2 report

The D.2 README states **"Byte-exact boundary under shadow mode"**. The boundary
probe (`scripts/repro/wsl_boundary_exact.sh`) never sets `OM_SAFETY_MODE` and
runs `forward_pass.py` directly from the checkout — the coordinator, and
therefore the observer, is not in that path at all. The measurement is valid
evidence that the transport boundary is byte-exact; it is **not** evidence about
shadow mode, and describing it that way overstated it.

The boundary probe was not re-run for `0.3.0a10`. Nothing on that path changed:
this build touches `runtime/safety/shadow.py` and `runtime/cli/coordinator.py`
only. The byte-exactness evidence stands where it was measured, and is not
claimed here as a shadow-mode result.

---

## Contents

| Path | What it is |
|---|---|
| `identities.json` | installed content digests for both distributions |
| `commit.txt` | the commit this artifact was built from |
| `unit-suite.txt` | 443 checkout tests |
| `installed-wheel-tests.txt` | 220 installed-wheel tests, with exclusions named |
| `scope-review.txt` | 14 scope checks |
| `smoke-compare.txt` | the paired comparison, every check |
| `run-off.json`, `run-off.err` | the `off` arm |
| `run-shadow.json`, `run-shadow.err` | the `shadow` arm |
| `safety-shadow-shadow.jsonl` | the observation |
| `events-off.jsonl`, `events-shadow.jsonl` | per-arm audit trails, not overwritten |
| `placement-shadow.json` | the manifest the observation names |
| `boot-*.txt` | boot id before, per arm, and after |
| `gpu-baseline.txt`, `gpu-after.txt`, `orphans.txt` | device state either side |

## Not done, and not authorized

No enforcement. Shadow-mode safety is not enforcement. No default enforcement,
no canary, no worker, CLI, API or console integration beyond the single
coordinator observation point. No Memory Fabric, pager, MHub or training work.
No extended GPU campaign — the position-balanced campaign that would bound
shadow-mode overhead belongs to D.3 and needs its protocol pre-registered first.
