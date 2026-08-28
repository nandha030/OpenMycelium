"""Token selection policy, stated once so every path agrees.

`torch.topk` does not guarantee which index wins a tie; `torch.argmax` returns
the first maximal index. The two therefore disagree exactly when the top logits
are bit-identical, which BF16 makes common: the measured run had `0x40ad` at
both token 1081 and token 1113, and `topk` returned the higher index first on
CPU and on GPU alike.

So:

* greedy decoding uses `argmax`;
* `topk` is diagnostic, or an input to sampling, and never the selector;
* reported candidate order is `(-logit, token_id)`, which is total and
  reproducible where `topk`'s tie order is neither;
* anything stochastic draws from one explicitly seeded generator, in the one
  process that owns the head.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def greedy_token(logits: Any, torch: Any) -> int:
    """The selected token: first maximal index, by definition.

    `logits` is the last position's vector. The argmax runs in BF16, on the
    values as they are, so no promotion can reorder near-ties.
    """
    flat = logits.reshape(-1)
    return int(torch.argmax(flat).item())


def ranked_candidates(logits: Any, torch: Any, count: int = 5
                      ) -> List[Dict[str, Any]]:
    """Top candidates in a deterministic order: by `(-logit, token_id)`.

    Sorting the ids of a `topk` result rather than trusting its order means two
    runs that tie report the same list, on any backend.
    """
    flat = logits.reshape(-1)
    take = min(max(count * 4, count), int(flat.numel()))
    ids = torch.topk(flat.float() if flat.numel() <= 4096 else flat,
                     k=take).indices.tolist()
    bits = flat.view(torch.uint16)
    rows = [(-float(flat[i].float().item()), int(i), int(bits[int(i)].item()))
            for i in ids]
    rows.sort()
    return [{"tokenId": token, "value": round(-negated, 5), "bits": f"0x{raw:04x}"}
            for negated, token, raw in rows[:count]]


def selection_agrees(a: Any, b: Any, torch: Any) -> Tuple[bool, int, int]:
    """Whether two logit vectors select the same token under the argmax policy."""
    first, second = greedy_token(a, torch), greedy_token(b, torch)
    return first == second, first, second
