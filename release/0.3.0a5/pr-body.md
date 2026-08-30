Adds the Model Adapter SDK and seals it as `0.3.0a5`.

The runtime previously had no architecture gate at all. A Llama checkpoint would
inspect, plan, allocate VRAM on both cards, load weights, and fail only when
`MistralStage` tried to build Mistral layers from a Llama config. This makes that
a refusal before anything is allocated.

## What changed

**Adapter SDK.** A registry, the `ModelAdapter` interface, and `MistralAdapter`.
Resolution reads only the checkpoint — never the directory name, the repository
name, a flag or an environment variable. Exactly one claimant resolves; zero is
`UNSUPPORTED_ARCHITECTURE`; more than one is `AMBIGUOUS_ADAPTER` and is never
broken by precedence, because two adapters claiming one architecture is a
registry defect and picking one would hide it.

`MistralStage` moved behind the interface byte-for-byte. `stage_model.py`
re-exports it through a module `__getattr__`, so every existing importer is
unchanged and there is no import cycle.

Removed `SUPPORTED_ARCHITECTURES = {"MistralForCausalLM", "LlamaForCausalLM"}`,
which was referenced nowhere and declared support for an architecture that would
have allocated on both cards before failing. It is now a view onto the registry
and cannot drift from what is installed.

**Placement schema v2.** New manifests pin `adapterId`, `adapterVersion` and
`adapterConfigDigest`, plus the runtimes the plan was compiled against and the
build that compiled it. All top level, so `manifestDigest` covers them.

Integrity and executability are separated. Schema 1 stays **readable** and
audit-replayable; only schema 2 is **executable**. `release/0.1.0a5/placement.json`
and every manifest in frozen gate evidence still verifies byte-for-byte, because
nothing rewrites it and the digest is checked before the schema is judged.

**Hardware qualification, default-deny.** An adapter with no record covering this
exact situation may be inspected and planned, and may not be executed. The record
is a tuple — checkpoint, adapter, config digest, both content digests, both torch
runtimes, transport, device pair and boundary — not a label on a name. Overrides
are explicit, attributed, and land in the audit trail.

## Identities

Full values, unabbreviated, so this PR identifies the artifact it validated
without a reader having to go and look them up.

| | |
|---|---|
| Version | `0.3.0a5` |
| Canonical wheel SHA-256 | `3eeaed3e229226f4d1408826fc42934057878fa48a91fc7fbb89900a88a04871` |
| Installed content SHA-256 | `e2eccbbe6de9aa8fdc342bbed015cede325a84161269dfa719c6f3f97c0011bf` |
| MCCL version | `0.2.0a3` |
| MCCL content SHA-256 | `5f2028695fa830417793ddcfecf90aa33bfd0b9e64775115058839d94d12631d` |
| Model fingerprint | `ff74ccb7c5e616ddfa3ea53f4d201be9825fb02ce8673e45f863ab892adcc7be` |
| Adapter config digest | `f230a7c1dea09ad930957d1fb7a446b69f85279941cb39a95bada043706c2d4a` |
| Placement schema | `2` |
| Hardware | `nvidia:GPU-cbb3d045-9d5f-a225-0f2e-adb1c6d6a033` + `amd:pci-0000:04:00.0` |

The canonical wheel is the one the Gate A paired campaign measured. It is not
rebuilt or replaced by merging or tagging.

## Evidence

`release/0.3.0a5/`, with the pre-refactor baseline at `d76334a` alongside this
build measured by the same script.

| Gate | Result |
|---|---|
| Unit suite | 263 tests, 0 failures, no group skipped |
| Adapter refusal (planning + direct worker) | PASS, VRAM measured either side |
| Qualification lifecycle (six states) | PASS |
| Baseline comparison | PASS |
| Performance (paired, 8 runs) | PASS, +1.8% against a 20% allowance |

Identical to baseline: the frozen 24-token sequence, ownership `181/182`, 363
tensors, overlap 0, boundary byte digest `c1467cd33c52032932ae4a39661a8136`,
fingerprint `ff74ccb7c5e616ddfa3ea53f4d201be9825fb02ce8673e45f863ab892adcc7be`,
zero orphans.

Changed as intended: schema 1 → 2, and `mistral@1` pinned with its config digest.

## Two things worth a reviewer's attention

**The TTFT acceptance rule changed, and not because a build failed it.** The
frozen absolute band is 28.9 ms wide; the measured within-build run-to-run range
is 44–47 ms, so identical code produced both a pass and a fail across campaigns.
TTFT is now judged paired against the incumbent at ≤20% mean regression. The
173.2 ms figure is unchanged and retained as a historical observation. The
justification is same-build measurements pre-registered as drift diagnostics
before any candidate outcome was known — see `gates/gate-a-verdict.md`.

**Seven wheels were built and six are recorded non-releasable**, with hashes and
the specific reason each failed, in `docs/NONRELEASABLE_BUILDS.md`. Identity is
asserted on installed content, not wheel bytes: rebuilding one committed source
gives different wheel hashes at identical size and one installed-content digest.
