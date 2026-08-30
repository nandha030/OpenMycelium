# Gate D.2 — seal, corrected

**Status: SEALED. The canonical artifact is `0.3.0a11`.**

`0.3.0a10` is **non-releasable**. It was tagged `v0.3.0a10` before the defect was
found; that tag is left in place as a record and is superseded by `v0.3.0a11`.

The Governor is a contract, a deterministic core, and an observer. **It enforces
nothing.** Shadow-mode safety is not enforcement.

---

## Why `0.3.0a10` is non-releasable

It could not be reproduced from its own commit.

It was built from a working tree where 42 shipped modules carried CRLF and the
rest carried LF. `.gitattributes` declares no rule for `.py`, so the checked-out
bytes depend on `core.autocrlf` — `true` for the Windows git on this machine,
unset for the WSL git — and the build ran over a mixture of the two.

**Every one of those files was byte-for-byte the committed program. Zero content
differences.** The artifact still cannot be reproduced, because no single
checkout produces that mixture, and the identity model rests on the
installed-content digest naming the committed source.

This is the failure the digest exists to catch and the one it is worst at
announcing. The program is identical, so every gate passes and nothing looks
wrong. It surfaced only because a `git reset --hard` during an unrelated
negative test normalised the tree, after which the installed-vs-checkout
comparison began to fail. Had that test not run, `0.3.0a10` would have been
sealed and tagged with a digest naming a tree no checkout can produce.

Two things changed as a result:

- **The build refuses a dirty tree.** `check_tree_matches_index()` compares the
  tree against the index before staging. `OM_ALLOW_DIRTY=1` bypasses it, and a
  build made that way is not releasable. Verified to fire.
- **`classify_installed_difference.py`** separates line-ending differences from
  content differences, because they call for completely different responses and
  a summary that merges them invites treating the second as the first.

Still open, deliberately not folded into this seal: `.gitattributes` should
declare `*.py text eol=lf`, for the same reason it already declares
`*.cmd text eol=crlf`. That is a repository-wide renormalisation touching every
milestone and belongs in its own change.

---

## Identities

| | |
|---|---|
| Version | `0.3.0a11` |
| Artifact commit | `a9030780d4603558dd2aa0c300c492911ece30c0` |
| Wheel sha256 | `4cfcaf023f2917e22d1f2cacacd42afb1283afaf106d5b834766d75acb453d28` |
| Wheel size | 822 432 bytes |
| Installed content sha256 | `249e1ba255d9f5648914334cc34453d2ff62fd4b368742d0ba4af6a483823e5e` |
| Installed python files | 60 |
| MCCL version | `0.2.0a3` |
| MCCL content sha256 | `5f2028695fa830417793ddcfecf90aa33bfd0b9e64775115058839d94d12631d` |
| Safety contract | `safety-contract-1.1` |
| Safety policy | `safety-policy-1-provisional` |
| Shadow observation schema | `1` |

### Reproducible, and demonstrated rather than asserted

Three builds from this commit, `rebuild-reproducible.txt` and `wheel-hashes.txt`:

```
wheel 4cfcaf02…  ┐
wheel 27ccdd18…  ├─ all install to content 249e1ba255d9f564…
wheel fe0ab76a…  ┘
```

Three distinct wheel byte streams at identical size, one installed-content
digest. The wheel hash names a file; the content digest names the product.

`installed-matches-checkout.txt`: **58 shipped modules identical, 0 differing in
line endings, 0 differing in content.** The installed wheel is this checkout,
and the checkout matches the index — which is what `0.3.0a10` could not say.

---

## Tests

| Suite | Result |
|---|---|
| Checkout unit suite | **443 tests, 0 failures** |
| — of which safety | 180 |
| — of which shadow mode | 39 |
| Installed-wheel suite | **220 tests, 0 failures** |

The gap reconciles exactly: 443 − 111 mccl − 52 event-gate − 23 mycelium = 257
shipped; − 25 checkout-only (`test_manifest_schema.py` needs frozen evidence a
wheel must not ship) − 12 class-skipped (`ProseMatchesDataTests`; a wheel ships
no document) = 220. Both exclusions are printed by the runner rather than
absorbed into a green total.

## Scope reviews

| Review | Result |
|---|---|
| Enforcement (`scope_review_d2.py`) | **14 checks** — no acting method, `observe()` returns nothing, no-op actuator, throwaway store, no device import, `off` path first |
| Branch (`scope_review_branch.py`) | **9 checks** — run before the merge |

