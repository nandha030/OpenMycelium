"""Assert the placement gives each stage exclusive ownership of its tensors.

181 and 182 with no overlap is the property that makes the split real: every
tensor lives on exactly one card, so neither GPU is holding a spare copy.
"""

import json
import sys


def main(path: str) -> int:
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    stages = manifest["stages"]
    counts = sorted(len(stage["tensors"]) for stage in stages)
    names = [set(stage["tensors"]) for stage in stages]
    overlap = names[0] & names[1] if len(names) == 2 else set()
    boundary = manifest["pipeline"]["boundaryAfterLayer"]
    print(f"  boundaryAfterLayer  {boundary}")
    print(f"  tensors per stage   {counts}  total {sum(counts)}")
    print(f"  overlap             {len(overlap)}")
    return 0 if counts == [181, 182] and not overlap else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
