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


class MistralStage:
    """Embedding and/or a contiguous run of decoder layers, and/or the head."""

    def __init__(self, config_path: str, spec: StageSpec, torch_module: Any):
        self.torch = torch_module
        self.spec = spec
        with open(config_path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        self.raw_config = raw

        from transformers import MistralConfig  # noqa: PLC0415

        self.config = MistralConfig(**raw)
        self.config._attn_implementation = "eager"
        self.dtype = getattr(torch_module, str(raw.get("torch_dtype", "bfloat16")))

        self.embed = None
        self.layers: Dict[int, Any] = {}
        self.norm = None
        self.head = None
        self.last_mask_shape: Optional[List[int]] = None
        self._build()

    def _build(self) -> None:
        torch = self.torch
        from transformers.models.mistral.modeling_mistral import (  # noqa: PLC0415
            MistralDecoderLayer, MistralRMSNorm, MistralRotaryEmbedding,
        )

        cfg = self.config
        # Construct on the meta device: allocating real storage here would
        # briefly double the stage's footprint before the weights arrive.
        with torch.device("meta"):
            if self.spec.holds_embedding:
                self.embed = torch.nn.Embedding(cfg.vocab_size, cfg.hidden_size)
            for index in self.spec.layer_indices:
                # The original global index, never a renumbered local one.
                self.layers[index] = MistralDecoderLayer(cfg, layer_idx=index)
            if self.spec.holds_head:
                self.norm = MistralRMSNorm(cfg.hidden_size, eps=cfg.rms_norm_eps)
                self.head = torch.nn.Linear(cfg.hidden_size, cfg.vocab_size, bias=False)

        # Rotary tables are small and derived from config; each stage builds its
        # own rather than shipping them across the boundary.
        self.rotary = MistralRotaryEmbedding(config=cfg).to(self.spec.device)

    # ------------------------------------------------------------- weights
    def _module_and_attr(self, name: str) -> Tuple[Any, str]:
        if name == "model.embed_tokens.weight":
            return self.embed, "weight"
        if name == "model.norm.weight":
            return self.norm, "weight"
        if name == "lm_head.weight":
            return self.head, "weight"
        if not name.startswith("model.layers."):
            raise StageModelError(f"unrecognised tensor {name}")
        rest = name[len("model.layers."):]
        index_text, _, path = rest.partition(".")
        layer = self.layers.get(int(index_text))
        if layer is None:
            raise StageModelError(f"{name} belongs to a layer this stage does not own")
        target: Any = layer
        parts = path.split(".")
        for part in parts[:-1]:
            target = getattr(target, part)
        return target, parts[-1]

    def install(self, tensors: Dict[str, Any]) -> int:
        """Attach loaded device tensors, replacing the meta-device parameters."""
        torch = self.torch
        installed = 0
        for name, tensor in tensors.items():
            module, attr = self._module_and_attr(name)
            if module is None:
                raise StageModelError(f"{name} has no home in this stage")
            if tensor.dtype != self.dtype:
                raise StageModelError(
                    f"{name} arrived as {tensor.dtype}, expected {self.dtype}; "
                    "silent promotion would change the arithmetic under test")
            setattr(module, attr, torch.nn.Parameter(tensor, requires_grad=False))
            installed += 1

        remaining = [n for n, p in self.named_meta_parameters()]
        if remaining:
            raise StageModelError(
                f"{len(remaining)} parameters are still on the meta device, "
                f"first: {remaining[0]}")
        return installed

    def named_meta_parameters(self) -> List[Tuple[str, Any]]:
        out: List[Tuple[str, Any]] = []
        for label, module in self._modules_with_labels():
            for name, param in module.named_parameters(recurse=True):
                if param.device.type == "meta":
                    out.append((f"{label}.{name}", param))
        return out

    def _modules_with_labels(self) -> List[Tuple[str, Any]]:
        items: List[Tuple[str, Any]] = []
        if self.embed is not None:
            items.append(("model.embed_tokens", self.embed))
        for index in sorted(self.layers):
            items.append((f"model.layers.{index}", self.layers[index]))
        if self.norm is not None:
            items.append(("model.norm", self.norm))
        if self.head is not None:
            items.append(("lm_head", self.head))
        return items

    # ------------------------------------------------------------- forward
    def _causal_mask(self, batch: int, sequence: int, past: int) -> Optional[Any]:
        """A causal mask, derived locally rather than shipped.

        A single query position needs no mask: with no cache it can only attend
        to itself, and with a cache every cached key precedes it, so the mask
        would be all zeros. Adding exact zeros changes nothing, so `None` is not
        an approximation here.
        """
        torch = self.torch
        total = past + sequence
        if sequence == 1:
            self.last_mask_shape = None
            return None
        mask = torch.full((sequence, total), torch.finfo(self.dtype).min,
                          dtype=self.dtype, device=self.spec.device)
        mask = mask.triu(diagonal=past + 1)
        mask = mask.unsqueeze(0).unsqueeze(0).expand(batch, 1, sequence, total)
        self.last_mask_shape = [int(d) for d in mask.shape]
        return mask

    def new_cache(self) -> Any:
        """A cache sized for the whole model, of which this stage fills its slice.

        `DynamicCache(config=...)` allocates one slot per layer of the *full*
        model, and the layers here were constructed with their global indices,
        so this stage writes only into its own slots. That the other slots stay
        empty is the property `cache_report` checks: it is what proves no stage
        is caching layers it does not own.
        """
        from transformers.cache_utils import DynamicCache  # noqa: PLC0415
        return DynamicCache(config=self.config)

    def cache_report(self, cache: Any) -> Dict[str, Any]:
        """Which layer slots this cache actually holds, and how long they are."""
        if cache is None:
            return {"populatedLayers": [], "lengths": {}, "bytes": 0}
        populated, lengths, total = [], {}, 0
        for index in range(self.config.num_hidden_layers):
            keys = getattr(cache.layers[index], "keys", None)
            if keys is None:
                continue
            populated.append(index)
            lengths[str(index)] = int(keys.shape[-2])
            values = cache.layers[index].values
            total += keys.numel() * keys.element_size()
            total += values.numel() * values.element_size()
        distinct = sorted(set(lengths.values()))
        return {
            "populatedLayers": populated,
            "layerCount": len(populated),
            "lengths": lengths,
            "uniformLength": distinct[0] if len(distinct) == 1 else None,
            "distinctLengths": distinct,
            "bytes": total,
        }

    def forward(self, hidden_or_ids: Any, meta: BoundaryMeta,
                past_key_values: Optional[Any] = None,
                last_position_only: bool = False,
                profiler: Optional[Any] = None) -> Any:
        """Run this stage. Input is token IDs on the first stage, else a hidden state.

        Passing a cache both reads the stage's existing keys and values and
        appends this step's. Passing `None` recomputes from scratch and leaves
        any cache untouched, which is what makes the two paths comparable.
        """
        torch = self.torch
        device = self.spec.device

        # A profiler that is absent behaves exactly as before: the null object
        # keeps the fast path free of conditionals without changing control flow.
        if profiler is None:
            from profiler import NullProfiler  # noqa: PLC0415
            profiler = NullProfiler()

        position_ids = torch.tensor([meta.position_ids], dtype=torch.long, device=device)
        if self.spec.holds_embedding:
            ids = hidden_or_ids.to(device)
            with profiler.time("embedding"):
                hidden = self.embed(ids)
        else:
            hidden = hidden_or_ids.to(device)
            if hidden.dtype != self.dtype:
                raise StageModelError(
                    f"boundary activation arrived as {hidden.dtype}, expected "
                    f"{self.dtype}; refusing a silent promotion")

        batch, sequence = hidden.shape[0], hidden.shape[1]
        if position_ids.shape[-1] != sequence:
            raise StageModelError(
                f"{position_ids.shape[-1]} position ids for {sequence} tokens; "
                "an off-by-one here shifts RoPE for every layer downstream")
        mask = self._causal_mask(batch, sequence, meta.past_length)
        rotary = self.rotary(hidden, position_ids)

        phase = "decode" if meta.past_length else "prefill"
        for index in sorted(self.layers):
            layer = self.layers[index]
            # Labelled by global layer index and phase: prefill and decode are
            # different workloads and one curve cannot model both.
            with profiler.time(f"layer.{phase}.{index:02d}"):
                out = layer(hidden, attention_mask=mask, position_ids=position_ids,
                            past_key_values=past_key_values,
                            use_cache=past_key_values is not None,
                            position_embeddings=rotary)
            hidden = out[0] if isinstance(out, tuple) else out
            if hidden.dtype != self.dtype:
                raise StageModelError(
                    f"layer {index} produced {hidden.dtype}, expected {self.dtype}")

        if self.spec.holds_head:
            if last_position_only:
                # The norm and the head are per-position, so slicing first is
                # exact, not an approximation. It also avoids materialising a
                # [1, 2048, 131072] BF16 tensor -- half a gigabyte -- to read
                # one row of it.
                hidden = hidden[:, -1:, :]
            with profiler.time("final_norm"):
                hidden = self.norm(hidden)
            with profiler.time("lm_head"):
                logits = self.head(hidden)
            return logits
        return hidden

    def workspace_probe(self, batch: int, sequence: int) -> Dict[str, int]:
        """Transient allocation for one forward, beyond the resident weights."""
        torch = self.torch
        if not self.spec.device.startswith("cuda"):
            return {}
        before = torch.cuda.memory_allocated()
        torch.cuda.reset_peak_memory_stats()
        return {"beforeBytes": before}
