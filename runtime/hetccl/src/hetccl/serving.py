"""OpenAI-compatible and HetCCL token-serving adapters."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .protocol import ProtocolError


@dataclass(frozen=True)
class ServingCapabilities:
    healthy: bool
    openai_compatible: bool
    speculative_extension: bool
    models: tuple[str, ...]
    runtime: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class OpenAICompatibleAdapter:
    """Small dependency-free client for vLLM and compatible model servers."""

    def __init__(
        self,
        base_url: str,
        model: str = "",
        api_key: str = "",
        timeout_seconds: float = 120,
        runtime: str = "unknown",
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.runtime = runtime
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("serving base_url must start with http:// or https://")

    def list_models(self) -> tuple[str, ...]:
        response = self._request("GET", "/v1/models")
        entries = response.get("data", [])
        if not isinstance(entries, list):
            raise ProtocolError("model server returned an invalid model list")
        return tuple(str(item["id"]) for item in entries if isinstance(item, dict) and item.get("id"))

    def chat(self, messages: Sequence[Mapping[str, str]], **options: object) -> dict[str, Any]:
        model = str(options.pop("model", self.model))
        if not model:
            raise ValueError("a model is required for chat completion")
        return self._request("POST", "/v1/chat/completions", {"model": model, "messages": list(messages), **options})

    def complete(self, prompt: str, **options: object) -> dict[str, Any]:
        model = str(options.pop("model", self.model))
        if not model:
            raise ValueError("a model is required for completion")
        return self._request("POST", "/v1/completions", {"model": model, "prompt": prompt, **options})

    def capabilities(self) -> ServingCapabilities:
        try:
            models = self.list_models()
            healthy = True
        except ProtocolError:
            models = ()
            healthy = False
        speculative = False
        if healthy:
            try:
                payload = self._request("GET", "/v1/hetccl/capabilities")
                speculative = bool(payload.get("speculative_decoding"))
            except ProtocolError:
                speculative = False
        return ServingCapabilities(healthy, healthy, speculative, models, self.runtime)

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(dict(payload)).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1024]
            raise ProtocolError(f"model server returned HTTP {error.code}: {detail}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ProtocolError(f"model server request failed: {error}") from error
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProtocolError("model server returned invalid JSON") from error
        if not isinstance(decoded, dict):
            raise ProtocolError("model server response must be a JSON object")
        return decoded


class VLLMServingAdapter(OpenAICompatibleAdapter):
    """vLLM adapter with optional OpenMycelium token-protocol extensions.

    Standard vLLM OpenAI endpoints support normal inference. Cross-vendor
    speculative decoding additionally requires an OpenMycelium worker plugin
    exposing ``/v1/hetccl/propose`` and ``/v1/hetccl/verify``.
    """

    def propose(self, context: Sequence[int], max_tokens: int) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/hetccl/propose",
            {"model": self.model, "context": list(context), "max_tokens": max_tokens},
        )

    def verify(self, context: Sequence[int], candidates: Sequence[int]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/hetccl/verify",
            {"model": self.model, "context": list(context), "candidates": list(candidates)},
        )


class RemoteDraftBackend:
    def __init__(self, adapter: VLLMServingAdapter):
        self.adapter = adapter

    def propose(self, context: Sequence[int], max_tokens: int) -> Any:
        from .speculative import DraftProposal

        response = self.adapter.propose(context, max_tokens)
        return DraftProposal.from_mapping(response)


class RemoteTargetBackend:
    def __init__(self, adapter: VLLMServingAdapter):
        self.adapter = adapter

    def score(self, context: Sequence[int], candidates: Sequence[int]) -> tuple[dict[int, float], ...]:
        response = self.adapter.verify(context, candidates)
        raw = response.get("distributions")
        if not isinstance(raw, list):
            raise ProtocolError("target endpoint returned no probability distributions")
        distributions: list[dict[int, float]] = []
        for row in raw:
            if not isinstance(row, dict):
                raise ProtocolError("target endpoint returned an invalid probability distribution")
            try:
                distributions.append({int(token): float(probability) for token, probability in row.items()})
            except (TypeError, ValueError) as error:
                raise ProtocolError("target endpoint returned a non-numeric probability distribution") from error
        return tuple(distributions)
