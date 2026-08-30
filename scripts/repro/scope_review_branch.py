"""Branch scope review: does the changed-file set belong to this milestone?

`docs/GATE_PROCESS.md` fixes the order -- scope review, then merge, then
ancestry verification, then tag -- because PR #3 was merged before its review
ran. It passed, but had it found scope creep the finding would have arrived
after the irreversible step. A review that can only produce its finding after
the action it guards is not a gate.

This asks a different question from the correctness gates. They ask whether the
code works; this asks whether the code belongs here at all. A perfectly correct
change to an unrelated subsystem is exactly what this catches.

Usage:  scope_review_branch.py [base] [head]
"""

from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
BASE = sys.argv[1] if len(sys.argv) > 1 else "main"
HEAD = sys.argv[2] if len(sys.argv) > 2 else "HEAD"

#: `feature/safety-governor` carries the Safety Governor only -- stated at
#: branch creation, so the review checks against something written before the
#: code rather than inferred from it afterwards. `SafetyGovernor` and
#: `safetyMode` are this milestone; everything else here belongs to a later one.
LATER_GATE_SYMBOLS = (
    "MemoryObject", "ResidencyManager", "TransportBackend", "PrefetchPolicy",
    "EvictionPolicy", "WorkingSet", "bind_working_set", "memoryMode",
    "create_memory_object", "advise_access_pattern", "query_residency",
)

#: Subsystems this branch may not touch, whatever adjacency suggests itself.
FOREIGN_PATHS = (
    "runtime/mccl/", "runtime/mycelium/", "runtime/bridge/", "deploy/", "k8s/",
    "observability/", "docs/paper/",
)

#: Directories whose contents are frozen evidence. A modification here is a
#: failure regardless of what it says: frozen releases are never changed.
FROZEN_PREFIXES = ("release/0.1.0", "release/0.2.0", "release/0.3.0a4",
                   "release/0.3.0a5")

failures, notes = [], []


def check(label: str, ok: bool, detail: str = "") -> None:
    (notes if ok else failures).append(
        f"{'ok  ' if ok else 'FAIL'}  {label}" + (f" -- {detail}" if detail else ""))


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout


rows = []
for line in git("diff", "--numstat", f"{BASE}...{HEAD}").splitlines():
    if not line.strip():
        continue
    added, removed, path = line.split("\t", 2)
    rows.append((added, removed, path))

status = {}
for line in git("diff", "--name-status", f"{BASE}...{HEAD}").splitlines():
    if line.strip():
        parts = line.split("\t")
        status[parts[-1]] = parts[0]

print(f"\nBranch scope review -- {BASE}...{HEAD}\n")
print(f"  {len(rows)} file(s) changed\n")

# 1. No deletions of whole files. --------------------------------------------
deleted = sorted(p for p, s in status.items() if s.startswith("D"))
check("no file is deleted", not deleted, ", ".join(deleted[:5]))

# 2. Frozen evidence is untouched. -------------------------------------------
touched_frozen = sorted(
    p for p in status
    if any(p.startswith(prefix) for prefix in FROZEN_PREFIXES))
check("no frozen release evidence is modified", not touched_frozen,
      ", ".join(touched_frozen[:5]))

# 3. Every release/ path is an addition. --------------------------------------
release_not_added = sorted(
    f"{s} {p}" for p, s in status.items()
    if p.startswith("release/") and not s.startswith("A"))
check("every release/ path is an addition", not release_not_added,
      "; ".join(release_not_added[:5]))

# 4. No foreign subsystem is touched. -----------------------------------------
foreign = sorted(p for p in status
                 if any(p.startswith(prefix) for prefix in FOREIGN_PATHS))
check("no foreign subsystem is touched", not foreign, ", ".join(foreign[:5]))

# 5. No symbol from a later gate appears. -------------------------------------
#
# Searched in the added lines of the diff, not in the tree: a symbol that was
# already there is not something this branch introduced.
#
# Code and prose are judged separately, and neither is exempt. A later gate's
# symbol in code is scope creep. In prose it is a mention -- `GATE_PROCESS.md`
# has to name the symbols to forbid them, and a rule that fails on its own
# statement teaches people to switch the rule off. But the mention is still
# reported and still pinned to the file that defines the rule: a *different*
# document starting to describe Memory OS work on this branch is exactly the
# drift this review exists to see.
CODE_SUFFIXES = (".py", ".sh", ".c", ".h", ".cpp", ".cu", ".js", ".go", ".ps1")

