"""Explainable inference-stage placement for heterogeneous accelerators."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from typing import Sequence


@dataclass(frozen=True)
class InferenceNode:
    node_id: str
    runtime: str
    memory_mib: int
    prefill_tokens_per_second: float
    decode_tokens_per_second: float
    draft_tokens_per_second: float = 0
    queue_ms: float = 0
    network_mbps: float = 1000
    network_latency_ms: float = 1
    cost_per_hour: float = 0
    healthy: bool = True
    capabilities: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.node_id or self.runtime not in {"cuda", "rocm", "metal", "oneapi", "cpu"}:
            raise ValueError("inference node requires an ID and a supported runtime")
        if self.memory_mib < 0 or min(self.prefill_tokens_per_second, self.decode_tokens_per_second) <= 0:
            raise ValueError("inference node capacity and throughput must be valid")
        if self.queue_ms < 0 or self.network_mbps <= 0 or self.network_latency_ms < 0 or self.cost_per_hour < 0:
            raise ValueError("inference node queue, network, and cost values must be valid")


@dataclass(frozen=True)
class InferenceRequest:
    model: str
    prompt_tokens: int
    output_tokens: int
    model_memory_mib: int
    kv_bytes_per_token: int
    allow_disaggregation: bool = True
    allow_speculative: bool = False
    minimum_acceptance_rate: float = 0.6

    def validate(self) -> None:
        if not self.model or min(self.prompt_tokens, self.output_tokens, self.model_memory_mib, self.kv_bytes_per_token) < 0:
            raise ValueError("inference request values must be non-negative and include a model")
        if not 0 < self.minimum_acceptance_rate <= 1:
            raise ValueError("minimum_acceptance_rate must be in (0, 1]")

    def kv_cache_mib(self, tokens: int) -> float:
        return tokens * self.kv_bytes_per_token / (1024 * 1024)

    def resident_mib(self, tokens: int) -> float:
        """Memory a node must hold: model weights plus the KV cache for `tokens`."""
        return self.model_memory_mib + self.kv_cache_mib(tokens)


@dataclass(frozen=True)
class StageAssignment:
    stage: str
    node_id: str
    runtime: str


@dataclass(frozen=True)
class InferenceRoute:
    mode: str
    assignments: tuple[StageAssignment, ...]
    estimated_latency_ms: float
    compute_ms: float
    queue_ms: float
    transfer_ms: float
    cost_score: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["assignments"] = [asdict(item) for item in self.assignments]
        return payload


class HetRouter:
    """Choose a feasible inference route using measured node profiles."""

    def __init__(self, cost_weight_ms_per_dollar_hour: float = 100):
        if cost_weight_ms_per_dollar_hour < 0:
            raise ValueError("cost weight must be non-negative")
        self.cost_weight = cost_weight_ms_per_dollar_hour

    def plan(self, request: InferenceRequest, nodes: Sequence[InferenceNode]) -> InferenceRoute:
        request.validate()
        candidates = [node for node in nodes if node.healthy and node.memory_mib >= request.model_memory_mib]
        for node in candidates:
            node.validate()
        if not candidates:
            raise ValueError("no healthy inference node has enough model memory")

        routes = [self._single(request, node) for node in candidates]
        if request.allow_disaggregation:
            routes.extend(self._disaggregated(request, prefill, decode) for prefill, decode in product(candidates, repeat=2) if prefill.node_id != decode.node_id)
        if request.allow_speculative:
            routes.extend(
                self._speculative(request, draft, target)
                for draft, target in product(candidates, repeat=2)
                if draft.node_id != target.node_id and draft.draft_tokens_per_second > 0
            )
        if request.allow_disaggregation and request.allow_speculative:
            routes.extend(
                self._disaggregated_speculative(request, prefill, draft, target)
                for prefill, draft, target in product(candidates, repeat=3)
                if prefill.node_id != target.node_id
                and draft.node_id != target.node_id
                and draft.draft_tokens_per_second > 0
            )
        feasible = [route for route in routes if route is not None]
        if not feasible:
            raise ValueError(
                "no route holds the model and its KV cache in node memory: "
                f"{request.resident_mib(request.prompt_tokens + request.output_tokens):.0f} MiB required"
            )
        return min(feasible, key=lambda route: (route.cost_score, route.estimated_latency_ms, route.mode))

    def _single(self, request: InferenceRequest, node: InferenceNode) -> InferenceRoute | None:
        if not _holds(node, request, request.prompt_tokens + request.output_tokens):
            return None
        prefill = _duration(request.prompt_tokens, node.prefill_tokens_per_second)
        decode = _duration(request.output_tokens, node.decode_tokens_per_second)
        compute = prefill + decode
        return self._route(
            "whole-request",
            (StageAssignment("prefill+decode", node.node_id, node.runtime),),
            compute,
            node.queue_ms,
            0,
            node.cost_per_hour,
            ("model and KV cache remain on one node", "no cross-vendor cache transfer is required"),
        )

    def _disaggregated(self, request: InferenceRequest, prefill: InferenceNode, decode: InferenceNode) -> InferenceRoute | None:
        if not _holds(prefill, request, request.prompt_tokens) or not _holds(decode, request, request.prompt_tokens + request.output_tokens):
            return None
        prefill_ms = _duration(request.prompt_tokens, prefill.prefill_tokens_per_second)
        decode_ms = _duration(request.output_tokens, decode.decode_tokens_per_second)
        cache_bytes = request.prompt_tokens * request.kv_bytes_per_token
        transfer = _transfer_ms(cache_bytes, prefill, decode)
        return self._route(
            "disaggregated-prefill-decode",
            (
                StageAssignment("prefill", prefill.node_id, prefill.runtime),
                StageAssignment("decode", decode.node_id, decode.runtime),
            ),
            prefill_ms + decode_ms,
            prefill.queue_ms + decode.queue_ms,
            transfer,
            prefill.cost_per_hour + decode.cost_per_hour,
            ("KV cache is canonicalized and transferred once after prefill", "decode remains local after handoff"),
        )

    def _speculative(self, request: InferenceRequest, draft: InferenceNode, target: InferenceNode) -> InferenceRoute | None:
        if not _holds(target, request, request.prompt_tokens + request.output_tokens):
            return None
        prefill = _duration(request.prompt_tokens, target.prefill_tokens_per_second)
        draft_ms = _duration(request.output_tokens, draft.draft_tokens_per_second)
        verification_tokens = request.output_tokens * (1.0 - 0.5 * request.minimum_acceptance_rate)
        verify_ms = _duration(verification_tokens, target.decode_tokens_per_second)
        token_payload = request.output_tokens * 64
        transfer = _transfer_ms(token_payload, draft, target)
        return self._route(
            "cross-vendor-speculative",
            (
                StageAssignment("target-prefill", target.node_id, target.runtime),
                StageAssignment("draft", draft.node_id, draft.runtime),
                StageAssignment("target-verify", target.node_id, target.runtime),
            ),
            prefill + max(draft_ms, verify_ms),
            draft.queue_ms + target.queue_ms,
            transfer,
            draft.cost_per_hour + target.cost_per_hour,
            ("only token IDs and probability distributions cross vendors", "the target model remains the correctness authority"),
        )

    def _disaggregated_speculative(
        self,
        request: InferenceRequest,
        prefill: InferenceNode,
        draft: InferenceNode,
        target: InferenceNode,
    ) -> InferenceRoute | None:
        if not _holds(prefill, request, request.prompt_tokens) or not _holds(target, request, request.prompt_tokens + request.output_tokens):
            return None
        prefill_ms = _duration(request.prompt_tokens, prefill.prefill_tokens_per_second)
        draft_ms = _duration(request.output_tokens, draft.draft_tokens_per_second)
        verification_tokens = request.output_tokens * (1.0 - 0.5 * request.minimum_acceptance_rate)
        verify_ms = _duration(verification_tokens, target.decode_tokens_per_second)
        cache_bytes = request.prompt_tokens * request.kv_bytes_per_token
        token_payload = request.output_tokens * 64
        transfer = _transfer_ms(cache_bytes, prefill, target) + _transfer_ms(token_payload, draft, target)
        unique_nodes = {node.node_id: node for node in (prefill, draft, target)}
        return self._route(
            "disaggregated-speculative",
            (
                StageAssignment("prefill", prefill.node_id, prefill.runtime),
                StageAssignment("draft", draft.node_id, draft.runtime),
                StageAssignment("target-verify", target.node_id, target.runtime),
            ),
            prefill_ms + max(draft_ms, verify_ms),
            sum(node.queue_ms for node in unique_nodes.values()),
            transfer,
            sum(node.cost_per_hour for node in unique_nodes.values()),
            (
                "the canonical KV cache moves once from prefill to target",
                "the draft exchanges only token IDs and probability distributions with the target",
                "the target model remains the correctness authority",
            ),
        )

    def _route(
        self,
        mode: str,
        assignments: tuple[StageAssignment, ...],
        compute_ms: float,
        queue_ms: float,
        transfer_ms: float,
        hourly_cost: float,
        reasons: tuple[str, ...],
    ) -> InferenceRoute:
        latency = compute_ms + queue_ms + transfer_ms
        score = latency + hourly_cost * self.cost_weight
        return InferenceRoute(mode, assignments, latency, compute_ms, queue_ms, transfer_ms, score, reasons)


def _holds(node: InferenceNode, request: InferenceRequest, tokens: int) -> bool:
    """Whether `node` can hold the model weights and the KV cache for `tokens`."""
    return node.memory_mib >= request.resident_mib(tokens)


def _duration(tokens: float, tokens_per_second: float) -> float:
    return tokens / tokens_per_second * 1000 if tokens else 0.0


def _transfer_ms(payload_bytes: int, source: InferenceNode, destination: InferenceNode) -> float:
    bandwidth = min(source.network_mbps, destination.network_mbps) * 1_000_000 / 8
    return source.network_latency_ms + destination.network_latency_ms + payload_bytes / bandwidth * 1000
