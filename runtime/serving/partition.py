"""Choose a contiguous layer boundary for a two-vendor pipeline.

Exactly one boundary. Alternating layers between vendors would pay a host-staged
crossing per layer, which on the measured link (~240-500 MB/s) would dominate
decode latency; one crossing per token is the only shape that makes sense until
a faster transport is qualified.

The split is chosen from real tensor sizes rather than by halving the layer
count, because the prologue (embeddings) and epilogue (final norm, LM head) are
large and land on opposite ends. On Mistral-Nemo each is ~1.25 GiB, so an
even layer count is only balanced because those two happen to match.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from model_inspect import GIB, MIB, ModelInspection

CUDA_STAGE = "cuda"
ROCM_STAGE = "rocm"


class PartitionError(RuntimeError):
    """No boundary satisfies the declared budgets."""


@dataclass(frozen=True)
class StagePlan:
    vendor: str
    layers: Tuple[int, ...]
    weight_bytes: int
    kv_bytes: int
    holds_prologue: bool
    holds_epilogue: bool
    budget_bytes: int

    @property
    def total_bytes(self) -> int:
        return self.weight_bytes + self.kv_bytes

    @property
    def headroom_bytes(self) -> int:
        return self.budget_bytes - self.total_bytes

    @property
    def layer_span(self) -> str:
        if not self.layers:
            return "none"
        return f"{self.layers[0]}-{self.layers[-1]}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vendor": self.vendor,
            "layers": self.layer_span,
            "layerCount": len(self.layers),
            "holdsEmbedding": self.holds_prologue,
            "holdsLMHead": self.holds_epilogue,
            "weightGiB": round(self.weight_bytes / GIB, 3),
            "kvCacheGiB": round(self.kv_bytes / GIB, 3),
            "totalGiB": round(self.total_bytes / GIB, 3),
            "budgetGiB": round(self.budget_bytes / GIB, 3),
            "headroomGiB": round(self.headroom_bytes / GIB, 3),
        }


@dataclass(frozen=True)
class PipelinePlan:
    boundary: int                       # last layer owned by the first stage
    stages: Tuple[StagePlan, StagePlan]
    activation_bytes_per_token: int
    prefill_activation_bytes: int
    context_length: int
    batch: int
    transport: str
    balance_ratio: float
    reasons: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution": "pipeline-parallel",
            "boundaryAfterLayer": self.boundary,
            "transport": self.transport,
            "contextLength": self.context_length,
            "batch": self.batch,
            "activationBytesPerToken": self.activation_bytes_per_token,
            "prefillActivationMiB": round(self.prefill_activation_bytes / MIB, 2),
            "balanceRatio": round(self.balance_ratio, 4),
            "stages": [s.to_dict() for s in self.stages],
            "reasons": list(self.reasons),
        }


def plan_pipeline(
    inspection: ModelInspection,
    cuda_budget: int,
    rocm_budget: int,
    context_length: int,
    batch: int = 1,
    transport: str = "host-staged-xvendor",
    first_stage: str = CUDA_STAGE,
    cuda_layer_speed: float = 1.0,
    rocm_layer_speed: float = 1.0,
) -> PipelinePlan:
    """Pick the boundary that best balances the two stages within budget.

    `*_layer_speed` are relative throughputs; the default of 1.0/1.0 balances by
    memory alone. Supplying measured values shifts work toward the faster card
    subject to capacity, which is the point of measuring before deciding.
    """
    layer_count = inspection.layer_count
    if layer_count < 2:
        raise PartitionError("a pipeline split needs at least two layers")

    second_stage = ROCM_STAGE if first_stage == CUDA_STAGE else CUDA_STAGE
    budget = {CUDA_STAGE: cuda_budget, ROCM_STAGE: rocm_budget}
    speed = {CUDA_STAGE: cuda_layer_speed, ROCM_STAGE: rocm_layer_speed}

    kv_per_token_per_layer = inspection.kv_bytes_per_token() // layer_count
    kv_per_layer = kv_per_token_per_layer * context_length * batch

    feasible: List[Tuple[float, int, StagePlan, StagePlan]] = []
    # `boundary` is the last layer index owned by the first stage. Both stages
    # must own at least one layer, so a stage never becomes a pure relay.
    for boundary in range(0, layer_count - 1):
        head = tuple(range(0, boundary + 1))
        tail = tuple(range(boundary + 1, layer_count))

        head_weights = sum(inspection.layer_bytes(i) for i in head) + inspection.prologue_bytes
        tail_weights = sum(inspection.layer_bytes(i) for i in tail) + inspection.epilogue_bytes
        head_kv = kv_per_layer * len(head)
        tail_kv = kv_per_layer * len(tail)

        first = StagePlan(first_stage, head, head_weights, head_kv, True, False,
                          budget[first_stage])
        second = StagePlan(second_stage, tail, tail_weights, tail_kv, False, True,
                           budget[second_stage])
        if first.headroom_bytes < 0 or second.headroom_bytes < 0:
            continue

        # Balance compute time, not bytes: layers/second is what stalls a stage.
        head_time = len(head) / max(speed[first_stage], 1e-6)
        tail_time = len(tail) / max(speed[second_stage], 1e-6)
        imbalance = abs(head_time - tail_time) / max(head_time + tail_time, 1e-6)
        feasible.append((imbalance, boundary, first, second))

    if not feasible:
        raise PartitionError(
            f"no contiguous boundary fits {cuda_budget / GIB:.1f} GiB CUDA + "
            f"{rocm_budget / GIB:.1f} GiB ROCm at context {context_length}; "
            f"the checkpoint needs {inspection.total_bytes / GIB:.2f} GiB of weights "
            f"plus {inspection.kv_bytes(context_length, batch) / GIB:.2f} GiB of KV cache")

    imbalance, boundary, first, second = min(feasible, key=lambda item: (item[0], item[1]))
    per_token = inspection.activation_bytes(1, batch)
    prefill = inspection.activation_bytes(context_length, batch)

    reasons = [
        f"one boundary after layer {boundary}: {first.vendor} runs "
        f"{len(first.layers)} layers, {second.vendor} runs {len(second.layers)}",
        f"{first.vendor} holds the embedding, {second.vendor} holds the LM head",
        "KV cache stays with the layers that own it; only the boundary hidden "
        "state crosses vendors",
        f"boundary activation {per_token} B/token, {prefill / MIB:.1f} MiB for a "
        f"{context_length}-token prefill",
    ]
    if speed[CUDA_STAGE] == speed[ROCM_STAGE] == 1.0:
        reasons.append(
            "balanced by layer count only; supply measured per-layer times to "
            "shift work toward the faster card")
    return PipelinePlan(boundary, (first, second), per_token, prefill,
                        context_length, batch, transport, imbalance, tuple(reasons))


def assign_tensors(inspection: ModelInspection, plan: PipelinePlan) -> Dict[str, List[str]]:
    """Which tensor names each stage must load, and no others.

    The loader must stream only these. Materialising the whole checkpoint in
    both processes and discarding half would peak at full model size per
    process -- which would still appear to work on a host with enough RAM while
    defeating the capacity demonstration entirely.
    """
    first, second = plan.stages
    owned = {first.vendor: set(first.layers), second.vendor: set(second.layers)}
    out: Dict[str, List[str]] = {first.vendor: [], second.vendor: []}

    for tensor in inspection.prologue:
        out[first.vendor].append(tensor.name)
    for tensor in inspection.epilogue:
        out[second.vendor].append(tensor.name)
    for index, group in inspection.layers.items():
        vendor = first.vendor if index in owned[first.vendor] else second.vendor
        for tensor in group.tensors:
            out[vendor].append(tensor.name)

    for vendor in out:
        out[vendor].sort()
    total = sum(len(v) for v in out.values())
    if total != len(inspection.tensors):
        raise PartitionError(
            f"tensor assignment covers {total} of {len(inspection.tensors)} tensors")
    return out
