"""The Safety Governor contract, as data. No behaviour lives here.

One canonical table, three consumers: the tests assert every row of it, the
prose in docs/SAFETY_GOVERNOR.md is checked against it, and Gate D's
implementation obeys it. A table written independently in prose and in code
drifts, and the drift is invisible until someone reads both carefully at the
same moment.

Importing this module must never start anything, sample anything or touch a
device. It is definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple

CONTRACT_VERSION = "safety-contract-1.1"

#: Preserved, not reinterpreted. A frozen version keeps meaning what it meant;
#: reading an old audit event against a newer table would silently misjudge the
#: decision it records.
CONTRACT_REVISIONS = {
    "safety-contract-1": (
        "Frozen at Gate C.1. ADMITTED had no incident path: a worker dying "
        "between admission and the canary, or a breaker opening on an incident "
        "recorded there, had nowhere to go. Events carrying this version were "
        "produced by a Governor that raised in that window."),
    "safety-contract-1.1": (
        "Closes the ADMITTED incident gap. Adds ADMITTED -> FAILED on "
        "worker_died and unresponsive, ADMITTED -> DRAINING on "
        "hard_limit_breached in immediate mode, and ADMITTED -> QUARANTINED on "
        "breaker_opened. Every exit from ADMITTED now declares what becomes of "
        "its lease."),
}

#: What an exit does to the lease the state was holding. Declared per transition
#: so "the lease was released" is a fact the table states, not something a reader
#: has to infer from the implementation.
LEASE_RETAINED = "retained"
LEASE_RELEASED = "released"


class State(str, Enum):
    UNKNOWN = "UNKNOWN"
    READY = "READY"
    ADMITTED = "ADMITTED"
    RUNNING = "RUNNING"
    DRAINING = "DRAINING"
    COOLDOWN = "COOLDOWN"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"


class TriggerSource(str, Enum):
    """Who reports the fact. Never who applies it."""

    GOVERNOR = "governor"
    OPERATOR = "operator"
    WORKER = "worker"


#: Every transition is applied by the Governor and only by the Governor. A
#: worker reporting its own death does not move the state machine; it reports a
#: fact the Governor acts on. Recording the executor separately from the source
#: keeps "who noticed" and "who decided" from collapsing into one field, which
#: is what makes an audit trail answerable to "who did this".
TRANSITION_EXECUTOR = "governor"


class DrainMode(str, Enum):
    """How much grace a drain gets. Chosen by the trigger, not by the drainer."""

    #: Finish the current unit of work, then escalate on the normal deadlines.
    GRACEFUL = "graceful"
    #: SIGTERM immediately, no finish-current-unit grace, short kill deadline.
    #: Reserved for exhaustion, where the grace is what causes the failure.
    IMMEDIATE = "immediate"


class Confidence(str, Enum):
    AVAILABLE = "AVAILABLE"
    STALE = "STALE"
    #: Known absent on this platform, with a recorded reason. A capability gap.
    UNAVAILABLE_EXPECTED = "UNAVAILABLE_EXPECTED"
    #: A source that should work did not. A fault.
    UNAVAILABLE_UNEXPECTED = "UNAVAILABLE_UNEXPECTED"


class Disposition(str, Enum):
    #: Absence blocks the action.
    FAIL_CLOSED = "fail-closed"
    #: Absence is tolerated; the system degrades rather than stopping.
    FAIL_SAFE = "fail-safe"
    #: Tolerated now, blocking if it persists past a deadline.
    FAIL_SAFE_THEN_CLOSED = "fail-safe-then-closed"
    #: The signal does not apply in this phase.
    NOT_APPLICABLE = "n/a"


@dataclass(frozen=True)
class Transition:
    source: State
    target: State
    trigger: str
    trigger_source: TriggerSource
    audit_event: str
    #: Names a Policy attribute, so a deadline is stated once.
    timeout_key: Optional[str] = None
    drain_mode: Optional[DrainMode] = None
    #: LEASE_RETAINED, LEASE_RELEASED, or None where no lease is involved.
    #: Every exit from ADMITTED declares one: a lease that is neither carried
    #: forward nor released is stranded, and a stranded lease is capacity the
    #: Governor believes is in use forever.
    lease_outcome: Optional[str] = None
    note: str = ""

    @property
    def key(self) -> Tuple[str, str]:
        return (self.source.value, self.trigger)


#: The complete transition table. Every transition not here is forbidden, and an
#: implementation asked to make one raises rather than choosing a plausible
#: target: a state machine that repairs itself silently cannot be reasoned about.
TRANSITIONS: Tuple[Transition, ...] = (
    Transition(
        State.UNKNOWN, State.READY, "preflight_passed", TriggerSource.GOVERNOR,
        "safety_ready", "preflight_deadline_seconds",
        note="device identities resolved, baseline sampled, required telemetry available"),
    Transition(
        State.UNKNOWN, State.FAILED, "preflight_failed", TriggerSource.GOVERNOR,
        "safety_preflight_failed",
        note="identity unresolvable, no device, or baseline unsamplable"),
    Transition(
        State.UNKNOWN, State.QUARANTINED, "quarantine_restored", TriggerSource.GOVERNOR,
        "safety_quarantine_restored",
        note="an unresolved quarantine record from a previous boot"),

    Transition(
        State.READY, State.ADMITTED, "admission_granted", TriggerSource.GOVERNOR,
        "safety_admitted", "admission_deadline_seconds"),
    Transition(
        State.READY, State.READY, "admission_refused", TriggerSource.GOVERNOR,
        "safety_admission_refused",
        note="a refusal is not a fault; the machine is not at fault and stays READY"),
    Transition(
        State.READY, State.QUARANTINED, "breaker_opened", TriggerSource.GOVERNOR,
        "safety_quarantined"),
    Transition(
        State.READY, State.UNKNOWN, "boot_changed", TriggerSource.GOVERNOR,
        "safety_reset_to_unknown",
        note="nothing measured in another boot describes this one"),

    Transition(
        State.ADMITTED, State.RUNNING, "canary_passed", TriggerSource.GOVERNOR,
        "safety_running", "canary_deadline_seconds",
        lease_outcome=LEASE_RETAINED,
        note="the work is starting; the lease carries forward into RUNNING"),
    Transition(
        State.ADMITTED, State.DRAINING, "operator_cancel", TriggerSource.OPERATOR,
        "safety_drain_started", drain_mode=DrainMode.GRACEFUL,
        lease_outcome=LEASE_RELEASED),
    Transition(
        State.ADMITTED, State.DRAINING, "soft_limit_sustained", TriggerSource.GOVERNOR,
        "safety_drain_started", drain_mode=DrainMode.GRACEFUL,
        lease_outcome=LEASE_RELEASED),
    Transition(
        State.ADMITTED, State.FAILED, "canary_failed", TriggerSource.GOVERNOR,
        "safety_canary_failed", "canary_deadline_seconds",
        lease_outcome=LEASE_RELEASED),
    # Added in safety-contract-1.1. The window between reserving capacity and
    # the canary passing is short, but it is where allocation and weight-load
    # setup happen -- which is when an OOM is most likely.
    Transition(
        State.ADMITTED, State.FAILED, "worker_died", TriggerSource.WORKER,
        "safety_incident", lease_outcome=LEASE_RELEASED,
        note="nothing left to drain, and the lease must not outlive the worker"),
    Transition(
        State.ADMITTED, State.FAILED, "unresponsive", TriggerSource.GOVERNOR,
        "safety_incident", "unresponsive_deadline_seconds",
        lease_outcome=LEASE_RELEASED),
    Transition(
        State.ADMITTED, State.DRAINING, "hard_limit_breached", TriggerSource.GOVERNOR,
        "safety_drain_started", drain_mode=DrainMode.IMMEDIATE,
        lease_outcome=LEASE_RELEASED,
        note="exhaustion during setup; SIGTERM at once, for the same reason as "
             "from RUNNING -- the grace is what causes the failure"),
    Transition(
        State.ADMITTED, State.QUARANTINED, "breaker_opened", TriggerSource.GOVERNOR,
        "safety_quarantined", lease_outcome=LEASE_RELEASED),

    Transition(
        State.RUNNING, State.DRAINING, "soft_limit_sustained", TriggerSource.GOVERNOR,
        "safety_drain_started", drain_mode=DrainMode.GRACEFUL),
    Transition(
        State.RUNNING, State.DRAINING, "hard_limit_breached", TriggerSource.GOVERNOR,
        "safety_drain_started", drain_mode=DrainMode.IMMEDIATE,
        note="exhaustion is imminent; the grace period is what causes the failure"),
    Transition(
        State.RUNNING, State.DRAINING, "progress_stalled", TriggerSource.GOVERNOR,
        "safety_drain_started", drain_mode=DrainMode.GRACEFUL,
        note="the process still responds and may exit cleanly if asked"),
    Transition(
        State.RUNNING, State.DRAINING, "operator_drain", TriggerSource.OPERATOR,
        "safety_drain_started", drain_mode=DrainMode.GRACEFUL),
    Transition(
        State.RUNNING, State.COOLDOWN, "work_completed", TriggerSource.WORKER,
        "safety_completed"),
    Transition(
        State.RUNNING, State.FAILED, "worker_died", TriggerSource.WORKER,
        "safety_incident",
        note="there is nothing left to drain"),
    Transition(
        State.RUNNING, State.FAILED, "unresponsive", TriggerSource.GOVERNOR,
        "safety_incident", "unresponsive_deadline_seconds",
        note="no signal of any kind; nothing to drain gracefully"),

    Transition(
        State.DRAINING, State.COOLDOWN, "drain_completed", TriggerSource.GOVERNOR,
        "safety_drained", "drain_deadline_seconds"),
    Transition(
        State.DRAINING, State.FAILED, "drain_timeout", TriggerSource.GOVERNOR,
        "safety_drain_timeout", "drain_deadline_seconds",
        note="forced termination; a drain needing SIGKILL is a different fact "
             "from one that did not, even though both end with the workers gone"),

    Transition(
        State.COOLDOWN, State.READY, "recovered", TriggerSource.GOVERNOR,
        "safety_recovered", "cooldown_period_seconds",
        note="cooldown elapsed and baseline reverified"),
    Transition(
        State.COOLDOWN, State.COOLDOWN, "recovery_pending", TriggerSource.GOVERNOR,
        "safety_recovery_pending",
        note="baseline not yet reverified; a killed process does not always "
             "release VRAM promptly"),
    Transition(
        State.COOLDOWN, State.QUARANTINED, "recovery_failed", TriggerSource.GOVERNOR,
        "safety_recovery_failed", "recovery_deadline_seconds"),
    Transition(
        State.COOLDOWN, State.QUARANTINED, "breaker_opened", TriggerSource.GOVERNOR,
        "safety_quarantined"),

    Transition(
        State.FAILED, State.COOLDOWN, "incident_recorded", TriggerSource.GOVERNOR,
        "safety_incident_recorded",
        note="FAILED is a recording state; a Governor resting in it is "
             "indistinguishable from one that crashed"),
    Transition(
        State.FAILED, State.QUARANTINED, "breaker_opened", TriggerSource.GOVERNOR,
        "safety_quarantined"),

    Transition(
        State.QUARANTINED, State.READY, "manual_reset", TriggerSource.OPERATOR,
        "safety_manual_reset",
        note="the only transition an operator alone can cause; the Governor "
             "never releases its own quarantine"),
)


@dataclass(frozen=True)
class Policy:
    """Thresholds are data. Changing one is a policy version bump, not a patch."""

    version: str = "safety-policy-1-provisional"

    preflight_deadline_seconds: int = 60
    admission_deadline_seconds: int = 30
    canary_deadline_seconds: int = 10
    drain_grace_deadline_seconds: int = 30
    drain_deadline_seconds: int = 120
    #: An immediate drain kills sooner: the grace is what causes the failure.
    hard_drain_deadline_seconds: int = 30
    cooldown_period_seconds: int = 60
    recovery_deadline_seconds: int = 600
    degraded_deadline_seconds: int = 120

    reserve_floor_bytes: int = 536870912          # 512 MiB
    reserve_fraction: float = 0.03
    baseline_tolerance_bytes: int = 268435456     # 256 MiB
    baseline_sample_count: int = 5
    baseline_sample_interval_seconds: int = 1

    soft_limit_fraction: float = 0.90
    soft_release_fraction: float = 0.85
    soft_dwell_seconds: int = 10
    soft_release_dwell_seconds: int = 30
    hard_limit_fraction: float = 0.97

    max_signal_age_seconds: int = 30
    progress_interval_seconds: int = 15
    stall_deadline_seconds: int = 60
    unresponsive_deadline_seconds: int = 180

    breaker_threshold: int = 3
    breaker_window_seconds: int = 3600
    max_concurrent_leases: int = 1

    def reserve_bytes(self, total_bytes: int) -> int:
        return max(self.reserve_floor_bytes,
                   int(self.reserve_fraction * total_bytes))

    def operation_deadline(self, operation: Optional[str]) -> int:
        """Unknown or undeclared operations get the shortest deadline.

        A worker that cannot say what it is doing is not evidence that it is
        doing something.
        """
        if not operation:
            return min(OPERATION_DEADLINES.values())
        return OPERATION_DEADLINES.get(operation, min(OPERATION_DEADLINES.values()))


#: Per-operation watchdog deadlines. A deadline calibrated to token generation
#: would fire during a legitimate 22.84 GiB weight load.
OPERATION_DEADLINES: Dict[str, int] = {
    "weight_load": 900,
    "prefill": 60,
    "decode_step": 15,
    "boundary_transfer": 30,
    #: The same named value the ADMITTED -> RUNNING transition uses.
    "canary": Policy().canary_deadline_seconds,
}


@dataclass(frozen=True)
class SignalRule:
    signal: str
    at_admission: Disposition
    while_running: Disposition
    note: str = ""


#: Admission fails closed, continuation fails safe, exhaustion fails closed.
#: Refusing to start costs a refusal; stopping a healthy run costs the work;
#: failing to stop an exhausting one costs more than either.
SIGNAL_RULES: Tuple[SignalRule, ...] = (
    SignalRule("device_enumeration", Disposition.FAIL_CLOSED, Disposition.FAIL_CLOSED),
    SignalRule("device_identity", Disposition.FAIL_CLOSED, Disposition.FAIL_CLOSED),
    SignalRule("vram", Disposition.FAIL_CLOSED, Disposition.FAIL_SAFE_THEN_CLOSED,
               "degrade and stop admitting; drain if unavailable past degraded_deadline"),
    SignalRule("baseline_vram", Disposition.FAIL_CLOSED, Disposition.NOT_APPLICABLE,
               "sampled at preflight only"),
    SignalRule("compute_canary", Disposition.FAIL_CLOSED, Disposition.NOT_APPLICABLE),
    SignalRule("worker_heartbeat", Disposition.NOT_APPLICABLE, Disposition.FAIL_CLOSED,
               "stalled, then unresponsive"),
    SignalRule("transport_progress", Disposition.NOT_APPLICABLE, Disposition.FAIL_SAFE,
               "the heartbeat is authoritative; absence alone is not a stall"),
    SignalRule("power_expected_absent", Disposition.FAIL_SAFE, Disposition.FAIL_SAFE,
               "advisory only in policy v1"),
    SignalRule("power_unexpected_absent", Disposition.FAIL_CLOSED,
               Disposition.FAIL_SAFE_THEN_CLOSED,
               "a source that worked has stopped, which is a fault"),
    SignalRule("temperature_expected_absent", Disposition.FAIL_SAFE, Disposition.FAIL_SAFE,
               "advisory only in policy v1"),
    SignalRule("temperature_unexpected_absent", Disposition.FAIL_CLOSED,
               Disposition.FAIL_SAFE_THEN_CLOSED),
    SignalRule("throttle_reasons", Disposition.FAIL_SAFE, Disposition.FAIL_SAFE,
               "advisory"),
    SignalRule("quarantine_store", Disposition.FAIL_CLOSED, Disposition.FAIL_CLOSED,
               "an unreadable quarantine store counts as quarantined; otherwise "
               "corrupting one file is how a quarantine gets cleared"),
)


#: Signals policy v1 records but never acts on. Stated as data so the claim
#: boundary is checkable rather than a sentence someone has to remember.
#:
#: Under WSL the AMD card has no qualified power or thermal signal at all --
#: rocm-smi needs the amdgpu kernel module and WSL exposes /dev/dxg. A policy
#: that gated on them would refuse all work on the only qualified platform, and
#: one that claimed to act on them would be claiming thermal protection it
#: cannot perform.
ADVISORY_ONLY_SIGNALS: Tuple[str, ...] = ("power", "temperature", "throttle_reasons")


@dataclass(frozen=True)
class TerminationIdentity:
    """What must still match before the Governor may signal a process.

    PIDs are reused. Signalling a PID that has been recycled kills whatever
    inherited it, which on a developer machine is as likely to be an editor as
    a worker. Every element is checked immediately before the signal, and the
    intent is recorded before it is sent so an unexplained death has a record.
    """

    pid: int
    process_start_time: int      # from /proc/<pid>/stat field 22, jiffies
    run_id: str
    placement_id: str
    boot_id: str


#: Fields a quarantine record must carry to be considered readable. A record
#: missing any of them is treated as corrupt, and a corrupt record is treated as
#: a quarantine (SIGNAL_RULES, quarantine_store).
QUARANTINE_RECORD_FIELDS: Tuple[str, ...] = (
    "recordId", "incidentClass", "scope", "scopeIdentity", "reason",
    "openedAt", "bootId", "policyVersion", "digest",
)

#: Incident classes that form a breaker signature with (scope, scopeIdentity).
#: Deliberately excludes the run id, which differs every time -- a breaker keyed
#: on it would never open.
INCIDENT_CLASSES: Tuple[str, ...] = (
    "oom", "worker_death", "stall", "transport_failure", "canary_failure",
    "drain_timeout", "telemetry_loss",
)

QUARANTINE_SCOPES: Tuple[str, ...] = ("device", "adapter", "transport", "machine")


#: Fields every Governor audit event adds to audit.MINIMAL_TUPLE.
AUDIT_FIELDS: Tuple[str, ...] = (
    "safetyState", "safetyPreviousState", "safetyTrigger", "safetyTriggerSource",
    "safetyTransitionExecutor", "safetyStateRevision", "safetyPolicyVersion",
    "safetyContractVersion", "safetySignals", "safetySimulated",
)

#: Recorded on any transition whose row declares a `lease_outcome`. A lease that
#: is neither carried forward nor released is stranded, and the audit trail must
#: say which happened rather than leaving it to be reconstructed.
LEASE_AUDIT_FIELD = "safetyLeaseOutcome"


# --------------------------------------------------------------------- lookup

_BY_KEY: Dict[Tuple[str, str], Transition] = {t.key: t for t in TRANSITIONS}


def permitted(source: State, trigger: str) -> Optional[Transition]:
    """The transition for this (state, trigger), or None if forbidden."""
    return _BY_KEY.get((State(source).value, trigger))


def triggers_from(source: State) -> Tuple[str, ...]:
    return tuple(t.trigger for t in TRANSITIONS if t.source == source)


def exits_from(source: State) -> Tuple[Transition, ...]:
    return tuple(t for t in TRANSITIONS if t.source == source)


def signal_rule(signal: str) -> Optional[SignalRule]:
    for rule in SIGNAL_RULES:
        if rule.signal == signal:
            return rule
    return None
