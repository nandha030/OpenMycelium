"""The Safety Governor enforcement contract, as data. No behaviour lives here.

Gate D.3. `contract.py` says which state transitions exist; this says which of
them the Governor is permitted to *cause*, under what evidence, with what
rollback, and what stops it. Those are different questions and they change for
different reasons, so they are versioned separately -- the same reasoning that
already keeps `SHADOW_SCHEMA_VERSION` apart from the contract and policy
versions. A reader who cannot tell which of the four moved cannot safely
interpret an old audit record.

`safety-contract-1.1` is unchanged by this file and stays frozen. Nothing here
adds, removes or retargets a transition. What it adds is authority: in shadow
mode every transition in that table was computed and none was applied, and the
question D.3 answers is which ones may now be applied, in what order they are
switched on, and how each one is switched off again.

Importing this module must never start anything, sample anything or touch a
device. It is definitions.

CONTRACT ONLY. No implementation accompanies this file. The tests that come with
it are expected to fail until Gate D.3's implementation is separately authorised.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

ENFORCEMENT_CONTRACT_VERSION = "safety-enforcement-1"

#: Preserved, not reinterpreted, for the same reason `CONTRACT_REVISIONS` is.
ENFORCEMENT_REVISIONS: Dict[str, str] = {
    "safety-enforcement-1": (
        "Frozen at Gate D.3. First contract in which the Governor may act. "
        "Defines the authority ladder, the fault matrix, the rollback "
        "mechanism, the hardware stopping conditions, the canary order and the "
        "evidence every action must carry. No implementation exists at the "
        "time of freezing."),
}

#: The evidence record an enforcement action writes. Separate from
#: SHADOW_SCHEMA_VERSION because a shadow observation and an enforcement action
#: are different records: one says what would have happened, the other says what
#: did, and the second must additionally carry the first for comparison.
ENFORCEMENT_SCHEMA_VERSION = 1


# --------------------------------------------------------------------- authority

class Authority(str, Enum):
    """What the Governor is permitted to do. A ladder, not a switch.

    Each rung is authorised separately and independently, because each one
    fails differently and the evidence for one says nothing about the next.
    Refusing an admission cannot corrupt an in-flight run; terminating a worker
    can. They do not belong behind the same flag.

    The Governor refuses to take an action above its current authority. It does
    not silently downgrade to the strongest action it is allowed -- a request to
    do something it may not do is a defect in the caller, and hiding it makes
    the ladder meaningless.
    """

    OFF = "off"
    SHADOW = "shadow"
    ENFORCE_ADMISSION = "enforce_admission"
    ENFORCE_DRAIN = "enforce_drain"
    ENFORCE_BREAKER = "enforce_breaker"
    ENFORCE_QUARANTINE = "enforce_quarantine"
    ENFORCE_FULL = "enforce_full"


#: Ordered weakest to strongest. Position is meaning: an authority permits every
#: action permitted by the rungs below it.
AUTHORITY_ORDER: Tuple[Authority, ...] = (
    Authority.OFF,
    Authority.SHADOW,
    Authority.ENFORCE_ADMISSION,
    Authority.ENFORCE_DRAIN,
    Authority.ENFORCE_BREAKER,
    Authority.ENFORCE_QUARANTINE,
    Authority.ENFORCE_FULL,
)

#: The default, and it does not change with this contract. Freezing an
#: enforcement contract is not the same as enabling enforcement, and D.3 does
#: not enable it: injected faults and hardware canaries come first, each
#: separately authorised.
DEFAULT_AUTHORITY = Authority.OFF


class Action(str, Enum):
    """What the Governor may do to a run. Ordered by how hard it is to undo.

    `refuse_admission` prevents work that has not started. `drain_graceful`
    stops new work and lets in-flight work finish. `drain_immediate` interrupts
    it. `quarantine` persists a refusal across runs. Reversibility decreases
    down the list, and so does the willingness to take the action on weak
    evidence.
    """

    NONE = "none"
    REFUSE_ADMISSION = "refuse_admission"
    DEGRADE = "degrade"
    DRAIN_GRACEFUL = "drain_graceful"
    DRAIN_IMMEDIATE = "drain_immediate"
    OPEN_BREAKER = "open_breaker"
    QUARANTINE = "quarantine"


#: The lowest authority that permits each action.
ACTION_REQUIRES: Dict[Action, Authority] = {
    Action.NONE: Authority.OFF,
    Action.REFUSE_ADMISSION: Authority.ENFORCE_ADMISSION,
    Action.DEGRADE: Authority.ENFORCE_ADMISSION,
    Action.DRAIN_GRACEFUL: Authority.ENFORCE_DRAIN,
    Action.DRAIN_IMMEDIATE: Authority.ENFORCE_DRAIN,
    Action.OPEN_BREAKER: Authority.ENFORCE_BREAKER,
    Action.QUARANTINE: Authority.ENFORCE_QUARANTINE,
}


class Confidence(str, Enum):
    """Reused verbatim from `contract.py`, restated so the matrix is readable.

    The three-valued split is the point: a platform that never had a sensor and
    a sensor that stopped answering are different facts, and collapsing them is
    how a missing reading becomes a false zero.
    """

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE_EXPECTED = "UNAVAILABLE_EXPECTED"
    UNAVAILABLE_UNEXPECTED = "UNAVAILABLE_UNEXPECTED"


# ------------------------------------------------------------------ fault matrix

@dataclass(frozen=True)
class Fault:
    """One row of the fault matrix.

    `permitted_action` is the strongest action this fault may ever justify. The
    Governor may take a weaker one; it may never take a stronger one, whatever
    its authority. Authority says what is switched on, this says what the fault
    itself warrants, and an action needs both.
    """

    name: str
    detected_by: str
    #: The minimum telemetry confidence required before acting at all.
    requires_confidence: Confidence
    permitted_action: Action
    #: Which `contract.py` trigger this fault raises, so the fault matrix and
    #: the transition table are joined by name rather than by convention.
    #:
    #: `None` where the response changes no state. Writing the matrix made the
    #: distinction visible and it is worth stating: `DEGRADE` means stop
    #: admitting and leave in-flight work alone, which is a change in what the
    #: Governor will agree to next, not a move through the state machine. The
    #: frozen table has no trigger for it and must not grow one -- inventing a
    #: transition to make a matrix tidy is how a state machine stops describing
    #: the system.
    contract_trigger: Optional[str]
    rollback: str
    note: str = ""


#: The complete fault matrix. A fault not listed here justifies no action: the
#: Governor reports it and does nothing, because an unlisted fault is one whose
#: consequences were never argued through.
FAULTS: Tuple[Fault, ...] = (
    Fault(
        "vram_reservation_unmet", "admission check against the reserve floor",
        Confidence.AVAILABLE, Action.REFUSE_ADMISSION, "admission_refused",
        "raise authority to SHADOW; the run proceeds as it does today",
        note="the only fault whose action prevents work rather than interrupting it"),
    Fault(
        "vram_unreadable_at_admission", "free bytes absent with UNAVAILABLE_UNEXPECTED",
        Confidence.UNAVAILABLE_UNEXPECTED, Action.REFUSE_ADMISSION,
        "admission_refused",
        "raise authority to SHADOW",
        note="fail closed at admission: an unreadable card is not an empty one"),
    Fault(
        "soft_limit_sustained", "used fraction over soft limit for the dwell",
        Confidence.AVAILABLE, Action.DRAIN_GRACEFUL, "soft_limit_sustained",
        "authority to ENFORCE_ADMISSION; in-flight work is left alone",
        note="hysteresis and dwell are policy, not enforcement; both must hold"),
    Fault(
        "hard_limit_breached", "used fraction at or over the hard limit",
        Confidence.AVAILABLE, Action.DRAIN_IMMEDIATE, "hard_limit_breached",
        "authority to ENFORCE_ADMISSION",
        note="the one in-flight interruption justified by a reading alone"),
    Fault(
        "telemetry_stale", "newest sample older than max_signal_age_seconds",
        Confidence.AVAILABLE, Action.DEGRADE, None,
        "authority to SHADOW",
        note="stop admitting, do not interrupt: a stale reading is not a fault report"),
    Fault(
        "telemetry_unavailable_unexpected", "a source that named itself returned nothing",
        Confidence.UNAVAILABLE_UNEXPECTED, Action.DEGRADE,
        None, "authority to SHADOW",
        note="expected unavailability -- AMD power under WSL -- is not a fault and is absent from this table"),
    Fault(
        "worker_died", "process exit observed with a matching identity",
        Confidence.AVAILABLE, Action.DRAIN_IMMEDIATE, "worker_died",
        "authority to ENFORCE_ADMISSION",
        note="identity must match pid and process start time, never pid alone"),
    Fault(
        "progress_stalled", "no heartbeat within the operation deadline",
        Confidence.AVAILABLE, Action.DRAIN_IMMEDIATE, "progress_stalled",
        "authority to ENFORCE_ADMISSION",
        note="per-operation deadline; an undeclared operation gets the shortest"),
    Fault(
        "unresponsive", "no heartbeat within unresponsive_deadline_seconds",
        Confidence.AVAILABLE, Action.DRAIN_IMMEDIATE, "unresponsive",
        "authority to ENFORCE_ADMISSION"),
    Fault(
        "canary_failed", "the compute canary did not return the expected value",
        Confidence.AVAILABLE, Action.DRAIN_IMMEDIATE, "canary_failed",
        "authority to ENFORCE_ADMISSION",
        note="admitted but not proven: the window where an OOM is most likely"),
    Fault(
        "boundary_integrity_failed", "sent and received digests differ at the boundary",
        Confidence.AVAILABLE, Action.QUARANTINE, "incident_recorded",
        "authority to ENFORCE_BREAKER; the record persists and is reset manually",
        note="the only fault that quarantines on a single occurrence"),
    Fault(
        "repeated_incidents", "breaker_threshold incidents within breaker_window_seconds",
        Confidence.AVAILABLE, Action.OPEN_BREAKER, "breaker_opened",
        "authority to ENFORCE_DRAIN; the counter is not cleared by the rollback"),
    Fault(
        "lease_stranded", "a lease outlives the run that took it",
        Confidence.AVAILABLE, Action.DEGRADE, None,
        "authority to SHADOW",
        note="capacity the Governor believes is in use forever; reported before it is reclaimed"),
    Fault(
        "boot_changed", "bootId differs from the one the lease was taken under",
        Confidence.AVAILABLE, Action.NONE, "boot_changed",
        "none required; the Governor always revises its own belief",
        note="READY -> UNKNOWN in the frozen table. This is the Governor correcting "
             "what it thinks is true, not an action on the world, so it needs no "
             "authority and is taken at every rung including OFF. Durations do not "
             "cross a boot, and a lease from a previous boot is not a lease"),
)


# --------------------------------------------------------------------- canaries

@dataclass(frozen=True)
class CanaryStage:
    """One rung of the ladder, and what it takes to climb it.

    Ordered. A stage may not begin before the one before it has passed, and
    each requires its own authorisation -- passing stage one is evidence about
    stage one only.
    """

    order: int
    authority: Authority
    actions_enabled: Tuple[Action, ...]
    synthetic_gate: str
    hardware_gate: str
    accept_when: str
    reject_when: str
    rationale: str


CANARY_SEQUENCE: Tuple[CanaryStage, ...] = (
    CanaryStage(
        1, Authority.ENFORCE_ADMISSION,
        (Action.REFUSE_ADMISSION, Action.DEGRADE),
        "every admission fault in the matrix refuses, with an injected reading and no GPU",
        "one bounded run per fault, each refusing before a worker is launched",
        "every refusal is deterministic, carries a reason code, matches the shadow "
        "prediction for the same inputs, and leaves no worker and no lease",
        "any refusal without a matching shadow prediction, any false refusal in the "
        "clean control runs, or any run that starts after a refusal",
        "admission refusal is first because it is the only action that prevents work "
        "rather than interrupting it: a wrong refusal costs a run, a wrong "
        "termination costs a run and whatever state it held"),
    CanaryStage(
        2, Authority.ENFORCE_DRAIN,
        (Action.DRAIN_GRACEFUL, Action.DRAIN_IMMEDIATE),
        "graceful and immediate drain from every drain fault, with an injected clock",
        "one bounded run per drain mode, with cleanup verified after each",
        "in-flight work either completes or is interrupted as the mode declares, the "
        "lease is released, no worker survives, no compute context remains",
        "a drain that leaves a worker, strands a lease, or interrupts work a graceful "
        "drain promised to let finish",
        "drain interrupts running work, so it comes after refusal and is proven "
        "against cleanup evidence rather than against a memory total"),
    CanaryStage(
        3, Authority.ENFORCE_BREAKER,
        (Action.OPEN_BREAKER,),
        "the breaker opens at the threshold and not before, across the window boundary",
        "repeated injected incidents on one device, with the breaker state durable "
        "across a restart",
        "the breaker opens exactly at threshold, refuses admission while open, and "
        "its state survives a restart",
        "an open breaker that admits, a breaker that opens below threshold, or a "
        "breaker whose state is lost on restart",
        "the breaker changes behaviour for later runs, so it is proven only after "
        "single-run actions are"),
    CanaryStage(
        4, Authority.ENFORCE_QUARANTINE,
        (Action.QUARANTINE,),
        "quarantine writes durably, survives a restart, and is cleared only by an "
        "attributed manual reset",
        "one injected boundary-integrity failure, quarantine verified across a restart",
        "the record is durable, names the device and the incident class, refuses the "
        "quarantined path, and is cleared only by a named actor",
        "a quarantine that is lost, that is cleared without an actor, or that refuses "
        "a path it does not name",
        "quarantine persists across runs and needs a human to undo, so it is last"),
)


# --------------------------------------------------------------------- rollback

@dataclass(frozen=True)
class Rollback:
    """How enforcement is switched off, and what must remain true while doing it."""

    mechanism: str
    scope: str
    requires_restart: bool
    survives_crash: bool
    note: str = ""


ROLLBACK: Tuple[Rollback, ...] = (
    Rollback(
        "lower the authority to SHADOW or OFF", "the next run",
        requires_restart=False, survives_crash=True,
        note="the ladder is the rollback: every fault row names the authority to "
             "drop to, and dropping one rung disables exactly that fault's action"),
    Rollback(
        "an operator kill switch that pins authority to OFF", "immediate, all runs",
        requires_restart=False, survives_crash=True,
        note="must not depend on the Governor being healthy. A rollback that needs "
             "the thing that failed is not a rollback"),
    Rollback(
        "manual reset of a quarantine record", "one device or path",
        requires_restart=False, survives_crash=True,
        note="attributed to a named actor and recorded; quarantine is the one action "
             "no automatic path may undo"),
)


# ----------------------------------------------------------- stopping conditions

#: Conditions under which a hardware canary is aborted and not retried. These
#: are not failures of the thing being tested -- they are conditions under which
#: the test itself stops meaning anything, so continuing produces evidence about
#: nothing.
HARDWARE_STOPPING_CONDITIONS: Tuple[str, ...] = (
    "a worker process survives a termination the Governor believes succeeded",
    "a compute context remains on a device after a run the Governor believes ended",
    "bootId changes during a campaign",
    "two consecutive unexplained device faults",
    "a telemetry source that was AVAILABLE becomes UNAVAILABLE_UNEXPECTED mid-campaign",
    "an enforcement action fires with no matching shadow prediction",
    "an enforcement action fires whose reason code is not in the fault matrix",
    "the run count for the campaign is reached",
    "any action is taken above the authority under test",
)

#: Deliberately absent from the list above: a device memory total above the
#: pre-run reading. Under WSL `nvidia-smi` reports the whole physical card
#: including host usage and cannot enumerate host processes; across one sealing
#: run it read 722 MiB, then 824, then 678 with nothing of ours alive. A number
#: that moves on its own cannot stop a campaign, and treating it as a fault
#: would abort valid runs. Cleanup is judged on surviving workers and compute
#: contexts, which are attributable.
UNATTRIBUTABLE_SIGNALS: Tuple[str, ...] = (
    "whole-device memory totals under WSL",
)


# ----------------------------------------------------------------- evidence

#: Every enforcement action writes one of these. An action that cannot be tied
#: to the prediction it was supposed to match is not evidence of anything: the
#: whole method of D.3 is comparing what the Governor would have done in shadow
#: against what it did.
ENFORCEMENT_AUDIT_FIELDS: Tuple[str, ...] = (
    "schemaVersion",
    "enforcementContractVersion",
    "safetyContractVersion",
    "safetyPolicyVersion",
    "authority",
    "runId",
    "placementId",
    "manifestDigest",
    "bootId",
    "wallTimeUtc",
    "monotonicNs",
    "deviceIdentity",
    "faultName",
    "reasonCode",
    "actionTaken",
    "transition",
    "leaseOutcome",
    "shadowPredictedAction",
    "shadowPredictedTransition",
    "predictionMatched",
    "rollbackAvailable",
    "actor",
)

#: A mismatch is recorded, never suppressed. The comparison exists to find the
#: cases where shadow and enforcement disagree, and a run that hides them has
#: destroyed its own purpose.
PREDICTION_MISMATCH_IS_FATAL = True


# ------------------------------------------------------------------- non-goals

#: Stated so the scope review has something written before the code.
NON_GOALS: Tuple[str, ...] = (
    "no default enforcement: DEFAULT_AUTHORITY stays OFF when this contract freezes",
    "no Memory OS work of any kind on this branch -- no MemoryObject, "
    "ResidencyManager, TransportBackend, PrefetchPolicy, EvictionPolicy, "
    "WorkingSet, bind_working_set or paging",
    "no thermal or power enforcement: AMD power is unavailable under WSL and no "
    "platform here has the telemetry such a claim would need",
    "no change to safety-contract-1.1: not one transition is added, removed or "
    "retargeted",
    "no new package version, tag or GPU campaign while the contract is being frozen",
    "no multi-node or MHub enforcement",
)


def permitted_action(fault_name: str, authority: Authority) -> Action:
    """The action a fault warrants at an authority. Data lookup, no behaviour.

    Returns the fault's permitted action when the authority allows it, and
    Action.NONE when it does not. Never returns a stronger action than the fault
    warrants, and never a stronger one than the authority permits.
    """
    for fault in FAULTS:
        if fault.name != fault_name:
            continue
        required = ACTION_REQUIRES[fault.permitted_action]
        if AUTHORITY_ORDER.index(authority) >= AUTHORITY_ORDER.index(required):
            return fault.permitted_action
        return Action.NONE
    return Action.NONE


def fault(name: str) -> Optional[Fault]:
    for entry in FAULTS:
        if entry.name == name:
            return entry
    return None


def stage(order: int) -> Optional[CanaryStage]:
    for entry in CANARY_SEQUENCE:
        if entry.order == order:
            return entry
    return None
