"""Inspect a safetensors checkpoint and decide whether it fits across two GPUs.

Sizes come from the safetensors headers themselves, not from parameter-count
arithmetic. Each shard begins with an 8-byte little-endian header length
followed by a JSON header mapping every tensor to its dtype, shape, and byte
range, so the exact on-disk footprint of any subset of tensors is knowable
without reading a single weight.

That matters for the capacity question this module exists to answer: whether a
checkpoint too large for either card alone fits when partitioned across both.
An estimate would not be evidence.
"""

from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

GIB = 1024 ** 3
MIB = 1024 ** 2

#: Bytes per element, keyed by the dtype strings safetensors writes.
DTYPE_BYTES = {
    "F64": 8, "F32": 4, "F16": 2, "BF16": 2,
    "I64": 8, "I32": 4, "I16": 2, "I8": 1, "U8": 1, "BOOL": 1,
    "F8_E4M3": 1, "F8_E5M2": 1,
}


class InspectionError(RuntimeError):
    """The checkpoint is unreadable or not in a form this runner supports."""


def read_safetensors_header(path: str) -> Dict[str, Any]:
    """Read one shard's header without touching the weights."""
    try:
        with open(path, "rb") as handle:
            raw_len = handle.read(8)
            if len(raw_len) != 8:
                raise InspectionError(f"{os.path.basename(path)} is truncated")
            length = struct.unpack("<Q", raw_len)[0]
            if not 0 < length < 200 * MIB:
                raise InspectionError(
                    f"{os.path.basename(path)} declares an implausible header of {length} bytes")
            header = json.loads(handle.read(length))
    except (OSError, ValueError) as error:
        raise InspectionError(f"cannot read {os.path.basename(path)}: {error}") from error
    if not isinstance(header, dict):
        raise InspectionError(f"{os.path.basename(path)} header is not an object")
    return header


@dataclass(frozen=True)
class Tensor:
    name: str
    dtype: str
    shape: Tuple[int, ...]
    shard: str

    @property
    def elements(self) -> int:
        total = 1
        for dim in self.shape:
            total *= dim
        return total

    @property
    def nbytes(self) -> int:
        if self.dtype not in DTYPE_BYTES:
            raise InspectionError(f"unsupported dtype {self.dtype} on {self.name}")
        return self.elements * DTYPE_BYTES[self.dtype]


@dataclass
class LayerGroup:
    """Tensors belonging to one transformer layer."""

    index: int
    tensors: List[Tensor] = field(default_factory=list)

    @property
    def nbytes(self) -> int:
        return sum(t.nbytes for t in self.tensors)


