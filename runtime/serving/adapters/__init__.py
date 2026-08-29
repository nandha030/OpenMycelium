"""Model architecture adapters: resolution, registry, and fail-closed refusal.

An adapter owns everything architecture-specific about executing a checkpoint:
how a stage is built, what the cache looks like, which tokenizer applies. The
runtime asks the registry which adapter claims a checkpoint and refuses when
the answer is not exactly one.

Resolution reads only the checkpoint. Not the directory name, not the
repository name, not a flag. A Mistral checkpoint in a directory called
`llama-7b` is a Mistral checkpoint.

See docs/ADAPTER_SDK.md, which this implements.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVING = os.path.dirname(_HERE)
if _SERVING not in sys.path:
    sys.path.insert(0, _SERVING)

ADAPTER_API_VERSION = 1


class ErrorCode:
    UNSUPPORTED_ARCHITECTURE = "UNSUPPORTED_ARCHITECTURE"
    AMBIGUOUS_ADAPTER = "AMBIGUOUS_ADAPTER"
    ADAPTER_UNAVAILABLE = "ADAPTER_UNAVAILABLE"
    ADAPTER_MISMATCH = "ADAPTER_MISMATCH"
    INVALID_CHECKPOINT = "INVALID_CHECKPOINT"
    CUSTOM_CODE_REFUSED = "CUSTOM_CODE_REFUSED"
    ADAPTER_UNQUALIFIED = "ADAPTER_UNQUALIFIED"


class CompatibilityStatus:
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED_ARCHITECTURE = "UNSUPPORTED_ARCHITECTURE"
    INVALID_CHECKPOINT = "INVALID_CHECKPOINT"
    ADAPTER_UNAVAILABLE = "ADAPTER_UNAVAILABLE"
    CONVERSION_REQUIRED = "CONVERSION_REQUIRED"
    CUSTOM_CODE_REFUSED = "CUSTOM_CODE_REFUSED"


class QualificationStatus:
    HARDWARE_QUALIFIED = "HARDWARE_QUALIFIED"
    UNQUALIFIED = "UNQUALIFIED"


class AdapterError(RuntimeError):
    """Carries its own machine-readable code and exit status."""

    def __init__(self, code: str, message: str, exit_code: int = 65,
                 remediation: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.error_code = code
        self.exit_code = exit_code
        self.remediation = remediation

    def as_dict(self) -> Dict[str, Any]:
        payload = {"errorCode": self.code, "detail": str(self)}
        if self.remediation:
            payload["remediation"] = self.remediation
        return payload


#: Excluded from adapterConfigDigest. Only these two, and only because they
#: provably do not reach model construction. Everything else is hashed --
#: MistralStage passes the whole config into MistralConfig(**raw), so an
#: unlisted key can still change behaviour, and hashing an unknown key is safe
#: while skipping one is not.
DIGEST_EXCLUDED_KEYS = ("transformers_version", "_name_or_path")


def canonical_json(value: Dict[str, Any]) -> bytes:
    """The project's one canonical form, matching placement._canonical."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


class ModelAdapter:
    """What every architecture must provide. See docs/ADAPTER_SDK.md §3."""

    adapter_id: str = ""
    adapter_version: str = ""
    adapter_api_version: int = ADAPTER_API_VERSION
    architectures: frozenset = frozenset()

    def claims(self, config: Dict[str, Any]) -> bool:
        """True when any declared architecture is one this adapter handles."""
        declared = config.get("architectures") or []
        if any(name in self.architectures for name in declared):
            return True
        return False

    def config_digest(self, config: Dict[str, Any]) -> str:
        subset = {key: value for key, value in config.items()
                  if key not in DIGEST_EXCLUDED_KEYS}
        return hashlib.sha256(canonical_json(subset)).hexdigest()

    def validate_checkpoint(self, config: Dict[str, Any],
                            tensors: Sequence) -> List[str]:
        raise NotImplementedError

    def build_stage(self, config_path: str, spec: Any, torch: Any) -> Any:
        raise NotImplementedError

    def identity(self) -> Dict[str, Any]:
        return {"adapterId": self.adapter_id,
                "adapterVersion": self.adapter_version,
                "adapterApiVersion": self.adapter_api_version}

    def __repr__(self) -> str:
        return f"<{self.adapter_id}@{self.adapter_version}>"


