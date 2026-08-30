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

## A pull request is required before every merge

**Adopted:** 2026-08-30, after the Gate D.2 merge.

The Gate D.2 branch was merged into `main` locally with `--no-ff` and no pull
request. The scope review ran first and was a script with preserved output,
which is stronger than a manual read of a Files-changed tab — but it is not a
review. Nobody other than the author looked at the change before it landed.

**Recorded as a process exception, not as an equivalent.** A retrospective pull
request was considered and rejected: it cannot review code that is already
merged. Opening one would have produced the appearance of review without the
substance, which is worse than the honest gap.

From here: **every merge into `main` goes through a pull request**, opened
before the merge and reviewed by someone other than whoever wrote the change.
The order becomes:

```
scope review  ->  pull request  ->  review  ->  merge  ->  ancestry verification  ->  tag
```

This applies to documentation and harness changes too. The Gate D.2 corrections
that followed the merge were themselves landed on a branch and opened as a pull
request rather than pushed to `main`, which is the first application of the
rule.

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

**The wheel bytes are not published.** The source and the tags are: they are
pushed to GitHub, where the commits, the tag messages and every digest recorded
in them can be read. Only the canonical wheel bytes are withheld.

That distinction was stated carelessly at first -- "not published", full stop --
which is wrong about a repository that has been pushed. Publishing the *binaries*
makes every byte public and mirrorable, and an alpha invites evaluation it is not
ready for. Publishing the *source* already happened and was always the intent.

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