@dataclass
class ModelInspection:
    path: str
    config: Dict[str, Any]
    tensors: List[Tensor]
    layers: Dict[int, LayerGroup]
    prologue: List[Tensor]          # embeddings, anything before layer 0
    epilogue: List[Tensor]          # final norm, LM head

    # ---------------------------------------------------------------- sizes
    @property
    def total_bytes(self) -> int:
        return sum(t.nbytes for t in self.tensors)

    @property
    def layer_count(self) -> int:
        return len(self.layers)

    @property
    def prologue_bytes(self) -> int:
        return sum(t.nbytes for t in self.prologue)

    @property
    def epilogue_bytes(self) -> int:
        return sum(t.nbytes for t in self.epilogue)

    def layer_bytes(self, index: int) -> int:
        return self.layers[index].nbytes

    @property
    def uniform_layers(self) -> bool:
        sizes = {g.nbytes for g in self.layers.values()}
        return len(sizes) <= 1

    # ------------------------------------------------------------ kv cache
    def kv_bytes_per_token(self) -> int:
        """K and V for every layer, one token, at the checkpoint's dtype.

        Grouped-query attention means the cache is sized by `num_key_value_heads`,
        not by `num_attention_heads`; using the latter overstates it fourfold on
        this model.
        """
        cfg = self.config
        kv_heads = int(cfg.get("num_key_value_heads") or cfg.get("num_attention_heads", 0))
        head_dim = int(cfg.get("head_dim") or 0)
        if not head_dim:
            hidden = int(cfg.get("hidden_size", 0))
            heads = int(cfg.get("num_attention_heads", 0))
            head_dim = hidden // heads if heads else 0
        element = DTYPE_BYTES.get(self._torch_dtype_tag(), 2)
        return 2 * kv_heads * head_dim * element * self.layer_count

    def kv_bytes(self, context_length: int, batch: int = 1) -> int:
        return self.kv_bytes_per_token() * context_length * batch

    def activation_bytes(self, tokens: int, batch: int = 1) -> int:
        """One boundary activation: the hidden state crossing between stages."""
        hidden = int(self.config.get("hidden_size", 0))
        return hidden * tokens * batch * DTYPE_BYTES.get(self._torch_dtype_tag(), 2)

    def _torch_dtype_tag(self) -> str:
        raw = str(self.config.get("torch_dtype", "bfloat16")).lower()
        return {"bfloat16": "BF16", "float16": "F16", "float32": "F32"}.get(raw, "BF16")

    # ------------------------------------------------------------- summary
    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "architecture": (self.config.get("architectures") or ["unknown"])[0],
            "modelType": self.config.get("model_type"),
            "dtype": self.config.get("torch_dtype"),
            "layers": self.layer_count,
            "hiddenSize": self.config.get("hidden_size"),
            "attentionHeads": self.config.get("num_attention_heads"),
            "kvHeads": self.config.get("num_key_value_heads"),
            "headDim": self.config.get("head_dim"),
            "vocabSize": self.config.get("vocab_size"),
            "maxPositionEmbeddings": self.config.get("max_position_embeddings"),
            "tensors": len(self.tensors),
            "totalBytes": self.total_bytes,
            "totalGiB": round(self.total_bytes / GIB, 3),
            "prologueBytes": self.prologue_bytes,
            "epilogueBytes": self.epilogue_bytes,
            "perLayerBytes": self.layer_bytes(0) if self.layers else 0,
            "perLayerMiB": round(self.layer_bytes(0) / MIB, 1) if self.layers else 0,
            "uniformLayers": self.uniform_layers,
            "kvBytesPerToken": self.kv_bytes_per_token(),
        }


#: Kept as a thin view onto the adapter registry rather than a second list.
#: It previously read `{"MistralForCausalLM", "LlamaForCausalLM"}`, was
#: referenced nowhere, and declared support for an architecture that would have
#: allocated VRAM on both cards and then failed in `MistralStage`. The registry
#: is now the single answer to "what can this build execute", and this name
#: cannot drift away from it.
def _supported_architectures() -> frozenset:
    from adapters import installed_adapters  # noqa: PLC0415
    return frozenset(name for adapter in installed_adapters()
                     for name in adapter.architectures)


SUPPORTED_ARCHITECTURES = _supported_architectures()


def inspect_model(path: str) -> ModelInspection:
    config_path = os.path.join(path, "config.json")
    if not os.path.isfile(config_path):
        raise InspectionError(f"no config.json under {path}")
    with open(config_path, "r", encoding="utf-8") as handle:
        config = json.load(handle)

    index_path = os.path.join(path, "model.safetensors.index.json")
    if os.path.isfile(index_path):
        with open(index_path, "r", encoding="utf-8") as handle:
            weight_map = json.load(handle).get("weight_map", {})
        shards = sorted(set(weight_map.values()))
    else:
        single = os.path.join(path, "model.safetensors")
        if not os.path.isfile(single):
            raise InspectionError(f"no safetensors index or model.safetensors under {path}")
        weight_map, shards = {}, ["model.safetensors"]

    tensors: List[Tensor] = []
    for shard in shards:
        shard_path = os.path.join(path, shard)
        if not os.path.isfile(shard_path):
            raise InspectionError(f"shard {shard} referenced by the index is missing")
        for name, meta in read_safetensors_header(shard_path).items():
            if name == "__metadata__":
                continue
            tensors.append(Tensor(name, str(meta.get("dtype", "")),
                                  tuple(int(d) for d in meta.get("shape", ())), shard))

    layers: Dict[int, LayerGroup] = {}
    prologue: List[Tensor] = []
    epilogue: List[Tensor] = []
    for tensor in tensors:
        index = _layer_index(tensor.name)
        if index is None:
            (epilogue if _is_epilogue(tensor.name) else prologue).append(tensor)
        else:
            layers.setdefault(index, LayerGroup(index)).tensors.append(tensor)

    if not layers:
        raise InspectionError("no transformer layers found; this loader expects "
                              "`model.layers.<n>.` tensor names")
    return ModelInspection(path, config, tensors, layers, prologue, epilogue)


