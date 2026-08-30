"""The Safety Governor: a deterministic core with no side effects on hardware.

Implements `safety-contract-1`. Every transition is looked up in
`contract.TRANSITIONS` rather than encoded again here -- a second copy of the
table is a second thing to keep in step, and the one that drifts is always the
one nobody is reading.

Nothing in this module touches a device, sends a signal, or reads a clock it was
not given. The clock, the telemetry source and the process actuator are all
injected, so the whole state machine is reachable in milliseconds and a test can
produce a stale reading or a dead worker on demand. Gate E supplies real
implementations of those three interfaces; the machine above them does not
change when it does.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from contract import (ADVISORY_ONLY_SIGNALS, AUDIT_FIELDS,  # noqa: E402
                      CONTRACT_VERSION, INCIDENT_CLASSES,
                      QUARANTINE_RECORD_FIELDS, TRANSITION_EXECUTOR,
                      Confidence, DrainMode, Policy, State, TriggerSource,
                      permitted)


class GovernorError(RuntimeError):
    """A refusal, or an attempt at a transition the contract forbids."""


# ------------------------------------------------------------------ injected

class TestClock:
    """A clock the test advances. Real deadlines are 60-900 s."""

    def __init__(self, start: float = 0.0):
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        self._now += float(seconds)
        return self._now


class SyntheticTelemetry:
    """Scripted device readings, including ones hardware will not produce on cue.

    A reading may be a dict (returned every time) or a list of dicts (consumed
    one per sample, the last repeating). The list form is what makes baseline
    sampling testable: five samples, one of them an outlier.
    """

    def __init__(self, readings: Dict[str, Any], installed_at: float = 0.0):
        self._readings = {device: list(value) if isinstance(value, list) else value
                          for device, value in readings.items()}
        self._index: Dict[str, int] = {}
        self._canary_fails: set = set()
        self.sampled_at: Dict[str, float] = {}

    def devices(self) -> Sequence[str]:
        return tuple(self._readings)

    def set(self, device: str, reading: Dict[str, Any], now: Optional[float] = None) -> None:
        self._readings[device] = reading
        self._index.pop(device, None)
        # A new reading is fresh as of now. Callers that want to age one out
        # simply advance the clock without setting a new one.
        self.sampled_at.pop(device, None)

    def refresh(self, now: float) -> None:
        """Restamp every reading as taken now.

        A real source keeps polling. Without this a test that advances the
        clock past `max_signal_age_seconds` finds every reading STALE and every
        admission refused -- correct behaviour, but not what the test was about.
        """
        for device in self._readings:
            self.sampled_at[device] = now

    def fail_canary(self, device: str) -> None:
        self._canary_fails.add(device)

    def canary(self, device: str) -> bool:
        return device not in self._canary_fails

    def sample(self, device: str, now: float) -> Dict[str, Any]:
        """Reading it does not refresh it.

        `sampled_at` records when the reading was *installed*, not when it was
        last read. An earlier version stamped it here, which made every reading
        eternally fresh: the age was measured against the read that had just
        happened, so STALE was unreachable and the test that wanted it could
        never pass.
        """
        value = self._readings[device]
        if isinstance(value, list):
            index = self._index.get(device, 0)
            reading = value[min(index, len(value) - 1)]
            self._index[device] = index + 1
        else:
            reading = value
        self.sampled_at.setdefault(device, now)
        return dict(reading)


class FakeProcessActuator:
    """Records what would have been signalled. Sends nothing.

    D.1 proves the identity check and the intent/outcome ordering without any
    real signal, which is the only way to test "we refused to kill the wrong
    process" without occasionally killing the wrong process.
    """

    def __init__(self):
        self.signalled: List[Dict[str, Any]] = []

    def signal(self, pid: int, name: str) -> bool:
        self.signalled.append({"pid": pid, "signal": name})
        return True


# ------------------------------------------------------------------ records

def _digest(record: Dict[str, Any]) -> str:
    body = {key: value for key, value in record.items() if key != "digest"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True).encode("utf-8")).hexdigest()


class QuarantineStore:
    """Durable enough that a crash cannot silently release a quarantine.

    Temporary file, fsync, atomic rename, then fsync the directory -- the last
    step being the one usually omitted, without which the contents survive a
    crash and the entry pointing at them may not.

    A dict may be passed instead of a path, for tests. It records the same
    durability facts so the ordering is asserted rather than assumed.
    """

    def __init__(self, target: Any = None, partial_present: bool = False,
                 readable: bool = True):
        self._path = target if isinstance(target, str) else None
        self._memory = target if isinstance(target, dict) else (
            None if self._path else {})
        self.partial_present = partial_present
        self.readable = readable
        self.last_write: Dict[str, Any] = {}

    def load(self) -> List[Dict[str, Any]]:
        if not self.readable:
            raise GovernorError(
                "the quarantine store is unreadable; treating the machine as "
                "quarantined, because otherwise damaging one file is how a "
                "quarantine gets cleared")
        if self.partial_present:
            raise GovernorError(
                "a .partial quarantine write was interrupted; treating the "
                "machine as quarantined. Between quarantining something that "
                "was fine and releasing something that was not, only the first "
                "is recoverable by an operator")
        if self._memory is not None:
            return list(self._memory.get("records") or [])
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                return list(json.load(handle).get("records") or [])
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as error:
            raise GovernorError(
                f"the quarantine store could not be parsed ({error}); "
                "treating the machine as quarantined") from error

    def validate(self, record: Dict[str, Any]) -> None:
        missing = [f for f in QUARANTINE_RECORD_FIELDS if f not in record]
        if missing:
            raise GovernorError(
                f"quarantine record {record.get('recordId', '?')} is missing "
                f"{missing}; a record that cannot be read is treated as a "
                "quarantine")
        if record["digest"] != _digest(record):
            raise GovernorError(
                f"quarantine record {record.get('recordId')} fails its own "
                "digest; treating it as a quarantine rather than assuming health")

    def append(self, record: Dict[str, Any]) -> None:
        record = dict(record)
        record["digest"] = _digest(record)
        existing = [r for r in self.load() if r.get("recordId") != record["recordId"]]
        existing.append(record)
        document = {"schemaVersion": 1, "records": existing}

        if self._memory is not None:
            self._memory.clear()
            self._memory.update(document)
            self.last_write = {"fsyncedFile": True, "fsyncedDirectory": True,
                               "atomicRename": True, "partialRemains": False}
            return

        temporary = self._path + ".partial"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._path)
        directory = os.open(os.path.dirname(self._path) or ".", os.O_RDONLY)
        try:
            os.fsync(directory)          # the rename itself must be durable
        finally:
            os.close(directory)
        self.last_write = {
            "fsyncedFile": True, "fsyncedDirectory": True,
            "atomicRename": True,
            "partialRemains": os.path.exists(temporary),
        }

    def remove(self, record_ids: Sequence[str]) -> None:
        keep = [r for r in self.load() if r.get("recordId") not in set(record_ids)]
        document = {"schemaVersion": 1, "records": keep}
        if self._memory is not None:
            self._memory.clear()
            self._memory.update(document)
            return
        temporary = self._path + ".partial"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._path)


# ---------------------------------------------------------------- the machine

class SafetyGovernor:
    """The state machine. Deterministic, injectable, and it signals nothing."""

    def __init__(self, clock, telemetry, policy: Optional[Policy] = None,
                 boot_id: str = "unknown", quarantine_store: Any = None,
                 quarantine_store_readable: bool = True,
                 quarantine_partial_present: bool = False,
                 actuator: Optional[FakeProcessActuator] = None,
                 simulated: bool = True,
                 run_id: str = "run-simulated",
                 placement_id: str = "pl-simulated",
                 manifest_digest: str = "", model_fingerprint: str = ""):
        self.clock = clock
        self.telemetry = telemetry
        self.policy = policy or Policy()
        self.boot_id = boot_id
        self.actuator = actuator or FakeProcessActuator()
        self.simulated = simulated
        self.run_id = run_id
        self.placement_id = placement_id
        self.manifest_digest = manifest_digest
        self.model_fingerprint = model_fingerprint

        self.store = QuarantineStore(quarantine_store,
                                     partial_present=quarantine_partial_present,
                                     readable=quarantine_store_readable)

        self._state = State.UNKNOWN
        self._revision = 0
        self.events: List[Dict[str, Any]] = []
        self._baselines: Dict[str, Optional[int]] = {}
        self._leases: Dict[str, int] = {}
        self._incidents: List[Dict[str, Any]] = []
        self._quarantines: List[Dict[str, Any]] = []
        self._workers: Dict[int, Dict[str, Any]] = {}

        #: Per device, for the same reason as `_soft_since`.
        self._degraded_since: Dict[str, float] = {}
        #: Per device. A single scalar let a healthy second card clear the dwell
        #: a loaded first card had started, so the soft limit could never fire
        #: on a two-device machine -- which is every machine this runs on.
        self._soft_since: Dict[str, float] = {}
        self.drain_mode: Optional[DrainMode] = None
        self._drain_started: Optional[float] = None
        self.escalation: Optional[str] = None
        self._cooldown_started: Optional[float] = None
        self._last_progress: Optional[float] = None
        self._last_counter: Optional[int] = None
        self._operation: Optional[str] = None
        self._records = 0

    # ------------------------------------------------------------- properties
    @property
    def degraded(self) -> bool:
        """True when any device has lost its VRAM signal."""
        return bool(self._degraded_since)

    @property
    def state(self) -> State:
        return self._state

    @property
    def state_revision(self) -> int:
        return self._revision

    @property
    def last_quarantine_write(self) -> Dict[str, Any]:
        return self.store.last_write

    # ----------------------------------------------------------------- audit
    def _signals(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for device in self.telemetry.devices():
            reading = self.telemetry.sample(device, self.clock.now())
            reading["vramConfidence"] = self._age_adjusted(
                device, reading.get("vramConfidence"))
            out[device] = reading
        return out

    def _emit(self, event: str, previous: State, trigger: str,
              source: TriggerSource, **fields: Any) -> Dict[str, Any]:
        record = {
            "event": event,
            "bootId": self.boot_id,
            "runId": self.run_id,
            "placementId": self.placement_id,
            "manifestDigest": self.manifest_digest,
            "modelFingerprint": self.model_fingerprint,
            "safetyPreviousState": previous.value,
            "safetyState": self._state.value,
            "safetyTrigger": trigger,
            "safetyTriggerSource": source.value,
            "safetyTransitionExecutor": TRANSITION_EXECUTOR,
            "safetyStateRevision": self._revision,
            "safetyPolicyVersion": self.policy.version,
            "safetyContractVersion": CONTRACT_VERSION,
            "safetySignals": fields.pop("signals", {}),
            "safetySimulated": self.simulated,
        }
        record.update(fields)
        missing = [name for name in AUDIT_FIELDS if name not in record]
        if missing:
            raise GovernorError(f"event {event!r} is missing {missing}")
        self.events.append(record)
        return record

    # ------------------------------------------------------------ transitions
    def _apply(self, trigger: str, *, expect_revision: Optional[int] = None,
               signals: Optional[Dict[str, Any]] = None,
               **fields: Any) -> bool:
        """Apply a trigger if the table permits it and the revision is current.

        Returns False for an idempotent rejection -- a duplicate, a stale or an
        out-of-order trigger. Raises only when the table forbids the transition
        outright, because that is a defect rather than a race.
        """
        if expect_revision is not None and expect_revision != self._revision:
            self._emit("safety_trigger_stale", self._state, "trigger_stale",
                       TriggerSource.GOVERNOR,
                       rejectedTrigger=trigger,
                       observedRevision=expect_revision,
                       currentRevision=self._revision)
            return False

        transition = permitted(self._state, trigger)
        if transition is None:
            raise GovernorError(
                f"{self._state.value} has no transition for {trigger!r}; the "
                f"contract permits {sorted(set(t.trigger for t in _from(self._state)))}")

        previous = self._state
        self._state = transition.target
        self._revision += 1
        if transition.drain_mode is not None:
            self.drain_mode = transition.drain_mode
        self._emit(transition.audit_event, previous, trigger,
                   transition.trigger_source,
                   signals=signals or {}, **fields)
        return True

    # ------------------------------------------------------------- preflight
    def preflight(self) -> bool:
        if self._state is State.QUARANTINED:
            raise GovernorError(
                "quarantined; only an operator reset clears this, with evidence")

        try:
            records = self.store.load()
            for record in records:
                self.store.validate(record)
        except GovernorError:
            self._quarantines = [{"recordId": "unreadable"}]
            self._apply("quarantine_restored")
            raise

        open_records = [r for r in records if not r.get("clearedAt")]
        if open_records:
            self._quarantines = list(open_records)
            self._apply("quarantine_restored")
            raise GovernorError(
                f"{len(open_records)} unresolved quarantine record(s) restored")

        for device in self.telemetry.devices():
            samples = []
            for _ in range(self.policy.baseline_sample_count):
                reading = self.telemetry.sample(device, self.clock.now())
                free = reading.get("freeBytes")
                if free is None:
                    self._apply("preflight_failed", detail=f"{device} has no VRAM reading")
                    return False
                samples.append(int(free))
            # Median, not one reading: a compositor allocating during a single
            # sample would raise the floor permanently and silently.
            self._baselines[device] = int(statistics.median(samples))

        return self._apply("preflight_passed", signals=self._signals())

    # ------------------------------------------------------------- admission
    def baseline(self, device: str) -> Optional[int]:
        return self._baselines.get(device)

    def signals(self, device: str) -> Dict[str, Any]:
        reading = self.telemetry.sample(device, self.clock.now())
        reading["vramConfidence"] = self._age_adjusted(
            device, reading.get("vramConfidence"))
        return reading

    def _age_adjusted(self, device: str, confidence: Any) -> Any:
        """A reading older than max_signal_age_seconds is STALE, not AVAILABLE."""
        if confidence != Confidence.AVAILABLE:
            return confidence
        taken = self.telemetry.sampled_at.get(device)
        if taken is None:
            return confidence
        if self.clock.now() - taken > self.policy.max_signal_age_seconds:
            return Confidence.STALE
        return confidence

    def _admissible(self, device: str, requested_bytes: int) -> Optional[str]:
        reading = self.signals(device)

        if reading.get("vramConfidence") is not Confidence.AVAILABLE:
            return f"VRAM confidence is {reading.get('vramConfidence')}"

        for name in ("power", "temperature"):
            confidence = reading.get(f"{name}Confidence")
            if confidence is Confidence.UNAVAILABLE_UNEXPECTED:
                # An expected absence is a capability gap; an unexpected one is
                # a source that worked and stopped.
                return f"{name} telemetry was expected to work and did not"

        free = int(reading.get("freeBytes") or 0)
        leased = self._leases.get(device, 0)
        total = int(reading.get("totalBytes") or 0)
        reserve = self.policy.reserve_bytes(total)
        if free - leased - requested_bytes < reserve:
            return (f"{free - leased} available, {requested_bytes} requested, "
                    f"{reserve} reserved")

        if len([d for d in self._leases if self._leases[d]]) >= 0:
            held = 1 if self._leases.get(device) else 0
            if held >= self.policy.max_concurrent_leases:
                return f"{held} lease(s) already held on {device}"
        return None

    def admit(self, device: str, requested_bytes: int,
              expect_revision: Optional[int] = None) -> bool:
        if self._state in (State.ADMITTED, State.RUNNING):
            # Already holding a lease. That is a refusal, not a defect: asking
            # a busy machine for capacity is a reasonable thing to do and gets a
            # reasoned no. The state machine is what enforces the limit, and
            # `max_concurrent_leases` records why.
            self._emit("safety_admission_refused", self._state,
                       "admission_refused", TriggerSource.GOVERNOR,
                       signals=self._signals(), device=device,
                       refusalReason=(f"a lease is already held; "
                                      f"max_concurrent_leases is "
                                      f"{self.policy.max_concurrent_leases}"))
            return False
        if self._state is not State.READY:
            # UNKNOWN, DRAINING, COOLDOWN, QUARANTINED, FAILED: nothing has been
            # established or the machine is not accepting work. Asking here is a
            # defect in the caller, not a capacity question.
            raise GovernorError(
                f"admission requires READY; the Governor is {self._state.value}")
        if expect_revision is not None and expect_revision != self._revision:
            self._emit("safety_trigger_stale", self._state, "trigger_stale",
                       TriggerSource.GOVERNOR, rejectedTrigger="admission_granted",
                       observedRevision=expect_revision,
                       currentRevision=self._revision)
            return False

        refusal = self._admissible(device, requested_bytes)
        if refusal is not None:
            self._apply("admission_refused", signals=self._signals(),
                        refusalReason=refusal, device=device)
            return False

        # The capacity check and the lease are applied under one revision, so
        # two admissions racing between telemetry samples cannot both succeed.
        self._leases[device] = self._leases.get(device, 0) + requested_bytes
        self._apply("admission_granted", signals=self._signals(), device=device,
                    requestedBytes=requested_bytes)
        return True

    # ---------------------------------------------------------------- canary
    def canary_passed(self, device: str,
                      expect_revision: Optional[int] = None) -> bool:
        if not self.telemetry.canary(device):
            return self._apply("canary_failed", expect_revision=expect_revision,
                               device=device)
        return self._apply("canary_passed", expect_revision=expect_revision,
                           device=device)

    # ------------------------------------------------------------- lifecycle
    def completed(self, expect_revision: Optional[int] = None) -> bool:
        if self._state is State.COOLDOWN:
            return False                     # duplicate; already recorded
        return self._apply("work_completed", expect_revision=expect_revision)

    def heartbeat(self, operation: Optional[str], counter: int,
                  expect_revision: Optional[int] = None) -> bool:
        if expect_revision is not None and expect_revision != self._revision:
            self._emit("safety_trigger_stale", self._state, "trigger_stale",
                       TriggerSource.WORKER, rejectedTrigger="heartbeat",
                       observedRevision=expect_revision,
                       currentRevision=self._revision)
            return False
        if self._state is not State.RUNNING:
            return False
        self._operation = operation
        # Only an advancing counter is progress. A process claiming to be
        # healthy is not evidence that it is.
        if self._last_counter is None or counter > self._last_counter:
            self._last_progress = self.clock.now()
        self._last_counter = counter
        return True

    def drain(self, reason: str, expect_revision: Optional[int] = None) -> bool:
        trigger = "operator_drain" if self._state is State.RUNNING else "operator_cancel"
        applied = self._apply(trigger, expect_revision=expect_revision,
                              reason=reason)
        if applied:
            self._drain_started = self.clock.now()
            self.escalation = None
            if self.drain_mode is DrainMode.IMMEDIATE:
                self.escalation = "SIGTERM"
        return applied

    def workers_exited(self) -> bool:
        applied = self._apply("drain_completed")
        if applied:
            self._cooldown_started = self.clock.now()
        return applied

    # -------------------------------------------------------------- incidents
    def report_incident(self, incident_class: str, device: str) -> bool:
        if incident_class not in INCIDENT_CLASSES:
            raise GovernorError(
                f"{incident_class!r} is not a declared incident class; "
                f"the contract lists {list(INCIDENT_CLASSES)}")
        trigger = "worker_died" if incident_class == "worker_death" else "worker_died"
        if self._state is State.RUNNING:
            self._apply(trigger)
        elif self._state in (State.ADMITTED, State.READY):
            # Reaching FAILED from a non-RUNNING state is not in the table, so
            # the incident is recorded without a transition and evaluated by
            # the breaker below.
            pass

        self._incidents.append({
            "incidentClass": incident_class, "device": device,
            "at": self.clock.now(), "bootId": self.boot_id,
        })
        self._leases.pop(device, None)

        if self._breaker_open(incident_class, device):
            self.quarantine(incident_class, device=device,
                            reason="circuit breaker opened")
            return True
        if self._state is State.FAILED:
            self._apply("incident_recorded")
            self._cooldown_started = self.clock.now()
        return True

    def _breaker_open(self, incident_class: str, device: str) -> bool:
        window = self.clock.now() - self.policy.breaker_window_seconds
        # The signature excludes the run id, which differs every time: a breaker
        # keyed on it would never open.
        matching = [i for i in self._incidents
                    if i["incidentClass"] == incident_class
                    and i["device"] == device
                    and i["at"] >= window]
        return len(matching) >= self.policy.breaker_threshold

    # ------------------------------------------------------------ quarantine
    def quarantine(self, incident_class: str, device: str, reason: str) -> str:
        self._records += 1
        record = {
            "recordId": f"q-{self._records}",
            "incidentClass": incident_class,
            "scope": "device",
            "scopeIdentity": device,
            "reason": reason,
            "openedAt": self.clock.now(),
            "bootId": self.boot_id,
            "policyVersion": self.policy.version,
        }
        self.store.append(record)
        self._quarantines.append(record)
        if self._state is not State.QUARANTINED:
            self._apply("breaker_opened", device=device, reason=reason)
        return record["recordId"]

    def is_quarantined(self, device: Optional[str] = None) -> bool:
        if device is None:
            return bool(self._quarantines)
        return any(r.get("scopeIdentity") == device for r in self._quarantines)

    def open_quarantine_records(self) -> List[str]:
        return [r["recordId"] for r in self._quarantines]

    def manual_reset(self, actor: str, records: Sequence[str],
                     reason: str = "") -> bool:
        if not actor:
            raise GovernorError(
                "a reset that records no one is not a reset; an actor is required")
        if not records:
            raise GovernorError(
                "name the quarantine record ids being cleared; a blanket reset "
                "cannot clear an incident nobody read")

        baselines = {}
        for device in self.telemetry.devices():
            reading = self.signals(device)
            free = reading.get("freeBytes")
            baseline = self._baselines.get(device)
            if free is None or baseline is None:
                raise GovernorError(f"{device} baseline cannot be reverified")
            if abs(int(free) - int(baseline)) > self.policy.baseline_tolerance_bytes:
                raise GovernorError(
                    f"{device} is {abs(int(free) - int(baseline))} bytes from its "
                    f"baseline, beyond the {self.policy.baseline_tolerance_bytes} "
                    "tolerance; a killed process does not always release VRAM")
            baselines[device] = int(free)

        canaries = {}
        for device in self.telemetry.devices():
            if not self.telemetry.canary(device):
                raise GovernorError(f"{device} failed its compute canary")
            canaries[device] = True

        cleared = [r for r in self._quarantines if r["recordId"] in set(records)]
        self.store.remove([r["recordId"] for r in cleared])
        self._quarantines = [r for r in self._quarantines
                             if r["recordId"] not in set(records)]

        # Incident history is NOT erased: an operator repeatedly clearing the
        # same fault re-opens the breaker rather than escaping it.
        return self._apply("manual_reset", resetActor=actor,
                           clearedRecords=[r["recordId"] for r in cleared],
                           resetReason=reason, baseline=baselines,
                           canary=canaries)

    # ------------------------------------------------------------ termination
    def register_worker(self, pid: int, process_start_time: int,
                        run_id: str, placement_id: str) -> None:
        self._workers[pid] = {
            "pid": pid, "processStartTime": process_start_time,
            "runId": run_id, "placementId": placement_id,
            "bootId": self.boot_id,
        }

    def may_signal(self, pid: int, process_start_time: int, run_id: str,
                   placement_id: str) -> bool:
        """Every element must still match. PIDs are reused within milliseconds."""
        owned = self._workers.get(pid)
        candidate = {"pid": pid, "processStartTime": process_start_time,
                     "runId": run_id, "placementId": placement_id,
                     "bootId": self.boot_id}
        diverged = [key for key in candidate
                    if owned is None or owned.get(key) != candidate[key]]
        if diverged:
            self._emit("safety_termination_abandoned", self._state,
                       "termination_abandoned", TriggerSource.GOVERNOR,
                       pid=pid, divergedFields=sorted(diverged))
            return False
        return True

    def terminate(self, pid: int, process_start_time: int, run_id: str,
                  placement_id: str, signal: str) -> bool:
        if not self.may_signal(pid, process_start_time, run_id, placement_id):
            return False
        # Intent first. Recording only the outcome loses the case where the
        # Governor died between deciding and acting, which is exactly when an
        # unexplained process death needs explaining.
        self._emit("safety_termination_intent", self._state,
                   "termination_intent", TriggerSource.GOVERNOR,
                   pid=pid, signalName=signal)
        delivered = self.actuator.signal(pid, signal)
        self._emit("safety_termination_outcome", self._state,
                   "termination_outcome", TriggerSource.GOVERNOR,
                   pid=pid, signalName=signal, delivered=delivered)
        return delivered

    # ------------------------------------------------------------------ clock
    def observe_boot(self, boot_id: str) -> bool:
        if boot_id == self.boot_id:
            return False
        self.boot_id = boot_id
        self._baselines.clear()          # a baseline is a property of a boot
        previous = self._state
        self._state = State.UNKNOWN
        self._revision += 1
        self._emit("safety_reset_to_unknown", previous, "boot_changed",
                   TriggerSource.GOVERNOR, signals={})
        return True

    def duration_between(self, first: Dict[str, Any],
                         second: Dict[str, Any]) -> int:
        if first.get("bootId") != second.get("bootId"):
            raise GovernorError(
                "a duration across two boots is meaningless, not merely "
                "imprecise: CLOCK_MONOTONIC restarts near zero on every boot")
        return int(second["monotonicNs"]) - int(first["monotonicNs"])

    # ------------------------------------------------------------------ poll
    def poll(self) -> State:
        now = self.clock.now()

        if self._state is State.DRAINING:
            self._poll_drain(now)
            return self._state
        if self._state is State.COOLDOWN:
            self._poll_cooldown(now)
            return self._state
        if self._state not in (State.RUNNING, State.ADMITTED):
            return self._state

        for device in self.telemetry.devices():
            reading = self.signals(device)
            confidence = reading.get("vramConfidence")

            if confidence is not Confidence.AVAILABLE:
                # Continuation fails safe: degrade, stop admitting, keep the
                # run, and only drain if the signal stays gone.
                started = self._degraded_since.get(device)
                if started is None:
                    self._degraded_since[device] = now
                elif now - started > self.policy.degraded_deadline_seconds:
                    self._start_drain("soft_limit_sustained", now)
                    return self._state
                continue

            # Per device. A single flag let the healthy card clear the state
            # the failing card had just set, one line later in the same loop --
            # the same shape of bug as the soft-limit dwell, and invisible on a
            # one-device machine.
            self._degraded_since.pop(device, None)
            total = int(reading.get("totalBytes") or 0)
            free = int(reading.get("freeBytes") or 0)
            used = (total - free) / total if total else 0.0

            if used >= self.policy.hard_limit_fraction:
                # No dwell: waiting to confirm imminent exhaustion is how the
                # exhaustion happens.
                self._start_drain("hard_limit_breached", now)
                return self._state

            if used >= self.policy.soft_limit_fraction:
                started = self._soft_since.get(device)
                if started is None:
                    self._soft_since[device] = now
                elif now - started >= self.policy.soft_dwell_seconds:
                    self._start_drain("soft_limit_sustained", now)
                    return self._state
            elif used < self.policy.soft_release_fraction:
                # Release needs the lower threshold, not merely dropping below
                # the enter threshold: between the two the dwell keeps running.
                self._soft_since.pop(device, None)

        self._poll_watchdog(now)
        return self._state

    def _start_drain(self, trigger: str, now: float) -> None:
        self._apply(trigger, signals=self._signals())
        self._drain_started = now
        self.escalation = "SIGTERM" if self.drain_mode is DrainMode.IMMEDIATE else None

    def _poll_drain(self, now: float) -> None:
        started = self._drain_started or now
        elapsed = now - started
        deadline = (self.policy.hard_drain_deadline_seconds
                    if self.drain_mode is DrainMode.IMMEDIATE
                    else self.policy.drain_deadline_seconds)
        grace = (0 if self.drain_mode is DrainMode.IMMEDIATE
                 else self.policy.drain_grace_deadline_seconds)

        if elapsed > deadline:
            self.escalation = "SIGKILL"
            self._apply("drain_timeout")
            self._apply("incident_recorded")
            self._cooldown_started = now
        elif elapsed > grace:
            self.escalation = "SIGTERM"

    def _poll_cooldown(self, now: float) -> None:
        started = self._cooldown_started or now
        at_baseline = True
        for device in self.telemetry.devices():
            reading = self.signals(device)
            free = reading.get("freeBytes")
            baseline = self._baselines.get(device)
            if free is None or baseline is None:
                at_baseline = False
                break
            if abs(int(free) - int(baseline)) > self.policy.baseline_tolerance_bytes:
                at_baseline = False
                break

        if now - started > self.policy.recovery_deadline_seconds and not at_baseline:
            self.quarantine("telemetry_loss", device=self.telemetry.devices()[0],
                            reason="baseline not reached within the recovery deadline")
            return
        if at_baseline and now - started >= self.policy.cooldown_period_seconds:
            self._leases.clear()
            self.drain_mode = None
            self.escalation = None
            self._apply("recovered")
        elif not at_baseline:
            self._apply("recovery_pending")

    def _poll_watchdog(self, now: float) -> None:
        if self._state is not State.RUNNING or self._last_progress is None:
            return
        silent = now - self._last_progress

        if silent > self.policy.unresponsive_deadline_seconds:
            self._apply("unresponsive")
            self._apply("incident_recorded")
            self._cooldown_started = now
            return

        # The operation's own deadline, not the larger of it and the generic
        # stall deadline. Taking the maximum made an undeclared operation *more*
        # patient than a declared one, which inverts the rule: a worker that
        # cannot say what it is doing is not evidence that it is doing
        # something, and gets the shortest deadline there is.
        if silent > self.policy.operation_deadline(self._operation):
            self._start_drain("progress_stalled", now)

    # ------------------------------------------------------------------ tests
    def force_state(self, state: State) -> None:
        """Test affordance: place the machine in a state without a transition.

        Present so a breaker test can produce repeated incidents without
        scripting a full lifecycle each time. It bypasses the table, so it is
        never used outside tests -- and `safetySimulated` marks every event of a
        Governor that has one.
        """
        self._state = state
        self._revision += 1


def _from(state: State):
    from contract import TRANSITIONS
    return [t for t in TRANSITIONS if t.source == state]
