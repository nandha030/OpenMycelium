"""Ancestry verification: is everything the gates validated still reachable?

`GATE_PROCESS.md` puts this between merge and tag. The tag names an artifact,
and the evidence for that artifact is commits -- if a merge strategy replaced
them with new ids, the tag would name a build whose validation history is no
longer in the repository.

Also checks that earlier tags stay reachable. A tag left dangling off the main
line is evidence that is still in the object database and no longer in the
history anyone reads.

Usage:  verify_ancestry.py [branch] [tip]

The merge commit is found from the tip, not assumed to be it.
"""

from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
BRANCH = sys.argv[1] if len(sys.argv) > 1 else "feature/safety-governor"
TIP = sys.argv[2] if len(sys.argv) > 2 else "main"

failures, notes = [], []


def check(label: str, ok: bool, detail: str = "") -> None:
    (notes if ok else failures).append(
        f"{'ok  ' if ok else 'FAIL'}  {label}" + (f" -- {detail}" if detail else ""))


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                          text=True).stdout.strip()


def reachable(ref: str, tip: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", ref, tip],
                          cwd=REPO, capture_output=True).returncode == 0


def find_merge(branch: str, tip: str) -> str:
    """The merge commit that brought `branch` in, not whatever the tip is now.

    Taking the tip as the merge was wrong and briefly reported three failures
    for a merge that was correct: main had already advanced by one commit --
    the evidence for this very check -- so the tip had one parent and a
    different tree. A gate whose verdict depends on how much has landed since
    is not verifying what it claims to.
    """
    target = git("rev-parse", branch)
    for candidate in git("rev-list", "--merges", tip).splitlines():
        if target in git("log", "--format=%P", "-1", candidate).split():
            return candidate
    return tip          # no merge found; report against the tip and let the
                        # two-parent check say so


MERGE = find_merge(BRANCH, TIP)

print(f"\nAncestry verification -- {git('rev-parse', '--short', MERGE)}"
      f" (the merge of {BRANCH} into {TIP})\n")

# 1. The merge preserved the branch, rather than replacing it. ----------------
parents = git("log", "--format=%P", "-1", MERGE).split()
check("the merge has two parents (not a squash or fast-forward)",
      len(parents) == 2, f"{len(parents)} parent(s): {' '.join(parents)}")
check("the branch tip is a parent of the merge",
      git("rev-parse", BRANCH) in parents,
      git("rev-parse", "--short", BRANCH))

# 2. Every validated commit is reachable, by its own id. ----------------------
#
# The point of the merge commit: the gates validated these exact ids, and a
# squash produces new ones that nothing has validated.
commits = [line for line in
           git("rev-list", f"{parents[0]}..{git('rev-parse', BRANCH)}").splitlines()
           if line]
unreachable = [c for c in commits if not reachable(c, TIP)]
check("every commit the gates validated is reachable by its own id",
      not unreachable, f"{len(commits)} commit(s); {len(unreachable)} unreachable")

# 3. Earlier tags stay reachable. ---------------------------------------------
for tag in git("tag", "--list").splitlines():
    if not tag.strip():
        continue
    # Against the tip, not the merge: a tag created after this merge is still
    # reachable history and must not be reported as lost.
    check(f"tag {tag} is reachable", reachable(tag, TIP),
          git("rev-parse", "--short", tag))

# 4. Nothing was lost: the merged tree equals the branch tree. ----------------
#
# main was an ancestor, so a --no-ff merge must reproduce the branch tree
# exactly. A difference would mean the merge resolved something, which for a
# fast-forwardable merge means something was dropped.
check("the merged tree is identical to the branch tree",
      git("rev-parse", f"{MERGE}^{{tree}}") == git("rev-parse", f"{BRANCH}^{{tree}}"),
      git("rev-parse", "--short", f"{MERGE}^{{tree}}"))

print()
for line in notes + failures:
    print(f"  {line}")
print()
if failures:
    print(f"  == ancestry verification FAILED: {len(failures)} check(s) ==")
    sys.exit(1)
print(f"  == ancestry verification PASSED: {len(notes)} checks ==")
