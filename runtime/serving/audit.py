"""The single place worker events are constructed.

Every event a worker emits goes through `EventWriter`, so the audit tuple cannot
be forgotten on one code path and present on another. A field that is required
on every validated event is required *here*, once, rather than at each of the
seventeen call sites that used to build records by hand.

Two record shapes exist and they are deliberately not interchangeable:

* **validated** -- emitted only after the placement manifest has been verified.
  Carries the full audit tuple, including identity taken from the verified
  manifest.
* **pre-validation failure** -- emitted when a worker dies before it has a
  verified manifest. It carries no `placementId`, `manifestDigest` or
  `modelFingerprint`, because echoing those from an unverified file would
  launder untrusted input into the audit trail as though it were evidence.

See docs/INTELLIGENCE_V0_CONTRACT.md, which this implements.
"""

from __future__ import annotations

import json
import os
import struct
import time
import uuid
from hashlib import sha256
from typing import Any, Dict, List, Optional, Sequence

EVENT_SCHEMA_VERSION = 1

#: Rotate past this size, keeping this many older segments. A segment holding a
#: failure is kept regardless of the retention count.
MAX_EVENT_BYTES = int(os.environ.get("OPENMYCELIUM_EVENT_BYTES",
                                     str(16 * 1024 * 1024)))
EVENT_RETENTION = int(os.environ.get("OPENMYCELIUM_EVENT_RETENTION", "5"))

#: The fields every validated event must carry. Enforced on write, and again by
#: the coordinator on read, because a producer-side check alone would not catch
#: an event written by something other than this module.
MINIMAL_TUPLE = (
    "eventSchemaVersion", "runId", "placementId", "manifestDigest",
    "modelFingerprint", "workerRole", "deviceIdentity", "stageOrientation",
    "boundaryAfterLayer", "eventSequence", "wallTimeUtc", "monotonicNs",
    "bootId", "writerId",
)

PRE_VALIDATION_FIELDS = (
    "eventSchemaVersion", "runId", "workerRole", "placementPath",
    "placementValidation", "failurePhase", "failureReason",
    "wallTimeUtc", "monotonicNs", "bootId", "writerId",
)

FAILURE_PHASES = ("read", "schema", "digest", "fingerprint", "ownership",
                  "role", "startup")

TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json",
                   "special_tokens_map.json")


class AuditError(RuntimeError):
    """An event could not be constructed as the contract requires."""


# ------------------------------------------------------------------ identity

def new_run_id() -> str:
    """One coordinator execution attempt.

    Correlation metadata, not authentication: it says which attempt an event
    belongs to and nothing about who produced it. Independent of `placementId`
    and excluded from the manifest digest, so re-running a placement produces a
    new `runId` while the placement digest is unchanged.
    """
    return str(uuid.uuid4()).lower()


def valid_run_id(value: Any) -> bool:
    """Whether a value is a well-formed UUID.

    Case-insensitive on input and normalised to lower case on storage. A UUID
    is the same identifier in either case, so refusing an upper-case spelling
    would fail a correct run for a cosmetic reason; normalising means the
    comparison is still exact.
    """
    try:
        return str(uuid.UUID(str(value))).lower() == str(value).strip().lower()
    except (ValueError, AttributeError, TypeError):
        return False


def boot_id() -> str:
    """Identifies this kernel boot, so monotonic clocks are comparable.

    CLOCK_MONOTONIC restarts near zero on every WSL VM boot -- measured at
    2.929 s on a VM whose uptime was 2.91 s -- so two events from different
    boots can carry the same `monotonicNs`. Durations may only be computed
    between samples sharing a boot id.
    """
    try:
        with open("/proc/sys/kernel/random/boot_id", "r", encoding="utf-8") as h:
            return h.read().strip()
    except OSError:
        return "unknown"


def monotonic_ns() -> int:
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


# ------------------------------------------------------------- canonical hashes

def prompt_ids_hash(token_ids: Sequence[int]) -> str:
    """SHA-256 over unsigned 32-bit little-endian ids, in sequence order.

    No separators, no length prefix, no padding -- specified exactly so two
    implementations agree. An id outside the 32-bit range is a fault rather
    than something to wrap around silently.
    """
    blob = bytearray()
    for value in token_ids:
        number = int(value)
        if not 0 <= number < (1 << 32):
            raise AuditError(f"token id {number} is outside the 32-bit range")
        blob += struct.pack("<I", number)
    return sha256(bytes(blob)).hexdigest()


