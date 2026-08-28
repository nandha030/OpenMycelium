"""Correctness-first speculative decoding across heterogeneous endpoints."""

from __future__ import annotations

import random
from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Protocol, Sequence

from .protocol import ProtocolError


class DraftBackend(Protocol):
    def propose(self, context: Sequence[int], max_tokens: int) -> "DraftProposal": ...


class TargetBackend(Protocol):
    def score(self, context: Sequence[int], candidates: Sequence[int]) -> tuple[dict[int, float], ...]: ...


@dataclass(frozen=True)
class DraftProposal:
    tokens: tuple[int, ...]
    distributions: tuple[dict[int, float], ...]

    def validate(self, max_tokens: int | None = None) -> None:
        if not self.tokens or len(self.tokens) != len(self.distributions):
            raise ProtocolError("draft proposal must contain one probability distribution per token")
        if max_tokens is not None and len(self.tokens) > max_tokens:
            raise ProtocolError("draft backend returned more tokens than requested")
        for token, distribution in zip(self.tokens, self.distributions):
            _validate_distribution(distribution)
            if distribution.get(token, 0.0) <= 0:
                raise ProtocolError("draft token must have a positive probability")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "DraftProposal":
        raw_tokens = payload.get("tokens")
        raw_distributions = payload.get("distributions")
        if not isinstance(raw_tokens, list) or not isinstance(raw_distributions, list):
            raise ProtocolError("draft endpoint returned an invalid proposal")
        try:
            proposal = cls(
                tuple(int(token) for token in raw_tokens),
                tuple(
                    {int(token): float(probability) for token, probability in distribution.items()}
                    for distribution in raw_distributions
                    if isinstance(distribution, dict)
                ),
            )
        except (TypeError, ValueError) as error:
            raise ProtocolError("draft endpoint returned non-numeric token probabilities") from error
        proposal.validate()
        return proposal


@dataclass(frozen=True)
class SpeculativeStep:
    tokens: tuple[int, ...]
    accepted_draft_tokens: int
    proposed_draft_tokens: int
    rejected_at: int | None

    @property
    def acceptance_rate(self) -> float:
        return self.accepted_draft_tokens / self.proposed_draft_tokens if self.proposed_draft_tokens else 0.0


class SpeculativeDecoder:
    """Leviathan-style rejection sampling with a remote draft and target.

    The draft and target may run on different vendors. Only token IDs and
    probability distributions cross the network. The target remains the source
    of truth, preserving its output distribution when complete distributions
    are supplied by both endpoints.
    """

    def __init__(self, draft: DraftBackend, target: TargetBackend, seed: int | None = None):
        self.draft = draft
        self.target = target
        self.random = random.Random(seed)

    def step(self, context: Sequence[int], max_draft_tokens: int = 4) -> SpeculativeStep:
        if max_draft_tokens <= 0:
            raise ValueError("max_draft_tokens must be positive")
        proposal = self.draft.propose(context, max_draft_tokens)
        proposal.validate(max_draft_tokens)
        target_rows = self.target.score(context, proposal.tokens)
        if len(target_rows) != len(proposal.tokens) + 1:
            raise ProtocolError("target must return one distribution per draft token plus a bonus distribution")
        for distribution in target_rows:
            _validate_distribution(distribution)

        emitted: list[int] = []
        accepted = 0
        for index, token in enumerate(proposal.tokens):
            draft_distribution = proposal.distributions[index]
            target_distribution = target_rows[index]
            q = draft_distribution[token]
            p = target_distribution.get(token, 0.0)
            if self.random.random() <= min(1.0, p / q):
                emitted.append(token)
                accepted += 1
                continue
            correction = _positive_difference(target_distribution, draft_distribution)
            emitted.append(_sample(correction or target_distribution, self.random))
            return SpeculativeStep(tuple(emitted), accepted, len(proposal.tokens), index)

        emitted.append(_sample(target_rows[-1], self.random))
        return SpeculativeStep(tuple(emitted), accepted, len(proposal.tokens), None)

    def generate(
        self,
        context: Sequence[int],
        max_new_tokens: int,
        max_draft_tokens: int = 4,
        eos_token_id: int | None = None,
    ) -> tuple[int, ...]:
        if max_new_tokens < 0:
            raise ValueError("max_new_tokens must be non-negative")
        generated: list[int] = []
        while len(generated) < max_new_tokens:
            remaining = max_new_tokens - len(generated)
            step = self.step([*context, *generated], min(max_draft_tokens, remaining))
            for token in step.tokens:
                if len(generated) >= max_new_tokens:
                    break
                generated.append(token)
                if eos_token_id is not None and token == eos_token_id:
                    return tuple(generated)
        return tuple(generated)


def _validate_distribution(distribution: Mapping[int, float]) -> None:
    if not distribution:
        raise ProtocolError("probability distribution cannot be empty")
    total = 0.0
    for token, probability in distribution.items():
        if isinstance(token, bool) or not isinstance(token, int):
            raise ProtocolError("probability distribution token IDs must be integers")
        if probability < 0 or not isfinite(probability):
            raise ProtocolError("probabilities must be finite and non-negative")
        total += probability
    if total <= 0:
        raise ProtocolError("probability distribution must have positive mass")
    if abs(total - 1.0) > 1e-4:
        raise ProtocolError(f"probability distribution must be normalized; received mass {total:.6f}")


def _positive_difference(target: Mapping[int, float], draft: Mapping[int, float]) -> dict[int, float]:
    vocabulary = set(target) | set(draft)
    return {token: difference for token in vocabulary if (difference := target.get(token, 0.0) - draft.get(token, 0.0)) > 0}


def _sample(distribution: Mapping[int, float], generator: random.Random) -> int:
    _validate_distribution(distribution)
    total = sum(distribution.values())
    threshold = generator.random() * total
    cumulative = 0.0
    last = next(iter(distribution))
    for token in sorted(distribution):
        last = token
        cumulative += distribution[token]
        if threshold <= cumulative:
            return token
    return last