Both verified non-vacuous by reintroducing the defect they guard.

---

## Hardware — one paired 24-token smoke

Boot `437b5825-d56f-417b-8c26-47415742871e`, identical in all four recordings.

**Correctness identical across the arms:** the 24-token sequence, decoded text,
14 prompt tokens, `length` stop reason, 363 tensors, fingerprint
`ff74ccb7c5e6…`, boundary layer 19, `host-staged-xvendor`, adapter `mistral`
with config digest `f230a7c1dea0…`. Ownership verified as a partition — cuda 181
tensors layers 0–19, rocm 182 tensors layers 20–39, **zero layer overlap, zero
tensor overlap, 363 tensors owned exactly once**. Both workers exited 0.

**The observation correlates to its own run**, checked against the run's records
rather than against itself: `placementId` `pl-4ff0041f00144bc2` matches
`placement.json`, `manifestDigest` matches that manifest, and `runId`
`50d7cba4-731e-4ea1-a85c-3390daa319ea` is the only run id in
`events-shadow.jsonl`.

**AMD power reads `null` with `UNAVAILABLE_EXPECTED`, never zero.** NVIDIA reads
12.91 W with `AVAILABLE`.

**`off` produced no observation file and no observation line.**

### Cleanup: what is attributable, and what is not

**Zero orphan workers**, and the check that says so was fixed to mean it. It had
matched the shell's own command line — the pattern `stage_model|pipeline_run`
matches any process whose arguments mention those names, including the `pgrep`
looking for them and the gate script naming them in a comment. "Zero orphan
workers" was briefly a claim about nothing. `check_orphans.sh` now requires a
python interpreter and excludes the current process tree, and is verified
against a deliberately planted worker-like process.

**Zero compute contexts** on the device (`gpu-compute-apps.txt`).

**The device memory total is not attributable, and is reported as such.** Under
WSL `nvidia-smi` reports the whole physical card, including Windows host usage,
and cannot enumerate Windows processes. Across this run it read:

| When | Device total |
|---|---|
| before | 722 MiB |
| after both arms | 824 MiB |
| settled, nothing of ours running | **678 MiB** |

It ended **44 MiB below** the pre-run figure with no OpenMycelium process alive.
A number that moves ±100 MiB on its own cannot show that this runtime released
its memory — and equally, the 824 MiB reading is not evidence that it did not.

**Earlier gates reported this number alone as "VRAM returned to baseline". That
claim was never supported by this instrument.** The Gate D.2 `0.3.0a10` run
happened to read 721 MiB before and after; that was luck, not measurement. What
is supported is: no worker survives, and no compute context remains.

### Timing — reported, not judged

| Arm | TTFT | Decode |
|---|---|---|
| `off` | 148.5 ms | 10.96 tok/s |
| `shadow` | 180.4 ms | 11.50 tok/s |

One observation per arm, position not balanced. The +31.9 ms difference sits
inside the 44–47 ms within-build run-to-run range Gate A established, and **no
threshold is set or changed from it.**

**The shadow arm's 11.50 tok/s is above the frozen 10.9–11.3 band, and the
`0.3.0a10` run's `off` arm read 10.85, below it.** Both are reported rather than
passed over. That band gates a *campaign median* and a single 24-token run is
not that instrument, but two excursions in two runs is worth having on the
record before D.3 rather than discovering it retrospectively. Bounding this
needs the position-balanced campaign pre-registered for D.3.

---

## Contents

`identities.json`, `rebuild-reproducible.txt`, `wheel-hashes.txt`,
`installed-matches-checkout.txt`, `unit-suite.txt`,
`installed-wheel-tests.txt`, `scope-review.txt`, `smoke-compare.txt`,
`run-off.*`, `run-shadow.*`, `safety-shadow-shadow.jsonl`,
`events-off.jsonl`, `events-shadow.jsonl`, `placement-shadow.json`,
`boot-*.txt`, `gpu-baseline.txt`, `gpu-after.txt`, `gpu-settled.txt`,
`gpu-compute-apps.txt`, `gpu-attribution.txt`, `orphans.txt`.

## Not done, and not authorized

No enforcement, no canary, no default enforcement. No worker, CLI, API or
console integration beyond the one coordinator observation point. No Memory
Fabric, pager, MHub or training. No extended GPU campaign. **Not published** —
this is an alpha baseline, and authorization to merge and tag is not
authorization to publish a binary.