def tokenizer_identity(model_path: str, fix_mistral_regex: bool) -> str:
    """Digest of the tokenizer files, in a fixed order, plus the regex flag.

    Name and length are hashed alongside content so that moving bytes between
    files cannot collide, and a missing file is distinguishable from an empty
    one. The flag is included because it changes the ids produced for identical
    text -- this project has already shipped a run where it did.
    """
    parts: List[bytes] = []
    for name in TOKENIZER_FILES:
        parts.append(name.encode("utf-8"))
        path = os.path.join(model_path, name)
        try:
            with open(path, "rb") as handle:
                blob = handle.read()
        except OSError:
            blob = b""
        parts.append(struct.pack("<Q", len(blob)))
        parts.append(blob)
    parts.append(b"fix_mistral_regex=" + (b"1" if fix_mistral_regex else b"0"))
    return sha256(b"".join(parts)).hexdigest()


# -------------------------------------------------------------------- writer

class EventWriter:
    """Constructs and writes every event a worker emits."""

    def __init__(self, stream, run_id: str, worker_role: str):
        if not valid_run_id(run_id):
            raise AuditError(f"run id {run_id!r} is not a canonical UUID")
        self.stream = stream
        self.run_id = str(run_id).lower()
        self.worker_role = worker_role
        self.boot_id = boot_id()
        #: Distinguishes two processes that claim the same role within one run
        #: and boot. Without it, a second worker emitting a non-conflicting
        #: sequence is indistinguishable from the legitimate one, and the
        #: contract's duplicate-role rule cannot actually be enforced.
        self.writer_id = str(uuid.uuid4())
        self._had_failure = False
        self._sequence = 0
        self._identity: Optional[Dict[str, Any]] = None

    # -- identity becomes available only after the manifest is verified ----
    def bind_placement(self, placement: Dict[str, Any], stage: Any) -> None:
        """Adopt the verified manifest's identity. Callable once."""
        if self._identity is not None:
            raise AuditError("placement identity is already bound")
        pipeline = placement["pipeline"]
        self._identity = {
            "placementId": placement["placementId"],
            "manifestDigest": placement["manifestDigest"],
            "modelFingerprint": placement["model"]["fingerprint"],
            "deviceIdentity": stage.device_identity,
            "stageOrientation": pipeline.get("stageOrientation", "cuda-first"),
            "boundaryAfterLayer": int(pipeline["boundaryAfterLayer"]),
        }

    @property
    def bound(self) -> bool:
        return self._identity is not None

    def _next_sequence(self) -> int:
        # Scoped to (runId, workerRole): two separate processes cannot share a
        # counter, and both would otherwise emit sequence 1.
        self._sequence += 1
        return self._sequence

    def _write(self, record: Dict[str, Any]) -> Dict[str, Any]:
        self.stream.write(json.dumps(record) + "\n")
        self.stream.flush()
        self._rotate_if_large()
        return record

    def _rotate_if_large(self) -> None:
        """Keep the event file bounded without discarding failure evidence.

        One short run wrote 152 events; a long-lived server would grow this
        without limit. Past `MAX_EVENT_BYTES` the file is rotated and
        `EVENT_RETENTION` older segments are kept.

        A segment containing a failure is renamed with a `.failed` suffix and
        is exempt from the retention count. The whole purpose of the trail is
        to explain a failure afterwards, so rotating that away while keeping
        the segments from runs that went fine would discard exactly the
        evidence worth having.
        """
        path = getattr(self.stream, "name", "")
        if not path or not os.path.isfile(path):
            return
        try:
            if os.path.getsize(path) < MAX_EVENT_BYTES:
                return
        except OSError:
            return
        suffix = ".failed" if getattr(self, "_had_failure", False) else ""
        stamp = time.strftime("%Y%m%dT%H%M%S")
        try:
            self.stream.flush()
            self.stream.close()
            os.rename(path, f"{path}.{stamp}{suffix}")
            self.stream = open(path, "a", encoding="utf-8")
        except OSError:
            return
        self._had_failure = False
        self._prune(path)

    @staticmethod
    def _prune(path: str) -> None:
        directory = os.path.dirname(path) or "."
        stem = os.path.basename(path)
        try:
            segments = sorted(name for name in os.listdir(directory)
                              if name.startswith(stem + ".")
                              and not name.endswith(".failed"))
        except OSError:
            return
        if len(segments) <= EVENT_RETENTION:
            return
        for name in segments[:-EVENT_RETENTION]:
            try:
                os.remove(os.path.join(directory, name))
            except OSError:
                pass

    def emit(self, event: str, **fields: Any) -> Dict[str, Any]:
        """A validated event. Refuses to write before the manifest is bound."""
        if self._identity is None:
            raise AuditError(
                f"event {event!r} was emitted before the placement manifest was "
                "verified; use emit_pre_validation_failure instead")
        record = {
            "eventSchemaVersion": EVENT_SCHEMA_VERSION,
            "runId": self.run_id,
            "workerRole": self.worker_role,
            "eventSequence": self._next_sequence(),
            "wallTimeUtc": round(time.time(), 3),
            "monotonicNs": monotonic_ns(),
            "bootId": self.boot_id,
            "writerId": self.writer_id,
            "event": event,
            "worker": self.worker_role,        # retained for existing readers
        }
        record.update(self._identity)
        record.update(fields)
        missing = [name for name in MINIMAL_TUPLE if name not in record]
        if missing:
            raise AuditError(f"event {event!r} is missing {missing}")
        if event in ("failed", "request_failed"):
            # Marks this segment as evidence, so rotation preserves it.
            self._had_failure = True
        return self._write(record)

    def emit_placement_summary(self, placement: Dict[str, Any], stage: Any,
                               manifest_path: str, runtime_version: str = "",
                               **fields: Any) -> Dict[str, Any]:
        """The full placement summary: a pointer to evidence, not the evidence.

        The complete object is the manifest. This carries its path together
        with the digest, so the pointer is bound to specific content.
        """
        pipeline = placement["pipeline"]
        boundary = int(pipeline["boundaryAfterLayer"])
        layer_count = int(placement["model"].get(
            "layerCount", boundary + 1 + len(stage.layers)))
        summary = {
            "manifestPath": os.path.abspath(manifest_path),
            "cudaLayerStart": 0,
            "cudaLayerEnd": boundary,
            "cudaLayerCount": boundary + 1,
            "rocmLayerStart": boundary + 1,
            "rocmLayerEnd": layer_count - 1,
            "rocmLayerCount": layer_count - (boundary + 1),
            "modelLayerCount": layer_count,
            "assignedTensorCount": len(stage.tensors),
            "assignedWeightBytes": int(getattr(stage, "weight_bytes", 0)),
            "budgetBytes": int(getattr(stage, "budget_bytes", 0)),
            "runtime": stage.runtime,
            "runtimeVersion": runtime_version,
            "transport": pipeline.get("transport", ""),
            "fabricSnapshotAt": placement.get("fabric", {}).get("probedAt"),
        }
        summary.update(fields)
        return self.emit("placement_validated", **summary)

    def emit_pre_validation_failure(self, placement_path: str, phase: str,
                                    reason: str) -> Dict[str, Any]:
        """A failure before any manifest was verified.

        Carries no placement identity. A worker that could not verify the
        manifest has no verified identity to report, and repeating one from an
        unverified file would present untrusted input as evidence.
        """
        if phase not in FAILURE_PHASES:
            phase = "startup"
        record = {
            "eventSchemaVersion": EVENT_SCHEMA_VERSION,
            "runId": self.run_id,
            "workerRole": self.worker_role,
            "worker": self.worker_role,
            "event": "placement_rejected",
            "eventSequence": self._next_sequence(),
            "placementPath": str(placement_path),
            "placementValidation": "failed",
            "failurePhase": phase,
            "failureReason": str(reason)[:400],
            "wallTimeUtc": round(time.time(), 3),
            "monotonicNs": monotonic_ns(),
            "bootId": self.boot_id,
            "writerId": self.writer_id,
        }
        self._had_failure = True
        leaked = [n for n in ("placementId", "manifestDigest",
                              "modelFingerprint") if n in record]
        if leaked:
            raise AuditError(f"pre-validation event must not carry {leaked}")
        return self._write(record)

    def close(self) -> None:
        try:
            self.stream.close()
        except (OSError, ValueError):
            pass