def _layer_index(name: str) -> Optional[int]:
    marker = ".layers."
    if marker not in name:
        return None
    tail = name.split(marker, 1)[1]
    head = tail.split(".", 1)[0]
    return int(head) if head.isdigit() else None


def _is_epilogue(name: str) -> bool:
    return ("lm_head" in name) or name.endswith("model.norm.weight")


# ------------------------------------------------------------------ fit check

@dataclass
class FitVerdict:
    fits_combined: bool
    fits_single: bool
    required_bytes: int
    combined_budget: int
    largest_single_budget: int
    reasons: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fitsAcrossBothGPUs": self.fits_combined,
            "fitsOnASingleGPU": self.fits_single,
            "requiredGiB": round(self.required_bytes / GIB, 3),
            "combinedBudgetGiB": round(self.combined_budget / GIB, 3),
            "largestSingleBudgetGiB": round(self.largest_single_budget / GIB, 3),
            "reasons": list(self.reasons),
        }


def assess_fit(inspection: ModelInspection, cuda_budget: int, rocm_budget: int,
               context_length: int, batch: int = 1) -> FitVerdict:
    """Weights plus KV cache against the two budgets.

    `fits_single` is what makes a capacity claim meaningful: a model that also
    fits on one card demonstrates the mechanism but proves nothing about
    aggregating capacity.
    """
    weights = inspection.total_bytes
    kv = inspection.kv_bytes(context_length, batch)
    required = weights + kv
    combined = cuda_budget + rocm_budget
    largest = max(cuda_budget, rocm_budget)

    reasons: List[str] = [
        f"weights {weights / GIB:.2f} GiB + KV cache {kv / GIB:.2f} GiB "
        f"at {context_length} tokens x batch {batch} = {required / GIB:.2f} GiB",
    ]
    fits_combined = required <= combined
    fits_single = required <= largest
    if fits_single:
        reasons.append(
            f"this checkpoint also fits on one {largest / GIB:.1f} GiB card, so a "
            "successful run demonstrates the pipeline mechanism but does not "
            "prove capacity aggregation")
    else:
        reasons.append(
            f"exceeds the largest single budget ({largest / GIB:.1f} GiB), so a "
            "successful run requires both cards")
    if not fits_combined:
        reasons.append(
            f"does not fit the combined budget ({combined / GIB:.1f} GiB); reduce "
            "context length, batch, or use a smaller checkpoint")
    return FitVerdict(fits_combined, fits_single, required, combined, largest,
                      tuple(reasons))


def parse_size(text: str) -> int:
    """Accept `14GiB`, `14GB`, `14g`, or a plain byte count."""
    raw = str(text).strip().lower().replace(" ", "")
    units = (("gib", GIB), ("gb", 10 ** 9), ("mib", MIB), ("mb", 10 ** 6),
             ("g", GIB), ("m", MIB), ("b", 1))
    for suffix, scale in units:
        if raw.endswith(suffix):
            head = raw[: -len(suffix)]
            if head:
                return int(float(head) * scale)
    return int(float(raw))
