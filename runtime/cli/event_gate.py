"""Coordinator-side admission of worker events.

Every event is checked here *before* it reaches the health state machine. An
event that fails admission never influences state, because the health model is
what the operator acts on and a forged or stale record reaching it would make
the runtime's own account of itself wrong.

The producer-side writer already refuses to build a malformed event. This is a
second, independent check on the reader side, for the same reason a protocol
validates what it receives rather than trusting the sender to be correct: events
arrive from a file that any process could have appended to.

Implements the coordinator enforcement section of
docs/INTELLIGENCE_V0_CONTRACT.md.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.join(_HERE, "..", "serving") not in sys.path:
    sys.path.insert(0, os.path.join(_HERE, "..", "serving"))

from audit import (EVENT_SCHEMA_VERSION, MINIMAL_TUPLE,  # noqa: E402
                   PRE_VALIDATION_FIELDS, valid_run_id)

#: Identity fields that must agree between the two workers.
SHARED_IDENTITY = ("placementId", "manifestDigest", "modelFingerprint")


@dataclass
class Rejection:
    reason: str
    event: Dict[str, Any]

    def __str__(self) -> str:
        worker = self.event.get("workerRole") or self.event.get("worker", "?")
        name = self.event.get("event", "?")
        return f"{worker}/{name}: {self.reason}"


@dataclass
class EventGate:
    """Admits worker events, or refuses them with a reason."""

    run_id: str
    expected: Dict[str, Any] = field(default_factory=dict)
    boot_id: Optional[str] = None
    sequences: Dict[str, int] = field(default_factory=dict)
    roles_seen: Dict[str, str] = field(default_factory=dict)
    rejections: List[Rejection] = field(default_factory=list)
    admitted: int = 0

    def expect_placement(self, placement: Dict[str, Any]) -> None:
        """Bind the identity workers must agree with, from the issued manifest."""
        self.expected = {
            "placementId": placement["placementId"],
            "manifestDigest": placement["manifestDigest"],
            "modelFingerprint": placement["model"]["fingerprint"],
            "boundaryAfterLayer": int(
                placement["pipeline"]["boundaryAfterLayer"]),
            "stageOrientation": placement["pipeline"].get(
                "stageOrientation", "cuda-first"),
        }
        # The device identities the manifest was compiled against. Checked
        # against the manifest's own snapshot, never against a live probe: the
        # world may legitimately have moved since planning, and failing a run
        # for that would reject correct work.
        self.expected["deviceIdentities"] = {
            stage["role"]: stage["deviceIdentity"]
            for stage in placement["stages"]}

    # ------------------------------------------------------------- admission
    def admit(self, event: Dict[str, Any]) -> Optional[Rejection]:
        """Return None if the event may reach the health model, else why not."""
        rejection = self._check(event)
        if rejection is not None:
            self.rejections.append(rejection)
            return rejection
        self.admitted += 1
        return None

    def _reject(self, event: Dict[str, Any], reason: str) -> Rejection:
        return Rejection(reason, event)

    def _check(self, event: Dict[str, Any]) -> Optional[Rejection]:
        if not isinstance(event, dict):
            return self._reject({}, "event is not an object")

        schema = event.get("eventSchemaVersion")
        if schema != EVENT_SCHEMA_VERSION:
            return self._reject(event,
                                f"event schema {schema!r}, expected "
                                f"{EVENT_SCHEMA_VERSION}")

        run_id = event.get("runId")
        if not valid_run_id(run_id):
            return self._reject(event, f"run id {run_id!r} is malformed")
        if str(run_id).lower() != self.run_id:
            # Foreign or stale: a previous attempt's events may still be in the
            # file, and they describe a different execution.
            return self._reject(event, f"run id {run_id} is not this run")

        role = event.get("workerRole")
        if role not in ("cuda", "rocm"):
            return self._reject(event, f"worker role {role!r} is not recognised")

        boot = event.get("bootId")
        if not boot:
            return self._reject(event, "bootId is missing")
        if self.boot_id is None:
            self.boot_id = boot
        elif boot != self.boot_id:
            # CLOCK_MONOTONIC restarts at zero on a new boot, so timestamps
            # either side of this are not comparable and must not be mixed.
            return self._reject(event,
                                f"bootId changed within the run "
                                f"({self.boot_id} -> {boot})")

        if event.get("placementValidation") == "failed":
            return self._check_pre_validation(event, role)
        return self._check_validated(event, role)

    def _check_pre_validation(self, event: Dict[str, Any], role: str
                              ) -> Optional[Rejection]:
        missing = [n for n in PRE_VALIDATION_FIELDS if n not in event]
        if missing:
            return self._reject(event,
                                f"pre-validation failure is missing {missing}")
        leaked = [n for n in SHARED_IDENTITY if n in event]
        if leaked:
            # It could not have verified these. Reporting them would present
            # untrusted file contents as though they were established.
            return self._reject(event,
                                f"pre-validation failure claims verified "
                                f"identity {leaked}")
        return None

    def _check_validated(self, event: Dict[str, Any], role: str
                         ) -> Optional[Rejection]:
        missing = [n for n in MINIMAL_TUPLE if n not in event]
        if missing:
            return self._reject(event, f"missing audit fields {missing}")

        for name in SHARED_IDENTITY:
            expected = self.expected.get(name)
            if expected is not None and event.get(name) != expected:
                return self._reject(
                    event, f"{name} {event.get(name)!r} disagrees with the "
                           f"issued placement {expected!r}")

        if self.expected:
            if event.get("boundaryAfterLayer") != \
                    self.expected["boundaryAfterLayer"]:
                return self._reject(event, "boundaryAfterLayer disagrees with "
                                           "the issued placement")
            if event.get("stageOrientation") != \
                    self.expected["stageOrientation"]:
                return self._reject(event, "stageOrientation disagrees with "
                                           "the issued placement")
            expected_device = self.expected["deviceIdentities"].get(role)
            if expected_device and event.get("deviceIdentity") != expected_device:
                return self._reject(
                    event, f"deviceIdentity {event.get('deviceIdentity')!r} is "
                           f"not the manifest's device for role {role}")

        # One process per role, identified by the writer's own nonce. Keying
        # this on run and boot alone would not work: a duplicate worker shares
        # both with the legitimate one, so only a per-process id separates them.
        writer_id = event.get("writerId")
        if not writer_id:
            return self._reject(event, "writerId is missing")
        seen = self.roles_seen.get(role)
        if seen is None:
            self.roles_seen[role] = writer_id
        elif seen != writer_id:
            return self._reject(event, f"a second worker claims role {role}")

        sequence = event.get("eventSequence")
        if not isinstance(sequence, int) or sequence < 1:
            return self._reject(event, f"eventSequence {sequence!r} is invalid")
        last = self.sequences.get(role, 0)
        if sequence <= last:
            return self._reject(
                event, f"eventSequence {sequence} repeats or regresses "
                       f"after {last} for {role}")
        self.sequences[role] = sequence
        return None

    # ---------------------------------------------------------------- report
    def summary(self) -> Dict[str, Any]:
        return {
            "runId": self.run_id,
            "bootId": self.boot_id,
            "admitted": self.admitted,
            "rejected": len(self.rejections),
            "rejections": [str(r) for r in self.rejections[:20]],
            "lastSequence": dict(self.sequences),
            "rolesSeen": sorted(self.roles_seen),
        }
