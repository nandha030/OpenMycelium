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

from adapters import (AdapterError, ErrorCode, installed_adapters,  # noqa: E402
                      require_executable_adapter, resolve_adapter)

#: Producers always write the current version. Integrity accepts more than
#: execution does, because a manifest that can no longer run must still be
#: readable and audit-replayable -- the frozen evidence under release/ is all
#: schema 1, and it has to keep verifying.
CURRENT_MANIFEST_SCHEMA_VERSION = 2
READABLE_MANIFEST_SCHEMA_VERSIONS = frozenset({1, 2})
EXECUTABLE_MANIFEST_SCHEMA_VERSIONS = frozenset({2})

#: Retained: existing callers and tests import this name.
MANIFEST_SCHEMA_VERSION = CURRENT_MANIFEST_SCHEMA_VERSION

#: Required by schema 2. Absence in a v2 manifest is INVALID_MANIFEST, not
#: legacy: an incomplete v2 must not be offered the "replan" remedy meant for
#: a manifest that predates pinning.
ADAPTER_MANIFEST_FIELDS = ("adapterId", "adapterVersion", "adapterConfigDigest")

ALGORITHM = "mycelium-contiguous-pipeline-v1"

#: Filled once by `_build_identity`, then reused.
_BUILD_IDENTITY: Dict[str, str] = {}


class ManifestError(RuntimeError):
    """A placement is ambiguous, tampered with, stale or model-incompatible.

    The exit code lives on the exception rather than being chosen by whoever
    catches it, so a new condition can carry a different code without the
    caller growing a branch per error.
    """

    error_code = "MANIFEST_ERROR"
    exit_code = 66
    remediation = None

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"errorCode": self.error_code,
                                   "detail": str(self)}
        if self.remediation:
            payload["remediation"] = self.remediation
        return payload


class LegacyUnpinnedManifestError(ManifestError):
    """A schema 1 manifest: readable and replayable, but not executable."""

    error_code = "LEGACY_UNPINNED_MANIFEST"
    exit_code = 65
    remediation = "REPLAN_REQUIRED"


class InvalidManifestError(ManifestError):
    """A schema 2 manifest missing fields its own version requires.

    Deliberately not demoted to legacy: a truncated or hand-edited v2 that
    presented itself as merely old would be offered the wrong remedy.
    """

    error_code = "INVALID_MANIFEST"
    exit_code = 65


class UnsupportedManifestSchemaError(ManifestError):
    """A schema version this build does not know how to read at all."""

    error_code = "UNSUPPORTED_MANIFEST_SCHEMA"
    exit_code = 65


class AdapterMismatchError(ManifestError):
    """The pinned adapter identity disagrees with the checkpoint on disk.

    Raised only for the conditions this milestone introduces -- a pinned
    identity that no longer describes the checkpoint. The pre-existing
    structural failures (fingerprint drift, missing stage, wrong role) keep
    their original code and exit status; renumbering them would change
    behaviour that already ships.
    """

    error_code = "ADAPTER_MISMATCH"
    exit_code = 65


class AdapterUnavailableError(ManifestError):
    """The manifest pins an adapter this build does not have installed."""

    error_code = "ADAPTER_UNAVAILABLE"
    exit_code = 69


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


def _build_identity() -> Dict[str, str]:
    """Version, wheel content digest and MCCL version of the planning build.

    The content digest is here because a version string does not identify a
    build: two wheels can carry the same version and different code, and a
    qualification record keyed on the version alone would cover both.
    """
    # Cached: this cannot change inside a running process, and collecting it
    # shells out to git and imports mccl. Without the cache a test module that
    # builds thirty manifests paid that cost thirty times.
    if _BUILD_IDENTITY:
        return dict(_BUILD_IDENTITY)

    identity = {"openmycelium": "unknown", "openmyceliumContent": "unknown",
                "mccl": "unknown"}
    record: Dict[str, Any] = {}
    try:
        cli = os.path.join(_HERE, "..", "cli")
        if cli not in sys.path:
            sys.path.insert(0, cli)
        from provenance import collect  # noqa: PLC0415
        record = collect()
    except Exception:                                     # noqa: BLE001
        record = {}

    identity["openmycelium"] = str(record.get("openmycelium") or "unknown")
    identity["mccl"] = str(record.get("mcclVersion") or "unknown")

    # A checkout has no wheel to digest. It gets an identity naming the commit
    # instead of an empty string, because every field of the tuple must be
    # something -- and because a checkout genuinely is a different build from
    # any wheel, so it must never match a wheel's record. Sentinels, not blanks:
    # a blank would be indistinguishable from "not filled in yet".
    content = str(record.get("installedContentSha256") or "")
    if not content:
        commit = str(record.get("gitCommit") or "") or "unknown"
        content = f"source-checkout:{commit}"
    identity["openmyceliumContent"] = content
    _BUILD_IDENTITY.update(identity)
    return identity


