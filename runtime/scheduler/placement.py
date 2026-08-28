"""Authoritative, immutable placement manifests for Mycelium workers.

The coordinator calls the planner once. Workers never call ``plan_pipeline``;
they validate this manifest's digest, model fingerprint, exact tensor ownership
and role assignment, then execute only their stage. That removes the previous
split-brain arrangement where two processes happened to calculate the same
answer from similar inputs.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVING = os.path.join(_HERE, "..", "serving")
if _SERVING not in sys.path:
    sys.path.insert(0, _SERVING)

from partition import PipelinePlan, assign_tensors, plan_pipeline  # noqa: E402

MANIFEST_SCHEMA_VERSION = 1
ALGORITHM = "mycelium-contiguous-pipeline-v1"


class ManifestError(RuntimeError):
    """A placement is ambiguous, tampered with, stale or model-incompatible."""


@dataclass(frozen=True)
class StageAssignment:
    role: str
    device_identity: str
    identity_source: str
    identity_confidence: str
    runtime: str
    layers: tuple[int, ...]
    tensors: tuple[str, ...]
    weight_bytes: int
    kv_bytes: int
    budget_bytes: int
    holds_embedding: bool
    holds_lm_head: bool

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "StageAssignment":
        return cls(
            role=str(value["role"]),
            device_identity=str(value["deviceIdentity"]),
            identity_source=str(value["identitySource"]),
            identity_confidence=str(value["identityConfidence"]),
            runtime=str(value["runtime"]),
            layers=tuple(int(item) for item in value["layers"]),
            tensors=tuple(str(item) for item in value["tensors"]),
            weight_bytes=int(value["weightBytes"]),
            kv_bytes=int(value["kvBytes"]),
            budget_bytes=int(value["budgetBytes"]),
            holds_embedding=bool(value["holdsEmbedding"]),
            holds_lm_head=bool(value["holdsLMHead"]),
        )


def _canonical(value: Dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _digest(value: Dict[str, Any]) -> str:
    unsigned = dict(value)
    unsigned.pop("manifestDigest", None)
    return hashlib.sha256(_canonical(unsigned)).hexdigest()


def model_fingerprint(inspection) -> str:
    """Bind a placement to model structure without hashing 23 GiB of weights."""
    description = {
        "config": inspection.config,
        "tensors": [
            {"name": tensor.name, "dtype": tensor.dtype,
             "shape": list(tensor.shape), "shard": tensor.shard,
             "bytes": tensor.nbytes}
            for tensor in sorted(inspection.tensors, key=lambda item: item.name)
        ],
    }
    return hashlib.sha256(_canonical(description)).hexdigest()


def _device_for_runtime(fabric: Dict[str, Any], runtime: str,
                        allow_cpu: bool) -> Dict[str, Any]:
    if allow_cpu:
        return {
            "identity": f"test:{runtime}-stage", "identity_source": "test",
            "identityConfidence": "test-only", "runtime": runtime,
            "free_bytes": 1 << 60, "total_bytes": 1 << 60,
        }
    matches = [device for device in fabric.get("devices", [])
               if device.get("runtime") == runtime]
    if len(matches) != 1:
        raise ManifestError(
            f"expected exactly one {runtime} device, found {len(matches)}; "
            "multi-device selection needs an explicit device selector")
    device = matches[0]
    if device.get("identity_source") == "index":
        raise ManifestError(
            f"{runtime} device has only an unstable index identity; refresh "
            "Fabric with UUID or PCI evidence before placement")
    return device


def _validate_ownership(expected: Iterable[str], stages: List[Dict[str, Any]]) -> None:
    expected_names = set(expected)
    counts: Dict[str, int] = {}
    for stage in stages:
        for name in stage["tensors"]:
            counts[name] = counts.get(name, 0) + 1
    missing = sorted(expected_names - set(counts))
    unexpected = sorted(set(counts) - expected_names)
    duplicated = sorted(name for name, count in counts.items() if count != 1)
    if missing or unexpected or duplicated:
        raise ManifestError(
            "tensor ownership is not exclusive: "
            f"missing={missing[:3]} unexpected={unexpected[:3]} "
            f"duplicated={duplicated[:3]}")


def build_placement(inspection, model_path: str, fabric: Dict[str, Any],
                    cuda_budget: int, rocm_budget: int, context_length: int,
                    batch: int = 1, allow_cpu: bool = False,
                    job_id: Optional[str] = None) -> Dict[str, Any]:
    """Compile one placement from one Fabric snapshot and model inspection."""
    if not fabric.get("identitiesUnique", True):
        raise ManifestError(
            f"Fabric identities are ambiguous: {fabric.get('duplicateIdentities')}")
    cuda = _device_for_runtime(fabric, "cuda", allow_cpu)
    rocm = _device_for_runtime(fabric, "rocm", allow_cpu)
    for role, device, budget in (("cuda", cuda, cuda_budget),
                                 ("rocm", rocm, rocm_budget)):
        free = int(device.get("free_bytes", device.get("freeBytes", 0)))
        if not allow_cpu and free and budget > free:
            raise ManifestError(
                f"{role} budget {budget} exceeds Fabric free memory {free}")

    plan: PipelinePlan = plan_pipeline(
        inspection, cuda_budget, rocm_budget, context_length, batch=batch)
    tensors = assign_tensors(inspection, plan)
    device_by_role = {"cuda": cuda, "rocm": rocm}
    stages: List[Dict[str, Any]] = []
    for stage in plan.stages:
        device = device_by_role[stage.vendor]
        stages.append({
            "role": stage.vendor,
            "deviceIdentity": device["identity"],
            "identitySource": device.get("identity_source", "unknown"),
            "identityConfidence": device.get("identityConfidence", "unknown"),
            "runtime": stage.vendor,
            "layers": list(stage.layers),
            "tensors": list(tensors[stage.vendor]),
            "weightBytes": stage.weight_bytes,
            "kvBytes": stage.kv_bytes,
            "budgetBytes": stage.budget_bytes,
            "holdsEmbedding": stage.holds_prologue,
            "holdsLMHead": stage.holds_epilogue,
        })
    _validate_ownership((tensor.name for tensor in inspection.tensors), stages)

    manifest: Dict[str, Any] = {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "placementId": job_id or f"pl-{uuid.uuid4().hex[:16]}",
        "createdAt": round(time.time(), 3),
        "algorithm": ALGORITHM,
        "model": {
            "path": os.path.abspath(model_path),
            "fingerprint": model_fingerprint(inspection),
            "architecture": (inspection.config.get("architectures") or ["unknown"])[0],
            "tensorCount": len(inspection.tensors),
            "weightBytes": inspection.total_bytes,
        },
        "fabric": {
            "schemaVersion": fabric.get("schemaVersion"),
            "probedAt": fabric.get("probedAt"),
            "fromCache": bool(fabric.get("fromCache", False)),
        },
        "pipeline": {
            "boundaryAfterLayer": plan.boundary,
            "transport": plan.transport,
            "contextLength": context_length,
            "batch": batch,
            "activationBytesPerToken": plan.activation_bytes_per_token,
            "prefillActivationBytes": plan.prefill_activation_bytes,
            "balanceRatio": plan.balance_ratio,
            "reasons": list(plan.reasons),
        },
        "stages": stages,
    }
    manifest["manifestDigest"] = _digest(manifest)
    return manifest


def create_placement(model_path: str, fabric: Dict[str, Any], cuda_budget: int,
                     rocm_budget: int, context_length: int, batch: int = 1,
                     allow_cpu: bool = False,
                     job_id: Optional[str] = None) -> Dict[str, Any]:
    from model_inspect import inspect_model
    inspection = inspect_model(model_path)
    return build_placement(inspection, model_path, fabric, cuda_budget,
                           rocm_budget, context_length, batch, allow_cpu, job_id)


def write_placement(path: str, manifest: Dict[str, Any]) -> None:
    validate_manifest(manifest)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temporary = f"{path}.{os.getpid()}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass


def validate_manifest(manifest: Dict[str, Any]) -> None:
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        raise ManifestError(
            f"placement schema {manifest.get('schemaVersion')} is unsupported; "
            f"expected {MANIFEST_SCHEMA_VERSION}")
    actual = _digest(manifest)
    if manifest.get("manifestDigest") != actual:
        raise ManifestError("placement manifest digest mismatch; file was modified")
    stages = manifest.get("stages") or []
    roles = [stage.get("role") for stage in stages]
    if sorted(roles) != ["cuda", "rocm"]:
        raise ManifestError(f"placement must contain one cuda and one rocm stage: {roles}")
    if len({stage.get("deviceIdentity") for stage in stages}) != len(stages):
        raise ManifestError("two stages cannot lease the same Fabric identity")


def load_placement(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as error:
        raise ManifestError(f"cannot read placement manifest {path}: {error}") from error
    validate_manifest(manifest)
    return manifest


def stage_assignment(manifest: Dict[str, Any], role: str) -> StageAssignment:
    matches = [stage for stage in manifest["stages"] if stage["role"] == role]
    if len(matches) != 1:
        raise ManifestError(f"placement has {len(matches)} assignments for role {role}")
    return StageAssignment.from_dict(matches[0])


def validate_for_worker(manifest: Dict[str, Any], inspection,
                        role: str) -> StageAssignment:
    validate_manifest(manifest)
    expected_fingerprint = model_fingerprint(inspection)
    if manifest["model"]["fingerprint"] != expected_fingerprint:
        raise ManifestError(
            "placement was compiled for a different model structure or checkpoint")
    stages = manifest["stages"]
    _validate_ownership((tensor.name for tensor in inspection.tensors), stages)
    stage = stage_assignment(manifest, role)
    if stage.runtime != role:
        raise ManifestError(
            f"role {role} cannot execute runtime assignment {stage.runtime}")
    return stage
