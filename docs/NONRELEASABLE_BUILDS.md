# Non-releasable validation builds

Versions identify immutable artifacts, not roadmap features. Every wheel built
during validation gets its own version, and a wheel whose gate did not pass is
recorded here rather than renumbered or quietly rebuilt. The hashes are kept so
that a build found on a machine can always be identified, including the ones
that were wrong.

None of the builds below may be published.

## Adapter SDK milestone (0.2.0a6 – 0.3.0a3)

| Version | sha256 of the wheel | Why it is not releasable |
|---|---|---|
| `0.2.0a6` | `a76dc5ea1d61ed0dd6c550e617c8e320aed4fa1f3b5799e49af85e898870d254` | `plan` refused an unsupported architecture with prose only — no machine-readable `errorCode` — so the refusal could not be matched on. The direct-worker path had not been exercised on hardware at all. |
| `0.2.0a7` | `3051cd9b81c2a8aefbfc5f60929637c6dd79f77b5f29f46d91ce4414fc2c6da3` | `provenance` reported `placementSchemaVersion: 1` from a hard-coded constant while producers were writing schema 2. A provenance record making a false statement about its own build. |
| `0.2.0a8` | `3e99b31f4ac8cb94ef774ed46aa9d380077af31cfd7e3c44671478a85eb3055c` | Functionally the validated code at the time, but superseded by the decision that this milestone is a `0.3.0` change: it introduces the Model Adapter SDK and placement schema v2. Recorded rather than republished under a new number. |
| `0.3.0a1` | `4732ef28e03186cb3523a740e16f57eb04f756c9a2443070cd32fa5760f07ab4` | Qualification enforcement could not work: a worker runs under the vendor's interpreter, where `openmycelium` is on `PYTHONPATH` but not installed, so it could not read the wheel's content digest and every situation came out malformed. The lifecycle gate failed at step 2. |
| `0.3.0a2` | `6eeb695c6944212ecd5e44dce650e6070624678663c730b5652d6b57912b737a` | The qualification override event was emitted during manifest validation, which the audit writer correctly refuses — an event may not carry an identity taken from a manifest that has not been verified. Every override run died with `AuditError`. |
| `0.3.0a3` | `865819d0b5d929f28fae9189a06b2acd09b2eba66180acb6dff5cb18659fc0d1` | `qualify record --model NAME` passed the store name straight to the planner instead of resolving it, so a record could never be written for a model referred to by name. The lifecycle gate failed at step 3. |

Each of these was installed on the qualified machine and run. They are recorded
because "we rebuilt it and it works now" is not an account of what happened, and
because a machine still carrying one of these needs to be identifiable.

## Safety Governor milestone

