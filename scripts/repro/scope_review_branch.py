"""Branch scope review: does the changed-file set belong to this milestone?

`docs/GATE_PROCESS.md` fixes the order -- scope review, then merge, then
ancestry verification, then tag -- because PR #3 was merged before its review
ran. It passed, but had it found scope creep the finding would have arrived
after the irreversible step. A review that can only produce its finding after
the action it guards is not a gate.

This asks a different question from the correctness gates. They ask whether the
code works; this asks whether the code belongs here at all. A perfectly correct
change to an unrelated subsystem is exactly what this catches.

Scope is per branch, declared as data before the code exists. `GATE_PROCESS.md`
already requires that -- "branch scope is stated before work begins" -- but the
checker used to hold one global list, which encoded a strictly sequential
roadmap: "everything else here belongs to a later one". Two branches that are
open at the same time cannot both be checked against that.

Usage:  scope_review_branch.py [base] [head] [--scope NAME]
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, Tuple

REPO = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
_ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = _ARGS[0] if len(_ARGS) > 0 else "main"
HEAD = _ARGS[1] if len(_ARGS) > 1 else "HEAD"


@dataclass(frozen=True)
class Scope:
    """What one branch may touch, written before its code.

    `forbidden_symbols` are the ones belonging to *another* milestone. They are
    per branch because "later" is not a property of a symbol: `MemoryObject`
    is forbidden on a safety branch and is the entire point of a memory branch.
    """

    purpose: str
    owned: Tuple[str, ...]
    forbidden_symbols: Tuple[str, ...]
    foreign_paths: Tuple[str, ...]
    #: Files whose job is to name what they forbid. A prohibition is not an
    #: introduction, and a rule that fails on its own statement teaches people
    #: to switch the rule off. Pinned exactly, never by pattern: exempting a
    #: path does mean scope creep hidden inside it would pass, so the list is
    #: short, per branch, and printed in the report.
    prohibition_files: Tuple[str, ...] = field(default_factory=tuple)


#: Every symbol either milestone cares about, so each branch can forbid the
#: other's without repeating the list.
MEMORY_SYMBOLS = (
    "MemoryObject", "ResidencyManager", "TransportBackend", "PrefetchPolicy",
    "EvictionPolicy", "WorkingSet", "bind_working_set", "memoryMode",
    "create_memory_object", "advise_access_pattern", "query_residency",
)
ENFORCEMENT_SYMBOLS = (
    "ENFORCE_ADMISSION", "ENFORCE_DRAIN", "ENFORCE_BREAKER",
    "ENFORCE_QUARANTINE", "enforce_full", "refuse_admission",
)

#: Subsystems no current branch may touch, whatever adjacency suggests itself.
COMMON_FOREIGN = (
    "runtime/mccl/", "runtime/mycelium/", "runtime/bridge/", "deploy/", "k8s/",
    "observability/", "docs/paper/",
)

_SHARED_HARNESS = ("scripts/repro/", "scripts/build_openmycelium_wheel.py",
                   "docs/GATE_PROCESS.md", "docs/NONRELEASABLE_BUILDS.md")

SCOPES: Dict[str, Scope] = {
    "feature/safety-governor": Scope(
        purpose="Safety Governor only: contract, deterministic core, shadow mode",
        owned=("runtime/safety/", "runtime/cli/coordinator.py",
               "runtime/cli/lifecycle.py", "packaging/launcher.py",
               "docs/SAFETY_GOVERNOR.md", "release/gate-c1/", "release/gate-d1/",
               "release/gate-d2/") + _SHARED_HARNESS,
        forbidden_symbols=MEMORY_SYMBOLS,
        foreign_paths=COMMON_FOREIGN,
        prohibition_files=("docs/GATE_PROCESS.md",
                           "scripts/repro/scope_review_branch.py",
                           "release/gate-d2/seal/scope-review-branch.txt")),
    "feature/safety-enforcement-d3": Scope(
        purpose="Gate D.3 enforcement contract only. No implementation.",
        owned=("runtime/safety/", "docs/SAFETY_ENFORCEMENT.md",
               "docs/SAFETY_GOVERNOR.md", "release/gate-d3/") + _SHARED_HARNESS,
        forbidden_symbols=MEMORY_SYMBOLS,
        foreign_paths=COMMON_FOREIGN,
        # The contract has to name Memory OS symbols in order to prohibit them,
        # in the data and in the document alike.
        prohibition_files=("docs/GATE_PROCESS.md",
                           "scripts/repro/scope_review_branch.py",
                           "runtime/safety/enforcement.py",
                           "runtime/safety/test_enforcement_contract.py",
                           "docs/SAFETY_ENFORCEMENT.md")),
    "fix/operator-path": Scope(
        purpose="The operator path: launcher, qualification flow, console, smi",
        owned=("openmycelium.cmd", "packaging/", "runtime/cli/",
               "runtime/serving/adapters/", "docs/NONRELEASABLE_BUILDS.md",
               "release/operator-path-a18/") + _SHARED_HARNESS,
        forbidden_symbols=MEMORY_SYMBOLS,
        foreign_paths=COMMON_FOREIGN,
        # The qualification contract and its tests name the Memory OS symbols
        # only to keep them out of this milestone.
        prohibition_files=("docs/GATE_PROCESS.md",
                           "scripts/repro/scope_review_branch.py")),
    "feature/memory-os-m1": Scope(
        purpose="Memory OS M.1: deterministic simulator, no GPU, no enforcement",
        owned=("runtime/memory/", "docs/MEMORY_OS.md",
               "docs/INVENTION_LEDGER.md", "release/memory-m1/") + _SHARED_HARNESS,
        # The mirror image: a memory branch may not implement enforcement.
        forbidden_symbols=ENFORCEMENT_SYMBOLS,
        foreign_paths=COMMON_FOREIGN + ("runtime/safety/",),
        prohibition_files=("docs/GATE_PROCESS.md",
                           "scripts/repro/scope_review_branch.py",
                           "docs/MEMORY_OS.md")),
    "chore/branch-scopes": Scope(
        purpose="Generalise the scope checker so two branches can be open at once",
        # Named exactly rather than widened to scripts/repro/: this branch
        # genuinely touches two files, and a scope that says "the whole harness"
        # would have nothing to catch.
        owned=("scripts/repro/scope_review_branch.py",
               "scripts/repro/scope_review_negative_test.sh",
               "docs/GATE_PROCESS.md", "release/hygiene/"),
        # This file declares every branch's forbidden set, so it names them all.
        forbidden_symbols=(),
        foreign_paths=COMMON_FOREIGN,
        prohibition_files=("scripts/repro/scope_review_branch.py",)),
}


def resolve_scope() -> Tuple[str, Scope]:
    """The scope for the branch under review, or a hard failure.

    An undeclared branch is a failure, not a default. `GATE_PROCESS.md` requires
    scope to be stated before work begins, and silently applying some fallback
    would let a branch with no declared scope pass a scope review.
    """
    named = [a[len("--scope="):] for a in sys.argv if a.startswith("--scope=")]
    if named:
        name = named[0]
    elif HEAD in SCOPES:
        name = HEAD
    else:
        name = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", HEAD], cwd=REPO,
            capture_output=True, text=True).stdout.strip()
    if name not in SCOPES:
        print(f"\nBranch scope review -- {BASE}...{HEAD}\n")
        print(f"  FAIL  {name!r} declares no scope in SCOPES")
        print("        State it before the work, not after: "
              "GATE_PROCESS.md, 'branch scope is stated before work begins'.")
        raise SystemExit(1)
    return name, SCOPES[name]


SCOPE_NAME, SCOPE = resolve_scope()
LATER_GATE_SYMBOLS = SCOPE.forbidden_symbols
FOREIGN_PATHS = SCOPE.foreign_paths

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
SELF_REFERENTIAL = SCOPE.prohibition_files

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
# The exemption is bounded by an invariant rather than by a count, because a
# count is arbitrary and an invariant is not: every exempt path must be one this
# scope already owns. That is what stops the list becoming a way to smuggle a
# foreign file past the review -- exempting something outside the milestone is
# now a failure regardless of how few there are.
_outside = [p for p in SELF_REFERENTIAL
            if not any(p.startswith(prefix) for prefix in SCOPE.owned)]
_wild = [p for p in SELF_REFERENTIAL if "*" in p or p.endswith("/")]
check("every prohibition exemption is an exact path this scope owns",
      not _outside and not _wild
      and len(set(SELF_REFERENTIAL)) == len(SELF_REFERENTIAL)
      and 0 < len(SELF_REFERENTIAL) <= 6,
      f"outside the scope: {_outside}" if _outside else
      f"pattern rather than a path: {_wild}" if _wild else
      f"{len(SELF_REFERENTIAL)}: " + ", ".join(SELF_REFERENTIAL))

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
unowned = sorted(p for p in status
                 if not any(p.startswith(prefix) for prefix in SCOPE.owned))
check("every changed path belongs to this milestone", not unowned,
      "; ".join(unowned[:8]))

print(f"  scope: {SCOPE_NAME} -- {SCOPE.purpose}\n")
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
