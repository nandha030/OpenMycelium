"""Decoding policy, and the gate that keeps unvalidated sampling switched off.

Greedy decoding is validated: the argmax agreed with full recomputation on all
32 steps of the oracle. Sampling is not, and the oracle says why rather than
leaving it to assumption -- top-5 overlap fell to 0.800 once and top-20 fell
below 1.000 on 8 of 32 steps. The cached and recomputed distributions therefore
rank lower candidates differently, and a sampler reading those ranks can diverge
where an argmax does not.

Top-k overlap alone cannot say how much that matters, because it counts set
membership and ignores probability mass. The measurements that would settle it
are in `sampling_audit.py`. Until those pass, this module refuses
`temperature > 0` instead of quietly permitting a decode path nothing has
checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


class SamplingNotValidated(RuntimeError):
    """Sampling was requested before the audit that would justify it."""


@dataclass
class DecodeRequest:
    temperature: float = 0.0
    do_sample: bool = False
    top_k: Optional[int] = None
    top_p: Optional[float] = None
    seed: Optional[int] = None
    max_new_tokens: int = 64

    def validate(self, allow_unvalidated: bool = False) -> "DecodeRequest":
        if self.max_new_tokens < 1:
            raise ValueError("max_new_tokens must be at least 1")
        stochastic = bool(self.do_sample) or self.temperature > 0.0 \
            or self.top_k is not None or self.top_p is not None
        if stochastic and not allow_unvalidated:
            raise SamplingNotValidated(
                "this build accepts only deterministic greedy decoding "
                '({"temperature": 0, "do_sample": false}). Sampled decoding is '
                "not validated across the vendor boundary: the cached and "
                "recomputed logit distributions agree on the argmax at every "
                "step but not on the ordering of lower-ranked candidates. "
                "Run scripts/repro/sampling_audit.py to qualify it.")
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {"temperature": self.temperature, "do_sample": self.do_sample,
                "top_k": self.top_k, "top_p": self.top_p, "seed": self.seed,
                "max_new_tokens": self.max_new_tokens}


def clamp_kl(value: float) -> float:
    """KL is non-negative; a small negative is FP32 rounding, not a divergence.

    The raw figure is kept by the caller for diagnostics -- a *large* negative
    would mean the computation is wrong and should not be hidden.
    """
    return 0.0 if -1e-6 < value < 0.0 else value