def build_placement(inspection, model_path: str, fabric: Dict[str, Any],
                    cuda_budget: int, rocm_budget: int, context_length: int,
                    batch: int = 1, allow_cpu: bool = False,
                    job_id: Optional[str] = None) -> Dict[str, Any]:
    """Compile one placement from one Fabric snapshot and model inspection."""
    # First enforcement point, and the first thing this function does. An
    # unsupported architecture must be refused before a budget is examined,
    # before a plan is compiled, and long before a worker or a device context
    # exists. Anything below this line has already committed to something.
    adapter = require_executable_adapter(inspection.config, model_path=model_path)

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
        # Top level, so `manifestDigest` covers them without an allowlist:
        # `_digest` hashes the whole manifest minus itself. A manifest with the
        # adapter identity stripped fails integrity rather than executing.
        "adapterId": adapter.adapter_id,
        "adapterVersion": adapter.adapter_version,
        "adapterConfigDigest": adapter.config_digest(inspection.config),
        # Which build compiled this, recorded here rather than asked of the
        # process that executes it. A worker runs under the vendor's own
        # interpreter, where `openmycelium` is on PYTHONPATH but not installed,
        # so it cannot see the wheel's dist-info and cannot answer this. The
        # planner can, and the digest then makes its answer tamper-evident.
        "build": _build_identity(),
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
            # The two runtimes the plan was compiled against. Recorded because
            # qualification is scoped to them and a worker cannot see the other
            # vendor's interpreter: without this the tuple would have to be
            # rebuilt by shelling into both environments at execution time, or
            # a torch upgrade after planning would inherit the old record.
            "cudaRuntime": str(cuda.get("torch_version") or "unknown"),
            "rocmRuntime": str(rocm.get("torch_version") or "unknown"),
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
    """Integrity, for any readable schema. Never mutates the manifest.

    The digest is verified first, before the schema is judged and before any
    structural check. Anything that defaulted or normalised a field ahead of
    this would change what is hashed and make a legitimate file fail its own
    integrity check -- which is exactly how the frozen v1 evidence would have
    been destroyed.
    """
    actual = _digest(manifest)
    if manifest.get("manifestDigest") != actual:
        raise ManifestError("placement manifest digest mismatch; file was modified")

    version = manifest.get("schemaVersion")
    if version not in READABLE_MANIFEST_SCHEMA_VERSIONS:
        raise UnsupportedManifestSchemaError(
            f"placement schema {version} cannot be read by this build; "
            f"readable: {sorted(READABLE_MANIFEST_SCHEMA_VERSIONS)}")

    if version >= 2:
        missing = [field for field in ADAPTER_MANIFEST_FIELDS
                   if not manifest.get(field)]
        if missing:
            raise InvalidManifestError(
                f"schema {version} manifest is missing required adapter "
                f"fields: {', '.join(missing)}")
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


def _validate_adapter_identity(manifest: Dict[str, Any], inspection) -> None:
    """The pinned identity must still describe the checkpoint on disk.

    This is what catches a checkpoint edited between planning and execution:
    the config digest is recomputed from the file as it is now, not read back
    out of the manifest.
    """
    pinned_id = manifest.get("adapterId")
    pinned_version = manifest.get("adapterVersion")

    installed = {(adapter.adapter_id, adapter.adapter_version): adapter
                 for adapter in installed_adapters()}
    if (pinned_id, pinned_version) not in installed:
        raise AdapterUnavailableError(
            f"this placement pins {pinned_id}@{pinned_version}, which this "
            f"build does not have installed; available: "
            f"{', '.join(sorted(f'{i}@{v}' for i, v in installed)) or '(none)'}")

    try:
        resolved = resolve_adapter(inspection.config)
    except AdapterError as error:
        raise AdapterMismatchError(
            f"this placement pins {pinned_id}@{pinned_version}, but the "
            f"checkpoint no longer resolves to any installed adapter: "
            f"{error}") from error

    if (resolved.adapter_id, resolved.adapter_version) != (pinned_id, pinned_version):
        raise AdapterMismatchError(
            f"this placement pins {pinned_id}@{pinned_version}, but the "
            f"checkpoint resolves to "
            f"{resolved.adapter_id}@{resolved.adapter_version}")

    actual_digest = resolved.config_digest(inspection.config)
    if manifest.get("adapterConfigDigest") != actual_digest:
        raise AdapterMismatchError(
            "the model configuration changed after this placement was "
            "compiled; the placement is no longer valid for this checkpoint. "
            "Regenerate it with `openmycelium plan`.")