| Version | sha256 of the wheel | Why it is not releasable |
|---|---|---|
| `0.3.0a6` | `7c40f548d597f555be5b1220c1e46660b74eae79f7cc21af2bb1ba357a4bc770` | Shipped the Gate D.1 Governor built against `safety-contract-1`, whose `ADMITTED` state had no incident path. A worker dying between admission and the compute canary — the window where allocation and weight-load setup happen, and so where an OOM is most likely — had nowhere to go, and its lease could be stranded. The Governor raised rather than inventing a transition, which is correct behaviour for a defective contract but not something to ship. Superseded by `0.3.0a7` on `safety-contract-1.1`. |
| `0.3.0a8` | `937d56dd9bc8343c8716c2299d93ffb0c480ed65d3b477b522f1c06adae9aec0` | First shadow-mode build. Observations reached stderr but were never persisted, so a run left no durable record of what the Governor would have done -- which is the entire output of shadow mode. Superseded by `0.3.0a9`, which writes them to `safety-shadow.jsonl`. |
| `0.3.0a9` | `4f78d005fc4a1e1c5fc8299082a0351eb0dd028045928e221ae33cd970d2ca79` | Persisted observations that could not be correlated. The record carried `bootId` and device identity but no schema version, run id, placement id or timestamp, so an observation could not be matched against the audit trail of the run it described — and comparing predicted actions against actual outcomes is the whole output of shadow mode. Separately, inserting the observer split `prepare_placement` and stranded its `run_id` assignment after a `return`, where nothing reached it; dead code rather than a live fault, because `Coordinator.__init__` also issues a run id and every caller constructs one before reading `args.run_id` — but a milestone that promised to leave the `off` path unchanged had silently deleted a statement from it. Superseded by `0.3.0a10`, which is itself non-releasable; the canonical build is `0.3.0a11`. |
| `0.3.0a10` | `36e6cd8f5a1047df47f84c19237a170f22453d8d5d67df0d0be610c8de076ac8` | Not reproducible from its own commit. Built from a working tree where 42 shipped modules carried CRLF and the rest LF: `.gitattributes` declares no rule for `.py`, so the checked-out bytes depend on `core.autocrlf` — `true` for the Windows git on this machine, unset for the WSL git — and the build ran over a mixture of the two. **Every file was byte-for-byte the committed program; zero content differences.** The artifact still cannot be reproduced, because no single checkout produces that mixture, and the identity model rests on the installed-content digest naming the committed source. This is the failure the digest exists to catch and the one it is worst at announcing: the program is identical, so every gate passes and nothing looks wrong. It surfaced only when a `git reset --hard` during an unrelated test normalised the tree and the installed-vs-checkout comparison began to fail. Tagged `v0.3.0a10` before the defect was found; the tag is left in place as a record and is superseded by `v0.3.0a11`. |

### Superseded after passing

| Version | sha256 of the wheel | Why it is not releasable |
|---|---|---|
| `0.3.0a4` | `0d668aefd9c9b37e5c46c2e324be9cb0895d7419421a1160cd38ac2b3d7e99cb` | Passed every gate and carries no known defect. Superseded by `0.3.0a5`, which pins `mcclContent` in the qualification tuple — `0.3.0a4` records only `mcclVersion`, leaving qualification-by-label open in the transport layer, which is the layer that decides what crosses the activation boundary. Retained as the incumbent in the Gate A paired campaign. |
| `0.3.0a7` | `f2827dcf238d836c91ade6e7a52d6bde4221df465b471c909ddeab18ba4347f7` | The Gate D.1 candidate. Passed D.1 and carries no known defect; superseded by the shadow-mode line. **The wheel D.1 actually gated is not this file.** D.1 built into `/tmp` and the directory was reclaimed before its digest was captured, so the hash above is a later rebuild of the same committed source. It is recorded so a build found on a machine can be identified, and it is not evidence of what D.1 measured. Evidence builds go to `dist/` since. |

## The complete artifacts

**`0.3.0a11` — the canonical shadow-mode release.** Installed content
`249e1ba255d9f5648914334cc34453d2ff62fd4b368742d0ba4af6a483823e5e`,
canonical wheel sha256
`4cfcaf023f2917e22d1f2cacacd42afb1283afaf106d5b834766d75acb453d28`,
MCCL `0.2.0a3` content `5f2028695fa830417793ddcfecf90aa33bfd0b9e64775115058839d94d12631d`.
Tag `v0.3.0a11`. Its installed content is reproducible: three builds from one
commit gave three wheel byte streams and one content digest.

**`0.3.0a5` — the Adapter SDK baseline.** Installed content
`e2eccbbe6de9aa8fdc342bbed015cede325a84161269dfa719c6f3f97c0011bf`,
canonical wheel sha256 `3eeaed3e229226f4d1408826fc42934057878fa48a91fc7fbb89900a88a04871`,
MCCL `0.2.0a3` content `5f2028695fa830417793ddcfecf90aa33bfd0b9e64775115058839d94d12631d`.
Tag `v0.3.0a5`.

### `0.3.0a5` — historical qualified artifact, source-reproduction caveat

**Audited read-only after the end-of-line policy landed.** No rebuild, no GPU
work, no tag modification. Evidence:
`release/audits/v0.3.0a5-source-reproduction.txt`, reproducible with
`scripts/repro/audit_v0_3_0a5.py`.

