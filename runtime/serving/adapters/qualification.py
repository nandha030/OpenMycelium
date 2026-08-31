"""Hardware qualification: default-deny, scoped to a situation, never to a name.

A qualification record does not say "mistral@1 works". It says "this exact
checkpoint, driven by this exact config, executed by this adapter version, on
these two runtimes, across this pair of devices, with this boundary, produced
the evidence in the gate". Change any element and the record no longer applies.

That is the whole point of the scoping. A label attached to an adapter name
would claim qualification for a different checkpoint, a torch upgrade, or a
different pair of cards -- three situations that have never been tested.

Default-deny follows from the same reasoning: an adapter with no matching
record has no evidence behind it here and now, so it is refused. Refusal can be
lifted only by an explicit, attributed override, and the override is written
into the audit trail with the situation it was used against.

See docs/ADAPTER_SDK.md §2.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

from adapters import (AdapterError, ErrorCode, QualificationScope,
                      QualificationStatus, canonical_json)

#: The tuple. Order is fixed because it is hashed.
#:
#: `openmyceliumContent` and `mcclContent` are the installed distributions'
#: content digests, and they are here because a version string does not identify
#: a build. This milestone alone produced seven wheels; a rebuilt wheel carrying
#: the same version would otherwise inherit a record it was never measured
#: against, which is exactly the "qualification by label" failure the scoping
#: exists to prevent.
#:
#: MCCL needs it more than openmycelium does. MCCL owns the wire protocol and
#: the transport, so a change there moves the boundary bytes that every
#: byte-exactness claim rests on -- and `mcclVersion` alone would have left that
#: hole open in precisely the layer where it matters most.
SITUATION_FIELDS = (
    "modelFingerprint",
    "adapterId",
    "adapterVersion",
    "adapterConfigDigest",
    "openmyceliumVersion",
    "openmyceliumContent",
    "mcclVersion",
    "mcclContent",
    "transport",
    "cudaRuntime",
    "rocmRuntime",
    "topology",
)

#: Where adapter records live inside the machine's qualification ledger. The
#: ledger already holds provisioning and transport qualification; a second file
#: would need its own path discovery, its own precedence and its own backup
#: story, and would drift from the first.
LEDGER_KEY = "adapterQualification"

#: Set to run the hardware gate itself, which is the one execution that must be
#: allowed before any record exists -- the gate is what produces the record.
#: `override` is the same mechanism used deliberately outside the gate.
QUALIFICATION_MODES = ("qualify", "override")

_HERE = os.path.dirname(os.path.abspath(__file__))

#: Last resort only. `records_path()` prefers the configured ledger; this is
#: what it falls back to on a machine with no configuration at all, and it
#: deliberately matches the ledger's documented default rather than inventing a
#: second location.
DEFAULT_RECORDS_PATH = "/var/lib/openmycelium/xvendor_qualification.json"


def records_path(explicit: Optional[str] = None) -> str:
    """The machine's qualification ledger, by the project's own precedence.

    Never inside site-packages: a record written there would be destroyed by the
    next `pip install`, and a qualification that disappears when the wheel is
    reinstalled is worse than none -- it would look like a regression.
    """
    if explicit:
        return explicit
    from_environment = (os.environ.get("OM_XVENDOR_LEDGER") or "").strip()
    if from_environment:
        return from_environment
    try:
        sys.path.insert(0, os.path.join(_HERE, "..", "..", "cli"))
        from config import load  # noqa: PLC0415
        configured = getattr(load(), "ledger", "")
        if configured:
            return configured
    except Exception:                                     # noqa: BLE001
        pass
    return DEFAULT_RECORDS_PATH


class QualificationError(AdapterError):
    """Execution refused: no record covers this situation and no override."""

    def __init__(self, message: str, situation: Dict[str, Any]):
        super().__init__(ErrorCode.ADAPTER_UNQUALIFIED, message, exit_code=65,
                         remediation="QUALIFICATION_REQUIRED")
        self.situation = dict(situation)

    def as_dict(self) -> Dict[str, Any]:
        payload = super().as_dict()
        payload["situationDigest"] = situation_digest(self.situation)
        return payload


def build_situation(model_fingerprint: str, adapter_id: str,
                    adapter_version: str, adapter_config_digest: str,
                    openmycelium_version: str, openmycelium_content: str,
                    mccl_version: str, mccl_content: str, transport: str,
                    cuda_runtime: str, rocm_runtime: str,
                    topology: str) -> Dict[str, str]:
    """Every field is required and none defaults.

    A default here would silently widen a record to cover a situation nobody
    measured, which is exactly the failure this design exists to prevent.
    """
    situation = {
        "modelFingerprint": model_fingerprint,
        "adapterId": adapter_id,
        "adapterVersion": adapter_version,
        "adapterConfigDigest": adapter_config_digest,
        "openmyceliumVersion": openmycelium_version,
        "openmyceliumContent": openmycelium_content,
        "mcclVersion": mccl_version,
        "mcclContent": mccl_content,
        "transport": transport,
        "cudaRuntime": cuda_runtime,
        "rocmRuntime": rocm_runtime,
        "topology": topology,
    }
    blank = [field for field in SITUATION_FIELDS if not situation[field]]
    if blank:
        raise ValueError(
            f"a qualification situation cannot have empty fields: {blank}")
    return situation


def situation_digest(situation: Dict[str, Any]) -> str:
    subset = {field: situation.get(field, "") for field in SITUATION_FIELDS}
    return hashlib.sha256(canonical_json(subset)).hexdigest()


def topology_from_manifest(manifest: Dict[str, Any]) -> str:
    """Both device identities and the boundary, in a stable textual form."""
    stages = sorted(manifest.get("stages") or [],
                    key=lambda stage: str(stage.get("role")))
    parts = [f"{stage.get('role')}:{stage.get('deviceIdentity')}"
             for stage in stages]
    boundary = (manifest.get("pipeline") or {}).get("boundaryAfterLayer")
    return " + ".join(parts) + f", boundary after layer {boundary}"


def build_identity() -> Dict[str, str]:
    """Which build is asking. Version and content digest, never version alone."""
    identity = {"openmyceliumVersion": "", "openmyceliumContent": "",
                "mcclVersion": "", "mcclContent": ""}
    try:
        sys.path.insert(0, os.path.join(_HERE, "..", "..", "cli"))
        from provenance import collect  # noqa: PLC0415
        record = collect()
        identity["openmyceliumVersion"] = str(record.get("openmycelium") or "")
        identity["openmyceliumContent"] = str(
            record.get("installedContentSha256") or "")
        identity["mcclVersion"] = str(record.get("mcclVersion") or "")
        identity["mcclContent"] = str(record.get("mcclContentSha256") or "")
    except Exception:                                     # noqa: BLE001
        pass
    return identity


def situation_from_manifest(manifest: Dict[str, Any],
                            identity: Optional[Dict[str, str]] = None
                            ) -> Dict[str, str]:
    """The tuple, read out of the manifest the worker is about to execute.

    Everything but the build identity comes from the manifest, which is digest
    protected -- so the situation a worker checks is the situation the planner
    compiled, and neither side can quietly widen it.
    """
    # The manifest's own record first. A worker must check the build the plan
    # was compiled by, not the one it happens to be able to see: under the
    # vendor interpreter `openmycelium` is on PYTHONPATH but not installed, so
    # asking the environment there returns blanks.
    recorded = manifest.get("build") or {}
    if recorded and not identity:
        identity = {"openmyceliumVersion": str(recorded.get("openmycelium") or ""),
                    "openmyceliumContent": str(
                        recorded.get("openmyceliumContent") or ""),
                    "mcclVersion": str(recorded.get("mccl") or ""),
                    "mcclContent": str(recorded.get("mcclContent") or "")}
    identity = identity or build_identity()
    fabric = manifest.get("fabric") or {}
    pipeline = manifest.get("pipeline") or {}
    return build_situation(
        model_fingerprint=str((manifest.get("model") or {}).get("fingerprint") or ""),
        adapter_id=str(manifest.get("adapterId") or ""),
        adapter_version=str(manifest.get("adapterVersion") or ""),
        adapter_config_digest=str(manifest.get("adapterConfigDigest") or ""),
        openmycelium_version=identity.get("openmyceliumVersion", ""),
        openmycelium_content=identity.get("openmyceliumContent", ""),
        mccl_version=identity.get("mcclVersion", ""),
        mccl_content=identity.get("mcclContent", ""),
        transport=str(pipeline.get("transport") or ""),
        cuda_runtime=str(fabric.get("cudaRuntime") or ""),
        rocm_runtime=str(fabric.get("rocmRuntime") or ""),
        topology=topology_from_manifest(manifest))


def load_records(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Missing or unreadable records file means no records, never an error.

    An absent file is the normal state before the first gate passes, and it
    must produce a refusal rather than a crash -- refusal is the safe answer,
    and a crash would be indistinguishable from a bug.
    """
    try:
        with open(records_path(path), "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(document, dict):
        return []
    section = document.get(LEDGER_KEY)
    records = (section or {}).get("records") if isinstance(section, dict) else section
    return [record for record in (records or []) if isinstance(record, dict)]


def find_record(situation: Dict[str, Any],
                records: Optional[List[Dict[str, Any]]] = None,
                path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """A record matches only when every field of the tuple matches."""
    if records is None:
        records = load_records(path)
    wanted = situation_digest(situation)
    for record in records:
        if situation_digest(record.get("situation") or record) == wanted:
            return record
    return None


def qualification_status(situation: Dict[str, Any],
                         records: Optional[List[Dict[str, Any]]] = None,
                         path: Optional[str] = None) -> str:
    if find_record(situation, records=records, path=path) is None:
        return QualificationStatus.UNQUALIFIED
    return QualificationStatus.HARDWARE_QUALIFIED


def override_from_environment(
        environ: Optional[Dict[str, str]] = None) -> Optional[Dict[str, str]]:
    """An override must be explicit and attributed, or it is not an override.

    Requiring an actor is what makes the audit entry worth reading: "someone
    set a variable" is not accountability. A mode with no actor is refused
    rather than quietly ignored, so a half-configured gate fails loudly.
    """
    environ = os.environ if environ is None else environ
    mode = (environ.get("OM_QUALIFICATION_MODE") or "").strip()
    if not mode:
        return None
    if mode not in QUALIFICATION_MODES:
        raise AdapterError(
            ErrorCode.ADAPTER_UNQUALIFIED,
            f"OM_QUALIFICATION_MODE={mode!r} is not one of "
            f"{list(QUALIFICATION_MODES)}")
    actor = (environ.get("OM_QUALIFICATION_ACTOR") or "").strip()
    if not actor:
        raise AdapterError(
            ErrorCode.ADAPTER_UNQUALIFIED,
            "OM_QUALIFICATION_MODE is set but OM_QUALIFICATION_ACTOR is not; "
            "an override that records no one is not an override")
    return {"mode": mode, "actor": actor,
            "reason": (environ.get("OM_QUALIFICATION_REASON") or "").strip()}


def require_qualified_execution(
        situation: Dict[str, Any],
        override: Optional[Dict[str, str]] = None,
        records: Optional[List[Dict[str, Any]]] = None,
        path: Optional[str] = None,
        audit: Optional[Any] = None) -> Dict[str, Any]:
    """The execution gate. Returns the evidence; raises to refuse.

    Returned evidence is written to the audit trail by the caller, and it names
    which record was relied on, or who overrode the absence of one.
    """
    record = find_record(situation, records=records, path=path)
    digest = situation_digest(situation)
    if record is not None:
        return {"qualificationStatus": QualificationStatus.HARDWARE_QUALIFIED,
                "situationDigest": digest,
                "recordId": record.get("recordId", digest),
                "qualifiedAt": record.get("qualifiedAt")}

    if override:
        evidence = {
            "qualificationStatus": QualificationStatus.UNQUALIFIED,
            "situationDigest": digest,
            "qualificationOverride": {
                "mode": override.get("mode"),
                "actor": override.get("actor"),
                "reason": override.get("reason", ""),
                # Named explicitly: the override was used *against the absence
                # of* a record for this situation, and the situation is what
                # makes that statement checkable later.
                "againstRecord": None,
                "situation": {field: situation.get(field)
                              for field in SITUATION_FIELDS},
                "at": round(time.time(), 3),
            },
        }
        if audit is not None and hasattr(audit, "emit_qualification_override"):
            audit.emit_qualification_override(evidence["qualificationOverride"])
        return evidence

    raise QualificationError(
        f"{situation.get('adapterId')}@{situation.get('adapterVersion')} has no "
        f"qualification record for this situation (digest {digest[:16]}...): "
        f"the checkpoint, the runtimes, the device pair and the boundary are "
        f"all part of what is qualified. Run the hardware gate with "
        f"OM_QUALIFICATION_MODE=qualify and OM_QUALIFICATION_ACTOR set, or "
        f"execute deliberately with OM_QUALIFICATION_MODE=override.",
        situation)


#: Gate names whose evidence establishes the full battery. A summary that does
#: not declare its scope and is not one of these grants execution only --
#: unknown evidence gets the least authority it could deserve, never the most.
FULL_GATE_NAMES = ("full-gate-qualification",)


def scope_from_evidence(evidence: Optional[Dict[str, Any]]) -> str:
    """How much this evidence proves. Derived, never asserted by a caller.

    Fail-safe: anything unrecognised is EXECUTION_QUALIFIED, the weaker of the
    two. A hand-written file claiming `failures: 0` can permit a run; it cannot
    promote itself to a release gate by saying so.
    """
    evidence = evidence or {}
    declared = evidence.get("qualificationScope")
    if declared == QualificationScope.FULL_GATE_QUALIFIED:
        # Declared, and only honoured when the evidence also names a gate that
        # actually runs the battery.
        if evidence.get("gate") in FULL_GATE_NAMES:
            return QualificationScope.FULL_GATE_QUALIFIED
        return QualificationScope.EXECUTION_QUALIFIED
    if evidence.get("gate") in FULL_GATE_NAMES:
        return QualificationScope.FULL_GATE_QUALIFIED
    return QualificationScope.EXECUTION_QUALIFIED


def record_scope(record: Optional[Dict[str, Any]]) -> str:
    """The scope a record holds. Absent means UNKNOWN, never full."""
    if not record:
        return QualificationScope.UNKNOWN
    return record.get("qualificationScope") or QualificationScope.UNKNOWN


def record_from_situation(situation: Dict[str, Any],
                          evidence: Optional[Dict[str, Any]] = None
                          ) -> Dict[str, Any]:
    """The record a passing gate writes. The gate run is its evidence.

    The scope is written into the record as a typed field, so a later caller
    can refuse on it without reading prose or guessing from a gate name.
    """
    return {
        "recordId": situation_digest(situation),
        "qualifiedAt": round(time.time(), 3),
        "qualificationScope": scope_from_evidence(evidence),
        "situation": {field: situation.get(field) for field in SITUATION_FIELDS},
        "evidence": evidence or {},
    }


def require_scope(situation: Dict[str, Any], required: str,
                  records: Optional[List[Dict[str, Any]]] = None,
                  path: Optional[str] = None) -> Dict[str, Any]:
    """Refuse unless a record for this situation holds at least `required`.

    This is the machine-enforced half of the distinction. Release sealing, an
    enforcement canary and paging work call it with FULL_GATE_QUALIFIED; a
    single-run record does not satisfy that and neither does a record written
    before the field existed.
    """
    record = find_record(situation, records=records, path=path)
    held = record_scope(record)
    if QualificationScope.permits(held, required):
        return {"qualificationScope": held,
                "recordId": (record or {}).get("recordId")}
    raise AdapterError(
        ErrorCode.ADAPTER_UNQUALIFIED,
        f"this situation holds {held} and {required} is required. "
        f"A single run establishes that the situation executes; it does not "
        f"establish performance, refusal paths, the qualification lifecycle or "
        f"the installed-wheel suite. Run the full gate and record its summary.",
        exit_code=65)


def append_record(record: Dict[str, Any], path: Optional[str] = None) -> str:
    """Merge one record into the ledger atomically, touching nothing else.

    Write-to-temporary-then-rename, the same shape `provision.write_ledger`
    uses: a reader either sees the ledger without this record or with it, never
    a half-written file. The provisioning and transport sections are read and
    written back untouched -- the adapter section is a tenant of this file, not
    its owner.
    """
    resolved = records_path(path)
    document: Dict[str, Any] = {}
    if os.path.isfile(resolved):
        try:
            with open(resolved, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                document = loaded
        except (OSError, ValueError):
            document = {}

    records = load_records(resolved)
    records = [existing for existing in records
               if existing.get("recordId") != record.get("recordId")]
    records.append(record)
    document[LEDGER_KEY] = {"schemaVersion": 1, "records": records}

    os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
    temporary = resolved + ".partial"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, resolved)
    return resolved


__all__ = [
    "DEFAULT_RECORDS_PATH", "LEDGER_KEY", "QUALIFICATION_MODES",
    "SITUATION_FIELDS", "QualificationError", "append_record", "build_identity",
    "build_situation", "find_record", "load_records",
    "override_from_environment", "qualification_status", "record_from_situation",
    "records_path", "require_qualified_execution", "situation_digest",
    "situation_from_manifest", "topology_from_manifest",
]