# ------------------------------------------------------------------ registry

def _registry() -> List[ModelAdapter]:
    from adapters.mistral import MistralAdapter  # noqa: PLC0415
    return [MistralAdapter()]


def installed_adapters() -> List[ModelAdapter]:
    return list(_registry())


def declared_architectures(config: Dict[str, Any]) -> List[str]:
    """Every entry, not just the first.

    A checkpoint listing two architectures claims both, and reading only
    element zero would silently ignore half of what it says.
    """
    declared = config.get("architectures") or []
    return [str(name) for name in declared]


def resolve_adapter(config: Dict[str, Any],
                    model_path: Optional[str] = None) -> ModelAdapter:
    """The one adapter that claims this checkpoint, or an error.

    `model_path` is accepted and deliberately unused for resolution: callers
    pass it for error messages, and taking a hint from it would break the rule
    that resolution reads only the checkpoint.
    """
    if config.get("trust_remote_code") or config.get("auto_map"):
        raise AdapterError(
            ErrorCode.CUSTOM_CODE_REFUSED,
            "this checkpoint requires executing repository-supplied Python, "
            "which this build never does",
            exit_code=77)

    declared = declared_architectures(config)
    matches = [adapter for adapter in installed_adapters()
               if adapter.claims(config)]

    if len(matches) == 1:
        return matches[0]

    installed = ", ".join(sorted(
        name for adapter in installed_adapters() for name in adapter.architectures))
    if not matches:
        raise AdapterError(
            ErrorCode.UNSUPPORTED_ARCHITECTURE,
            f"no installed adapter handles {declared or ['(none declared)']}; "
            f"installed architectures: {installed or '(none)'}")

    # Never broken by precedence: two adapters claiming one architecture is a
    # registry defect, and picking one would hide it.
    raise AdapterError(
        ErrorCode.AMBIGUOUS_ADAPTER,
        f"{len(matches)} installed adapters claim {declared}: "
        f"{', '.join(repr(a) for a in matches)}. This is a registry defect.",
        exit_code=70)


def require_executable_adapter(config: Dict[str, Any],
                               model_path: Optional[str] = None) -> ModelAdapter:
    """The gate before planning. Raises before anything is allocated."""
    return resolve_adapter(config, model_path=model_path)


def describe_compatibility(config: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only. Always answers, never raises: the Models screen needs this."""
    declared = declared_architectures(config)
    try:
        adapter = resolve_adapter(config)
    except AdapterError as error:
        return {
            "architecture": declared[0] if declared else None,
            "architectures": declared,
            "adapterId": None, "adapterVersion": None,
            "compatibilityStatus": (
                CompatibilityStatus.CUSTOM_CODE_REFUSED
                if error.code == ErrorCode.CUSTOM_CODE_REFUSED
                else CompatibilityStatus.UNSUPPORTED_ARCHITECTURE),
            "compatibilityReason": str(error),
        }
    return {
        "architecture": declared[0] if declared else None,
        "architectures": declared,
        "adapterId": adapter.adapter_id,
        "adapterVersion": adapter.adapter_version,
        "adapterApiVersion": adapter.adapter_api_version,
        "adapterConfigDigest": adapter.config_digest(config),
        "compatibilityStatus": CompatibilityStatus.SUPPORTED,
        "compatibilityReason": "",
    }


__all__ = [
    "ADAPTER_API_VERSION", "AdapterError", "CompatibilityStatus", "ErrorCode",
    "ModelAdapter", "QualificationStatus", "canonical_json",
    "declared_architectures", "describe_compatibility", "installed_adapters",
    "require_executable_adapter", "resolve_adapter",
]