**It remains a qualified artifact and is not non-releasable.** Its hardware
evidence is unaffected: the wheel that was measured is the wheel that is
preserved, and the audit confirms it.

| Question | Answer |
|---|---|
| Preserved bytes match the recorded canonical wheel SHA-256? | **yes** — `3eeaed3e…` |
| Wheel reproduces the recorded `installedContentSha256`? | **yes** — `e2eccbbe…` |
| Any substantive difference from the tagged source? | **none**, under either `core.autocrlf` |
| Reproducible from a clean checkout of `v0.3.0a5`? | **no** |

**The caveat.** 43 of the 54 `.py` files inside the artifact carry CRLF. A clean
checkout of the tagged commit `ab6f2554` differs from the artifact in 43 files
with `core.autocrlf=false` and in 11 with `true` — **line endings only, zero
substantive differences in either case.** The artifact matches *neither* setting,
because it was built from a mixed tree: the same condition that made `0.3.0a10`
non-releasable.

So `0.3.0a5` was never reproducible from its commit either. Gate B's rebuild
check reproduced `e2eccbbe…` because it ran on that same mixed tree in the same
session, which is why it could not have caught this.

**Why this is a caveat and not a reclassification.** The program in the artifact
is byte-for-byte the tagged source; only line endings differ. The wheel was
qualified on hardware and that evidence stands. Marking it non-releasable would
discard valid hardware evidence over a packaging property that changes nothing
the program does. What it cannot claim is source reproduction: anyone rebuilding
from `v0.3.0a5` gets a different `installedContentSha256`, and that is now
recorded rather than discovered later.

`0.3.0a11` is the first artifact in this repository that *is* reproducible from
its commit, verified across three builds.

<!-- superseded caution, retained for the record:

It was built on this machine before the
line-ending defect was understood, so its recorded digest may share the
condition that made `0.3.0a10` non-releasable — a working tree whose bytes
depend on which `git` wrote it. This has **not** been re-checked, and
`0.3.0a5` is **not** reclassified here on suspicion. Gate B did verify that a
rebuild reproduced `e2eccbbe…`, but that rebuild ran in the same session and
on the same tree, so it does not settle the question. The `.py text eol=lf`
change should re-examine it; until then this note stands rather than a claim in
either direction.

end of superseded caution -->

### The tree must match the commit, not merely contain the same program

`0.3.0a10` established that "the same program" is not the same as "the same
artifact". Equal source semantics with different line endings produce a
different installed-content digest, and a digest that depends on which `git`
wrote the working tree identifies something weaker than a commit.

`scripts/build_openmycelium_wheel.py` now compares the tree against the index
before staging and refuses to build when they differ. `OM_ALLOW_DIRTY=1`
bypasses it, and a build made that way is not releasable.

Still open, and deliberately not folded into the Gate D.2 seal: `.gitattributes`
should declare `*.py text eol=lf`, for the same reason it already declares
`*.cmd text eol=crlf` — so the bytes do not depend on the configuration of
whoever cloned. That is a repository-wide renormalisation touching every
milestone's files and belongs in its own change.

### Wheel bytes are not product content

Rebuilding `0.3.0a5` from the same committed source produced wheel files hashing
`495a3fde…` and `b99adac0…` — three distinct byte streams at identical size,
differing in ZIP timestamps and packaging metadata. All three install to the same
`installedContentSha256`.

So identity is asserted on **installed content**, never on the wheel file, and
equal installed-content digests are never described as byte-identical wheels. The
wheel that the Gate A paired campaign actually measured is preserved as canonical;
a rebuild is used to confirm the digest, never to replace it. Two different wheel
byte streams must never appear under one version in release evidence.

## Earlier precedent

`0.1.0a7` was built twice with different contents and both builds were declared
non-releasable; the validated code was reissued as `0.1.0a8`. This file
generalises that decision rather than repeating the argument each time.
