"""Build one pipeline stage from real Transformers Mistral components.

Uses `MistralDecoderLayer` rather than reimplementing attention. Rewriting the
mathematics would make any numerical disagreement with a reference ambiguous:
a wrong result could be the split, the transport, or my attention kernel. Using
the library's own layer leaves only the split and the transport in question.

Two things must survive the vendor boundary intact:

* **Global layer indices.** A `MistralDecoderLayer` is constructed with its
  `layer_idx`, which selects its KV-cache slot. The ROCm stage owns layers
  20-39 and must construct them with those indices, not renumber them 0-19,
  or every cache lookup lands in the wrong place once caching is enabled.
* **Position information.** RoPE is computed from `position_ids`, so both
  stages must derive rotary embeddings from the same positions. The second
  stage cannot infer them from a hidden state alone; they travel as metadata.

Only the hidden state crosses. Attention masks and rotary tables are derived
independently on each side from small metadata, because sending them would
duplicate large tensors for no benefit.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


class StageModelError(RuntimeError):
    """The stage could not be constructed or populated as planned."""


@dataclass
class StageSpec:
    vendor: str
    layer_indices: Tuple[int, ...]
    holds_embedding: bool
    holds_head: bool
    device: str

    @property
    def is_first(self) -> bool:
        return self.holds_embedding

    @property
    def is_last(self) -> bool:
        return self.holds_head


@dataclass
class BoundaryMeta:
    """Everything the next stage needs that is not the hidden state itself."""

    batch: int
    sequence: int
    hidden: int
    dtype: str
    position_ids: List[int]
    cache_position: List[int]
    use_cache: bool = False
    past_length: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "batch": self.batch, "sequence": self.sequence, "hidden": self.hidden,
            "dtype": self.dtype, "positionIds": self.position_ids,
            "cachePosition": self.cache_position, "useCache": self.use_cache,
            "pastLength": self.past_length,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "BoundaryMeta":
        required = ("batch", "sequence", "hidden", "dtype", "positionIds",
                    "cachePosition")
        missing = [k for k in required if k not in payload]
        if missing:
            raise StageModelError(f"boundary metadata is missing {missing}")
        return cls(int(payload["batch"]), int(payload["sequence"]),
                   int(payload["hidden"]), str(payload["dtype"]),
                   [int(p) for p in payload["positionIds"]],
                   [int(p) for p in payload["cachePosition"]],
                   bool(payload.get("useCache", False)),
                   int(payload.get("pastLength", 0)))


# `MistralStage` now lives in `adapters/mistral.py`. It is re-exported here so
# every existing importer -- pipeline_run, forward_pass, the repro harnesses --
# keeps working unchanged.
#
# Resolved lazily rather than by a module-level import because `adapters.mistral`
# imports `StageSpec` and `BoundaryMeta` from this module: an eager import here
# would be a cycle, and whichever module was imported second would see a
# half-initialised first. `from stage_model import MistralStage` still works,
# because `from X import Y` falls back to the module `__getattr__`.
def __getattr__(name: str) -> Any:
    if name == "MistralStage":
        from adapters.mistral import MistralStage  # noqa: PLC0415
        return MistralStage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