#: Paths allowed to contain the symbols, pinned exactly rather than by pattern.
#:
#: `GATE_PROCESS.md` states the rule and has to name what it forbids. This file
#: enforces it and has to hold the list. Its output quotes whatever it found.
#: All three match themselves. The first run passed only because this script was
#: still untracked and so absent from the diff; the moment it was committed it
#: failed on its own definition -- and that regenerated output was committed as
#: evidence without being re-read, so a file recording FAIL was merged under a
#: summary claiming PASS.
#:
#: Exempting a path means scope creep hidden inside it would pass. That is
#: unavoidable for a checker that must name what it forbids, so the exemption is
#: three exact paths, asserted below to be exactly three, and never a pattern.
RULE_DOCUMENT = "docs/GATE_PROCESS.md"
SELF_REFERENTIAL = (
    RULE_DOCUMENT,
    "scripts/repro/scope_review_branch.py",
    "release/gate-d2/seal/scope-review-branch.txt",
)

code_hits, prose_hits = {}, {}
path = None
for line in git("diff", f"{BASE}...{HEAD}").splitlines():
    if line.startswith("+++ b/"):
        path = line[6:]
    elif line.startswith("+") and not line.startswith("+++") and path:
        for symbol in LATER_GATE_SYMBOLS:
            if symbol in line:
                bucket = code_hits if path.endswith(CODE_SUFFIXES) else prose_hits
                bucket.setdefault(path, set()).add(symbol)

stray_code = {p: s for p, s in code_hits.items() if p not in SELF_REFERENTIAL}
check("no later-gate symbol appears in code", not stray_code,
      "; ".join(f"{p}: {sorted(s)}" for p, s in sorted(stray_code.items())))
stray_prose = {p: s for p, s in prose_hits.items() if p not in SELF_REFERENTIAL}
check("later-gate symbols are named only where they are forbidden",
      not stray_prose,
      "; ".join(f"{p}: {sorted(s)}" for p, s in sorted(stray_prose.items()))
      or "only in the rule, the checker, and the output of the checker")
check("the self-referential exemption is exactly three named paths",
      len(SELF_REFERENTIAL) == 3 and len(set(SELF_REFERENTIAL)) == 3,
      ", ".join(SELF_REFERENTIAL))

# 6. Line-ending churn has not been swept in. ---------------------------------
#
# The working tree carries a large pre-existing line-ending difference across
# files this milestone never touched. A commit that swept those in would show
# as a file whose additions equal its deletions and whose real diff is empty.
churn = []
for added, removed, path in rows:
    if added == "-" or removed == "-":
        continue                                  # binary
    if int(removed) == 0:
        continue
    real = git("diff", "--ignore-all-space", f"{BASE}...{HEAD}", "--", path)
    if not real.strip():
        churn.append(path)
check("no file changed only in whitespace or line endings", not churn,
      ", ".join(churn[:5]))

# 7. Every changed path is one this milestone owns. ---------------------------
OWNED = ("runtime/safety/", "runtime/cli/coordinator.py", "runtime/cli/lifecycle.py",
         "packaging/launcher.py", "scripts/build_openmycelium_wheel.py",
         "scripts/repro/", "docs/SAFETY_GOVERNOR.md", "docs/NONRELEASABLE_BUILDS.md",
         "docs/GATE_PROCESS.md", "release/gate-c1/", "release/gate-d1/",
         "release/gate-d2/")
unowned = sorted(p for p in status
                 if not any(p.startswith(prefix) for prefix in OWNED))
check("every changed path belongs to this milestone", not unowned,
      "; ".join(unowned[:8]))

print("  changed paths")
for added, removed, path in sorted(rows, key=lambda row: row[2]):
    print(f"    {status.get(path, '?'):<3} +{added:<6} -{removed:<6} {path}")

print()
for line in notes + failures:
    print(f"  {line}")
print()
if failures:
    print(f"  == branch scope review FAILED: {len(failures)} check(s) ==")
    sys.exit(1)
print(f"  == branch scope review PASSED: {len(notes)} checks ==")