def validate_for_worker(manifest: Dict[str, Any], inspection, role: str,
                        runtime_version: str = "",
                        audit: Optional[Any] = None,
                        qualification: bool = True) -> StageAssignment:
    """Everything that must hold before a worker builds a stage.

    This is the second of two enforcement points. `create_placement` rejects
    an unsupported architecture before anything is allocated; this one guards
    the direct path, where a worker is started with a manifest that already
    exists. A worker necessarily exists by the time this runs, so the
    guarantee it gives is narrower and more precise: exit before stage
    construction, leaving no orphan and no resident VRAM.

    `runtime_version` is this worker's actual torch build. Supplying it lets the
    manifest's recorded runtime be checked against reality, which is what
    catches a torch upgrade between planning and execution -- the manifest alone
    cannot notice that, because it still records what was true when it was
    written.

    `qualification` exists so the unit tests can exercise the manifest rules on
    a machine with no ledger. Every production caller leaves it on.
    """
    validate_manifest(manifest)

    # Executability, after integrity. A readable manifest is not necessarily a
    # runnable one, and the frozen v1 evidence depends on that distinction.
    version = manifest.get("schemaVersion")
    if version not in EXECUTABLE_MANIFEST_SCHEMA_VERSIONS:
        raise LegacyUnpinnedManifestError(
            f"placement schema {version} predates adapter pinning and cannot "
            f"be executed; it remains readable and audit-replayable. "
            f"Regenerate it with `openmycelium plan`.")

    expected_fingerprint = model_fingerprint(inspection)
    if manifest["model"]["fingerprint"] != expected_fingerprint:
        raise ManifestError(
            "placement was compiled for a different model structure or checkpoint")

    # After the fingerprint, deliberately. The fingerprint already covers the
    # whole config, so it catches config drift first and keeps reporting it in
    # the words it always has; putting the adapter check ahead of it would
    # relabel a failure that already ships. What this adds is the case the
    # fingerprint cannot see: a pinned identity that no installed adapter
    # provides, or one that no longer matches what the checkpoint resolves to.
    _validate_adapter_identity(manifest, inspection)

    _validate_runtime(manifest, role, runtime_version)

    stages = manifest["stages"]
    _validate_ownership((tensor.name for tensor in inspection.tensors), stages)
    stage = stage_assignment(manifest, role)
    if stage.runtime != role:
        raise ManifestError(
            f"role {role} cannot execute runtime assignment {stage.runtime}")

    # Last, and still before the caller builds anything. Qualification is the
    # only check here that can be lifted by an operator, so it must not be able
    # to mask a structural failure underneath it.
    if qualification:
        require_qualified_manifest(manifest, audit=audit)
    return stage


def _validate_runtime(manifest: Dict[str, Any], role: str,
                      runtime_version: str) -> None:
    """This worker's torch must be the torch the plan was compiled against."""
    if not runtime_version:
        return
    key = "cudaRuntime" if role == "cuda" else "rocmRuntime"
    recorded = str((manifest.get("fabric") or {}).get(key) or "")
    if not recorded or recorded == "unknown":
        return
    if recorded != runtime_version:
        raise AdapterMismatchError(
            f"this placement was compiled against {role} runtime {recorded}, "
            f"but this worker is running {runtime_version}; the plan is no "
            f"longer valid for this environment. Regenerate it with "
            f"`openmycelium plan`.")


def require_qualified_manifest(manifest: Dict[str, Any],
                               audit: Optional[Any] = None) -> Dict[str, Any]:
    """Refuse to execute a situation nothing has ever measured.

    Default-deny. The refusal happens here rather than deeper because here is
    still before any device context exists, and the guarantee this milestone
    makes is that an unqualified situation costs no allocation.
    """
    from adapters.qualification import (  # noqa: PLC0415
        override_from_environment, require_qualified_execution,
        situation_from_manifest)
    try:
        situation = situation_from_manifest(manifest)
    except ValueError as error:
        # A situation that cannot be formed is a refusal, not a crash. Reaching
        # this means the manifest is missing something the tuple needs, and the
        # safe answer to "is this qualified" when the question cannot even be
        # asked is no.
        raise AdapterError(
            ErrorCode.ADAPTER_UNQUALIFIED,
            f"this placement does not carry everything a qualification record "
            f"is scoped to, so it cannot be shown to be qualified: {error}. "
            f"Regenerate it with `openmycelium plan`.",
            exit_code=65, remediation="REPLAN_REQUIRED") from error
    return require_qualified_execution(
        situation, override=override_from_environment(), audit=audit)
