"""Record of the HetCCL/HetHub -> MCCL/MHub rename. Already applied.

Kept as documentation of what moved and, more importantly, of what deliberately
did not. Re-running it now is a no-op.

Two systems in the literature are already called HETHUB (arXiv:2405.16256,
S. Xu et al.) and HetCCL (cited as [3] in the manuscript). The paper cites both
as prior art, spelled exactly as this project's former component names. A blind
find-and-replace would have rewritten those citations and attributed other
people's systems to this project, so `docs/paper/` was excluded and reviewed by
hand.

`dist/` was deleted rather than renamed. A wheel whose filename says `mccl`
while its recorded hashes describe the old build looks valid and is not.

What moved:

    runtime/hetccl                        -> runtime/mccl
    runtime/mccl/src/hetccl               -> runtime/mccl/src/mccl
    runtime/mccl/native/include/hetccl    -> runtime/mccl/native/include/mccl
    src/mccl/cli_hetccl.py                -> src/mccl/cli_mccl.py
    package openmycelium-hetccl           -> openmycelium-mccl
    HETCCL_* environment variables        -> MCCL_*

Counts, across 121 files: hetccl 464, HETCCL 164, HetCCL 118, HetHub 4,
hethub 1.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(__doc__)
    print("  This rename has already been applied; nothing to do.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
