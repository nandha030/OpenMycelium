# Gate process corrections

Rules adopted after a gate did not work as intended. Each records what happened,
not just the rule, because the rule without its incident reads as bureaucracy and
gets dropped.

---

## Scope review must complete before merge

**Adopted:** 2026-08-30, after PR #3.

The Files changed scope review was designated the final gate before merging the
Adapter SDK baseline. PR #3 was merged before it ran. The review was then carried
out against the merged range and passed — nothing frozen modified, zero
deletions, no out-of-scope subsystem touched.

It passing is not the point. Had it found scope creep, the finding would have
arrived after the irreversible step, and the only remedies left would have been a
revert commit or a follow-up PR against `main`. **A review that can only produce
its finding after the action it guards is not a gate.**

The order is fixed:

```
scope review  ->  merge  ->  ancestry verification  ->  tag
```

No step starts before the one before it reports. A gate skipped under time
pressure is recorded as skipped, and the artifact is not described as having
passed it.

### What the scope review is

Reading the changed-file set for files that do not belong to the milestone,
independently of whether their contents are correct. It is distinct from the
correctness gates, which ask whether the code works. A perfectly correct change
to an unrelated subsystem is exactly what this catches.

For PR #3 it checked: every `release/` path is an addition and no frozen evidence
is modified; no deletions anywhere; no symbol from a later gate
(`SafetyGovernor`, `MemoryObject`, `ResidencyManager`, `TransportBackend`,
`PrefetchPolicy`, `EvictionPolicy`, `WorkingSet`, `bind_working_set`,
`memoryMode`, `safetyMode`, `pager`); and that cross-subsystem files carry only
the change the milestone required — in that case, import-path preambles in
`runtime/mycelium/` with no behavioural difference.

---

## Canonical artifacts are preserved outside the repository

**Adopted:** 2026-08-30, at `v0.3.0a5`.

`dist/` is gitignored, so a tag naming a wheel SHA-256 names bytes the repository
cannot check. Rebuilding does not recover them: three rebuilds of one committed
source produced wheel hashes `3eeaed3e`, `495a3fde` and `b99adac0` at identical
size, all installing to one `installedContentSha256`. ZIP timestamps and
packaging metadata differ per build.

So the installed-content digest is the reproducible identity, and the wheel hash
is only meaningful while the wheel exists.

`v0.3.0a5` artifacts are preserved read-only at
`C:\Users\User\Documents\OpenMycelium_artifacts\v0.3.0a5\` with both wheels, the
MCCL sdist, `SHA256SUMS`, and a `MANIFEST.txt` recording the tag, merge commit,
digests and hardware qualification summary. They are not rebuilt or replaced.

**Not published.** `v0.3.0a5` is an Adapter SDK alpha baseline, not a release for
external evaluation. Publishing binaries makes every byte and digest public and
mirrorable, and an alpha invites evaluation it is not ready for.

Before a public beta or RC: make a second off-machine backup, then publish a
GitHub prerelease containing the exact qualified artifacts — not a rebuild of
them.

---

## Branch scope is stated before work begins

**Adopted:** 2026-08-30, at the start of Gate C.

`feature/safety-governor` carries the **Safety Governor only**. No Memory Fabric,
pager, virtual heap, training, or MHub integration enters this branch, whatever
adjacency suggests itself while working.

Stating scope at branch creation gives the scope review something to check
against that was written before the code, rather than inferred from it
afterwards.
