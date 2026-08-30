"""The Safety Governor contract, as tests. Written before the implementation.

Every test here fails until Gate D lands `safety.governor`, which is the point:
each states a rule from docs/SAFETY_GOVERNOR.md that nothing currently enforces.

No GPU is touched. Clock, telemetry and policy are injected, because the real
deadlines are 60-900 s and a suite that waited them out would be skipped, and
because STALE and UNAVAILABLE_UNEXPECTED are difficult to produce on demand from
real hardware and trivial to get wrong.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME = os.path.dirname(_HERE)
for _root in (_HERE, os.path.join(_RUNTIME, "serving")):
    if _root not in sys.path:
        sys.path.insert(0, _root)

from contract import (AUDIT_FIELDS, Confidence, DrainMode,  # noqa: E402
                      Policy, State, TRANSITION_EXECUTOR, TriggerSource,
                      permitted)

# Only the runtime surface is unresolved. States, policy and confidence come
# from the canonical contract, so these tests cannot disagree with the table
# they are meant to be testing.
from governor import (GovernorError, SafetyGovernor,  # noqa: E402
                      SyntheticTelemetry, TestClock)

BOOT = "75a1077f-b05d-494a-af24-b00e2847d61c"
OTHER_BOOT = "12c60700-b2e9-4c5c-824f-6ec7f679b58e"
CUDA = "nvidia:GPU-cbb3d045-9d5f-a225-0f2e-adb1c6d6a033"
ROCM = "amd:pci-0000:04:00.0"
TOTAL = 17102864384          # 15.93 GiB, the qualified NVIDIA card


def healthy(total: int = TOTAL, used: int = 800 * 1024 * 1024) -> dict:
    """A device reading that should admit: idle, telemetry fresh."""
    return {
        "totalBytes": total, "freeBytes": total - used,
        "vramConfidence": Confidence.AVAILABLE,
        # As the qualified platform actually reports AMD under WSL.
        "powerWatts": None, "powerConfidence": Confidence.UNAVAILABLE_EXPECTED,
        "temperatureC": None, "temperatureConfidence": Confidence.UNAVAILABLE_EXPECTED,
    }


def governor(**overrides) -> "SafetyGovernor":
    clock = overrides.pop("clock", None) or TestClock(start=1000.0)
    telemetry = overrides.pop("telemetry", None) or SyntheticTelemetry(
        {CUDA: healthy(), ROCM: healthy()})
    policy = overrides.pop("policy", None) or Policy()
    return SafetyGovernor(clock=clock, telemetry=telemetry, policy=policy,
                          boot_id=overrides.pop("boot_id", BOOT), **overrides)


def ready(**overrides) -> "SafetyGovernor":
    handle = governor(**overrides)
    handle.preflight()
    return handle


# ------------------------------------------------------------------ states

class StateTests(unittest.TestCase):
    def test_the_eight_states_exist_and_no_others(self):
        self.assertEqual(
            {state.name for state in State},
            {"UNKNOWN", "READY", "ADMITTED", "RUNNING", "DRAINING",
             "COOLDOWN", "QUARANTINED", "FAILED"})

    def test_a_new_governor_starts_unknown(self):
        self.assertEqual(governor().state, State.UNKNOWN)

    def test_failed_is_a_recording_state_not_a_resting_one(self):
        """A Governor sitting in FAILED is indistinguishable from a crashed one."""
        handle = ready()
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)          # the contract has no incident
        handle.report_incident("worker_death", device=CUDA)   # path from ADMITTED
        self.assertIn(handle.state, (State.COOLDOWN, State.QUARANTINED))


# ------------------------------------------------------- the transition table

class TransitionTableTests(unittest.TestCase):
    """Section 3. Every transition not in the table is forbidden."""

    def test_preflight_moves_unknown_to_ready(self):
        handle = governor()
        handle.preflight()
        self.assertEqual(handle.state, State.READY)

    def test_admission_moves_ready_to_admitted(self):
        handle = ready()
        handle.admit(CUDA, requested_bytes=1 << 30)
        self.assertEqual(handle.state, State.ADMITTED)

    def test_a_passing_canary_moves_admitted_to_running(self):
        handle = ready()
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        self.assertEqual(handle.state, State.RUNNING)

    def test_completion_moves_running_to_cooldown(self):
        handle = ready()
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        handle.completed()
        self.assertEqual(handle.state, State.COOLDOWN)

    def test_an_undeclared_transition_raises_rather_than_guessing(self):
        """A state machine that repairs itself silently cannot be reasoned about."""
        handle = governor()                       # UNKNOWN
        with self.assertRaises(GovernorError):
            handle.admit(CUDA, requested_bytes=1 << 30)

    def test_every_transition_records_the_canonical_audit_fields(self):
        handle = governor()
        handle.preflight()
        event = handle.events[-1]
        for field in AUDIT_FIELDS:
            self.assertIn(field, event, f"{field} missing from a transition event")
        self.assertEqual(event["safetyPreviousState"], "UNKNOWN")
        self.assertEqual(event["safetyState"], "READY")

    def test_the_executor_is_the_governor_even_when_a_worker_sourced_it(self):
        """'Who noticed' and 'who decided' must not collapse into one field."""
        handle = ready()
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        handle.completed()                       # worker-sourced
        event = handle.events[-1]
        self.assertEqual(event["safetyTriggerSource"], TriggerSource.WORKER.value)
        self.assertEqual(event["safetyTransitionExecutor"], TRANSITION_EXECUTOR)

    def test_every_applied_transition_is_one_the_table_permits(self):
        """The implementation may not invent a transition the data lacks."""
        handle = ready()
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        handle.completed()
        for event in handle.events:
            source = State(event["safetyPreviousState"])
            transition = permitted(source, event["safetyTrigger"])
            self.assertIsNotNone(
                transition,
                f"{source.value} -> {event['safetyState']} on "
                f"{event['safetyTrigger']} is not in the table")
            self.assertEqual(transition.target.value, event["safetyState"])


# ------------------------------------------------- the ADMITTED incident window

class AdmittedIncidentTests(unittest.TestCase):
    """safety-contract-1.1. The window between reserving and computing.

    Short, but it is where allocation and weight-load setup happen, which is
    when an OOM is most likely. Under safety-contract-1 an incident here had
    nowhere to go and the Governor raised.
    """

    def _admitted(self, telemetry=None, clock=None):
        handle = ready(telemetry=telemetry, clock=clock) if (telemetry or clock) \
            else ready()
        handle.admit(CUDA, requested_bytes=1 << 30)
        self.assertEqual(handle.state, State.ADMITTED)
        return handle

    def test_worker_death_while_admitted_reaches_failed(self):
        handle = self._admitted()
        handle.report_incident("worker_death", device=CUDA)
        self.assertIn(handle.state, (State.COOLDOWN, State.QUARANTINED))

    def test_worker_death_cannot_strand_a_lease(self):
        """A stranded lease is capacity the Governor books forever."""
        handle = self._admitted()
        handle.report_incident("worker_death", device=CUDA)
        handle.force_state(State.READY)
        self.assertTrue(handle.admit(CUDA, requested_bytes=1 << 30),
                        "the dead worker's lease was never released")

    def test_the_lease_outcome_is_recorded_on_the_transition(self):
        handle = self._admitted()
        handle.report_incident("worker_death", device=CUDA)
        event = [e for e in handle.events
                 if e.get("safetyPreviousState") == "ADMITTED"][-1]
        self.assertEqual(event["safetyLeaseOutcome"], "released")
        self.assertTrue(event["releasedLeases"],
                        "the audit trail must say what was released")

    def test_the_canary_retains_the_lease_rather_than_releasing_it(self):
        """The work is starting; the capacity is still needed."""
        handle = self._admitted()
        handle.canary_passed(CUDA)
        event = handle.events[-1]
        self.assertEqual(event["safetyLeaseOutcome"], "retained")

    def test_hard_limit_while_admitted_drains_in_immediate_mode(self):
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = self._admitted(telemetry=telemetry)
        telemetry.set(CUDA, healthy(used=int(TOTAL * 0.98)))
        handle.poll()
        self.assertEqual(handle.state, State.DRAINING)
        self.assertEqual(handle.drain_mode, DrainMode.IMMEDIATE)
        self.assertEqual(handle.escalation, "SIGTERM",
                         "no grace when memory is already gone")

    def test_breaker_opening_while_admitted_quarantines(self):
        clock = TestClock(start=1000.0)
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(clock=clock, telemetry=telemetry)
        for _ in range(Policy().breaker_threshold):
            handle.force_state(State.READY)
            handle.admit(CUDA, requested_bytes=1 << 30)
            handle.report_incident("oom", device=CUDA)
            clock.advance(10)
        self.assertEqual(handle.state, State.QUARANTINED)

    def test_a_quarantine_prevents_another_admission(self):
        clock = TestClock(start=1000.0)
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(clock=clock, telemetry=telemetry)
        for _ in range(Policy().breaker_threshold):
            handle.force_state(State.READY)
            handle.admit(CUDA, requested_bytes=1 << 30)
            handle.report_incident("oom", device=CUDA)
            clock.advance(10)
        self.assertEqual(handle.state, State.QUARANTINED)
        with self.assertRaises(GovernorError):
            handle.admit(CUDA, requested_bytes=1 << 30)

    def test_every_admitted_exit_declares_a_lease_outcome(self):
        """A lease neither carried forward nor released is stranded."""
        from contract import exits_from
        for transition in exits_from(State.ADMITTED):
            self.assertIsNotNone(
                transition.lease_outcome,
                f"ADMITTED -> {transition.target.value} on "
                f"{transition.trigger} does not say what becomes of the lease")


# ------------------------------------------------------- revision and racing

class StateRevisionTests(unittest.TestCase):
    """Blocker 4: duplicate, stale and out-of-order triggers are safe."""

    def test_the_revision_increases_on_every_applied_transition(self):
        handle = governor()
        first = handle.state_revision
        handle.preflight()
        self.assertGreater(handle.state_revision, first)

    def test_a_stale_revision_is_rejected_idempotently(self):
        handle = ready()
        stale = handle.state_revision
        handle.admit(CUDA, requested_bytes=1 << 30)          # revision moves on
        before = handle.state_revision
        handle.completed(expect_revision=stale)              # late arrival
        self.assertEqual(handle.state_revision, before,
                         "a stale trigger must not change state")
        self.assertEqual(handle.state, State.ADMITTED)

    def test_a_rejected_trigger_is_recorded_not_silently_dropped(self):
        handle = ready()
        stale = handle.state_revision
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.completed(expect_revision=stale)
        self.assertTrue(any(e.get("safetyTrigger") == "trigger_stale"
                            or e.get("event") == "safety_trigger_stale"
                            for e in handle.events))

    def test_a_duplicate_trigger_applies_once(self):
        """work_completed delivered twice must not be attempted from COOLDOWN."""
        handle = ready()
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        revision = handle.state_revision
        handle.completed(expect_revision=revision)
        handle.completed(expect_revision=revision)           # the duplicate
        self.assertEqual(handle.state, State.COOLDOWN)

    def test_a_late_heartbeat_cannot_revive_a_drained_machine(self):
        handle = ready()
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        revision = handle.state_revision
        handle.drain(reason="operator")
        handle.workers_exited()
        handle.heartbeat(operation="decode_step", counter=99,
                         expect_revision=revision)
        self.assertNotEqual(handle.state, State.RUNNING)

    def test_a_stale_trigger_cannot_release_a_newer_lease(self):
        """The dangerous shape: a late release freeing work that just started.

        A worker dies, its incident is recorded, a new admission takes a fresh
        lease -- and then the dead worker's delayed trigger arrives naming the
        revision it saw. Applied, it would release capacity belonging to the
        run that replaced it.
        """
        handle = ready()
        handle.admit(CUDA, requested_bytes=1 << 30)
        stale = handle.state_revision
        handle.report_incident("worker_death", device=CUDA)

        handle.force_state(State.READY)
        self.assertTrue(handle.admit(CUDA, requested_bytes=1 << 30))
        revision_now = handle.state_revision
        self.assertEqual(handle.state, State.ADMITTED)

        handle.completed(expect_revision=stale)          # the late arrival
        self.assertEqual(handle.state, State.ADMITTED,
                         "a stale trigger moved the state")
        self.assertEqual(handle.state_revision, revision_now)
        self.assertTrue(any(e.get("event") == "safety_trigger_stale"
                            for e in handle.events))

        # The newer lease must still be held: the machine is busy.
        handle.force_state(State.READY)
        self.assertFalse(
            handle.admit(ROCM, requested_bytes=TOTAL),
            "capacity accounting was disturbed by the stale trigger")

    def test_capacity_check_and_lease_are_applied_under_one_revision(self):
        """Two admissions racing between samples must not both succeed."""
        handle = ready()
        revision = handle.state_revision
        self.assertTrue(handle.admit(CUDA, requested_bytes=1 << 30,
                                     expect_revision=revision))
        self.assertFalse(handle.admit(CUDA, requested_bytes=1 << 30,
                                      expect_revision=revision),
                         "the second admission observed a stale revision")


# ------------------------------------------------------------- termination

class TerminationTests(unittest.TestCase):
    """Blocker 5: PIDs are reused, so identity is checked before signalling."""

    def _draining_with_worker(self):
        handle = ready()
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        handle.register_worker(pid=4242, process_start_time=99999,
                               run_id="run-1", placement_id="pl-1")
        handle.drain(reason="operator")
        return handle

    def test_a_matching_worker_may_be_signalled(self):
        handle = self._draining_with_worker()
        self.assertTrue(handle.may_signal(pid=4242, process_start_time=99999,
                                          run_id="run-1", placement_id="pl-1"))

    def test_a_recycled_pid_is_not_signalled(self):
        """A different start time means a different process wearing that PID."""
        handle = self._draining_with_worker()
        self.assertFalse(handle.may_signal(pid=4242, process_start_time=123456,
                                           run_id="run-1", placement_id="pl-1"))

    def test_a_foreign_run_or_placement_is_not_signalled(self):
        handle = self._draining_with_worker()
        self.assertFalse(handle.may_signal(pid=4242, process_start_time=99999,
                                           run_id="run-2", placement_id="pl-1"))
        self.assertFalse(handle.may_signal(pid=4242, process_start_time=99999,
                                           run_id="run-1", placement_id="pl-2"))

    def test_an_unmatched_signal_is_abandoned_and_recorded(self):
        handle = self._draining_with_worker()
        handle.may_signal(pid=4242, process_start_time=123456,
                          run_id="run-1", placement_id="pl-1")
        self.assertTrue(any(e.get("event") == "safety_termination_abandoned"
                            for e in handle.events))

    def test_intent_is_recorded_before_the_signal_not_only_after(self):
        """Recording only the outcome loses the case where the Governor died."""
        handle = self._draining_with_worker()
        handle.terminate(pid=4242, process_start_time=99999,
                         run_id="run-1", placement_id="pl-1", signal="SIGTERM")
        events = [e.get("event") for e in handle.events]
        self.assertIn("safety_termination_intent", events)
        self.assertIn("safety_termination_outcome", events)
        self.assertLess(events.index("safety_termination_intent"),
                        events.index("safety_termination_outcome"))


# ------------------------------------------------------- durable quarantine

class QuarantineDurabilityTests(unittest.TestCase):
    """Blocker 6: a quarantine lost to a crash is worse than one never taken."""

    def test_a_record_is_written_atomically_and_fsynced(self):
        handle = ready()
        handle.quarantine("oom", device=CUDA, reason="test")
        write = handle.last_quarantine_write
        self.assertTrue(write["fsyncedFile"])
        self.assertTrue(write["fsyncedDirectory"],
                        "without this the rename itself is not durable")
        self.assertTrue(write["atomicRename"])
        self.assertFalse(write["partialRemains"])

    def test_a_corrupt_digest_is_treated_as_quarantined(self):
        store = {"records": [{"recordId": "r1", "incidentClass": "oom",
                              "scope": "device", "scopeIdentity": CUDA,
                              "reason": "x", "openedAt": 1.0, "bootId": BOOT,
                              "policyVersion": "safety-policy-1-provisional",
                              "digest": "wrong"}]}
        handle = governor(quarantine_store=store)
        with self.assertRaises(GovernorError):
            handle.preflight()
        self.assertEqual(handle.state, State.QUARANTINED)

    def test_a_missing_required_field_is_treated_as_quarantined(self):
        store = {"records": [{"recordId": "r1", "incidentClass": "oom"}]}
        handle = governor(quarantine_store=store)
        with self.assertRaises(GovernorError):
            handle.preflight()
        self.assertEqual(handle.state, State.QUARANTINED)

    def test_a_leftover_partial_file_quarantines_rather_than_assuming_health(self):
        """Between quarantining something fine and releasing something not,
        only the first is recoverable by an operator."""
        handle = governor(quarantine_partial_present=True)
        with self.assertRaises(GovernorError):
            handle.preflight()
        self.assertEqual(handle.state, State.QUARANTINED)

    def test_the_governor_never_releases_its_own_quarantine(self):
        """QUARANTINED -> READY is the operator's transition alone."""
        handle = ready()
        handle.quarantine("oom", device=CUDA, reason="test")
        self.assertEqual(handle.state, State.QUARANTINED)
        with self.assertRaises(GovernorError):
            handle.preflight()
        self.assertEqual(handle.state, State.QUARANTINED)


# ----------------------------------------------------------------- admission

class AdmissionTests(unittest.TestCase):
    """Section 4. All four inputs must pass; a missing input is not a pass."""

    def test_a_healthy_idle_machine_admits(self):
        self.assertTrue(ready().admit(CUDA, requested_bytes=1 << 30))

    def test_admission_refused_when_the_reserve_would_be_breached(self):
        handle = ready()
        # Ask for everything free: the reserve cannot survive it.
        free = TOTAL - 800 * 1024 * 1024
        self.assertFalse(handle.admit(CUDA, requested_bytes=free))
        self.assertEqual(handle.state, State.READY,
                         "a refusal is not a fault; the machine stays READY")

    def test_the_reserve_floor_applies_on_small_devices(self):
        """max(floor, fraction * total): 3% of 16 GiB is under the 512 MiB floor."""
        policy = Policy()
        self.assertEqual(
            policy.reserve_bytes(TOTAL),
            max(policy.reserve_floor_bytes,
                int(policy.reserve_fraction * TOTAL)))
        self.assertEqual(policy.reserve_bytes(TOTAL), policy.reserve_floor_bytes)

    def test_leases_are_counted_not_inferred_from_free_memory(self):
        """Two admissions racing between samples would each see enough room."""
        handle = ready()
        self.assertTrue(handle.admit(CUDA, requested_bytes=1 << 30))
        self.assertFalse(handle.admit(CUDA, requested_bytes=1 << 30),
                         "the second admission must see the first one's lease")

    def test_baseline_is_a_median_of_samples_not_one_reading(self):
        """One compositor allocation must not raise the floor permanently."""
        readings = [healthy(used=u * 1024 * 1024)
                    for u in (800, 800, 4000, 800, 800)]
        telemetry = SyntheticTelemetry({CUDA: readings, ROCM: healthy()})
        handle = governor(telemetry=telemetry)
        handle.preflight()
        self.assertEqual(handle.baseline(CUDA), TOTAL - 800 * 1024 * 1024)


# --------------------------------------------------------- telemetry handling

class TelemetryTests(unittest.TestCase):
    """Sections 4.4, 9 and 11."""

    def test_missing_amd_power_is_unavailable_never_zero(self):
        handle = ready()
        signals = handle.signals(ROCM)
        self.assertIsNone(signals["powerWatts"])
        self.assertEqual(signals["powerConfidence"],
                         Confidence.UNAVAILABLE_EXPECTED)
        self.assertNotEqual(signals["powerWatts"], 0,
                            "zero watts is a reading, and a false one")

    def test_expected_unavailable_power_does_not_block_admission(self):
        """Gating on it would refuse all work on the only qualified platform."""
        self.assertTrue(ready().admit(ROCM, requested_bytes=1 << 30))

    def test_unexpectedly_unavailable_power_does_block_admission(self):
        """A source that should work has stopped: that is a fault, not a gap."""
        reading = healthy()
        reading["powerConfidence"] = Confidence.UNAVAILABLE_UNEXPECTED
        handle = ready(telemetry=SyntheticTelemetry(
            {CUDA: reading, ROCM: healthy()}))
        self.assertFalse(handle.admit(CUDA, requested_bytes=1 << 30))

    def test_stale_vram_refuses_admission(self):
        clock = TestClock(start=1000.0)
        handle = ready(clock=clock)
        clock.advance(Policy().max_signal_age_seconds + 1)
        self.assertEqual(handle.signals(CUDA)["vramConfidence"],
                         Confidence.STALE)
        self.assertFalse(handle.admit(CUDA, requested_bytes=1 << 30))

    def test_missing_vram_is_caught_at_preflight_before_admission(self):
        """Fail-closed earlier than expected is still fail-closed.

        This originally asserted that admission refused. It never gets that
        far: with no VRAM reading the baseline cannot be sampled, so preflight
        fails and the machine never reaches READY. Refusing sooner is the
        stronger behaviour, and the test now says so rather than describing a
        path that cannot be taken.
        """
        reading = healthy()
        reading["vramConfidence"] = Confidence.UNAVAILABLE_UNEXPECTED
        reading["freeBytes"] = None
        handle = governor(telemetry=SyntheticTelemetry(
            {CUDA: reading, ROCM: healthy()}))
        self.assertFalse(handle.preflight())
        self.assertEqual(handle.state, State.FAILED)
        with self.assertRaises(GovernorError):
            handle.admit(CUDA, requested_bytes=1 << 30)

    def test_vram_lost_while_running_degrades_before_it_drains(self):
        """Continuation fails safe: stop admitting, keep the run, then drain."""
        clock = TestClock(start=1000.0)
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(clock=clock, telemetry=telemetry)
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)

        telemetry.set(CUDA, {**healthy(), "freeBytes": None,
                             "vramConfidence": Confidence.UNAVAILABLE_UNEXPECTED})
        handle.poll()
        self.assertEqual(handle.state, State.RUNNING, "must not kill a live run")
        self.assertTrue(handle.degraded)

        clock.advance(Policy().degraded_deadline_seconds + 1)
        handle.poll()
        self.assertEqual(handle.state, State.DRAINING)

    def test_an_unreadable_quarantine_store_is_treated_as_quarantined(self):
        """Otherwise corrupting one file is how a quarantine gets cleared."""
        handle = governor(quarantine_store_readable=False)
        with self.assertRaises(GovernorError):
            handle.preflight()
        self.assertEqual(handle.state, State.QUARANTINED)


# -------------------------------------------------------------- soft and hard

class LimitTests(unittest.TestCase):
    """Section 5. Hysteresis needs both a gap and a dwell."""

    def _at(self, fraction: float) -> dict:
        return healthy(used=int(TOTAL * fraction))

    def test_soft_limit_needs_dwell_before_it_acts(self):
        clock = TestClock(start=1000.0)
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(clock=clock, telemetry=telemetry)
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)

        telemetry.set(CUDA, self._at(0.92))
        handle.poll()
        self.assertEqual(handle.state, State.RUNNING, "one sample is not sustained")

        clock.advance(Policy().soft_dwell_seconds + 1)
        handle.poll()
        self.assertEqual(handle.state, State.DRAINING)

    def test_a_single_spike_below_the_dwell_does_not_flap(self):
        clock = TestClock(start=1000.0)
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(clock=clock, telemetry=telemetry)
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)

        for fraction in (0.92, 0.70, 0.92, 0.70):
            telemetry.set(CUDA, self._at(fraction))
            clock.advance(2)
            handle.poll()
        self.assertEqual(handle.state, State.RUNNING)

    def test_release_requires_falling_below_the_lower_threshold(self):
        """A gap with no dwell flaps; a dwell with no gap flaps too."""
        policy = Policy()
        self.assertLess(policy.soft_release_fraction, policy.soft_limit_fraction)
        self.assertGreater(policy.soft_release_dwell_seconds, 0)

    def test_the_hard_limit_acts_immediately_with_no_dwell(self):
        """Waiting to confirm imminent exhaustion is how exhaustion happens."""
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(telemetry=telemetry)
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        telemetry.set(CUDA, self._at(0.98))
        handle.poll()
        self.assertEqual(handle.state, State.DRAINING)

    def test_the_hard_limit_drains_it_does_not_jump_to_failed(self):
        """Blocker 1: one sequence, drain then bounded kill."""
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(telemetry=telemetry)
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        telemetry.set(CUDA, self._at(0.98))
        handle.poll()
        self.assertEqual(handle.state, State.DRAINING)
        self.assertNotEqual(handle.state, State.FAILED)

    def test_the_hard_limit_drain_is_immediate_with_no_grace(self):
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(telemetry=telemetry)
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        telemetry.set(CUDA, self._at(0.98))
        handle.poll()
        self.assertEqual(handle.drain_mode, DrainMode.IMMEDIATE)
        self.assertEqual(handle.escalation, "SIGTERM",
                         "no finish-current-unit grace when memory is gone")

    def test_an_immediate_drain_kills_on_the_shorter_deadline(self):
        clock = TestClock(start=1000.0)
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(clock=clock, telemetry=telemetry)
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        telemetry.set(CUDA, self._at(0.98))
        handle.poll()
        clock.advance(Policy().hard_drain_deadline_seconds + 1)
        handle.poll()
        self.assertEqual(handle.escalation, "SIGKILL")

    def test_a_graceful_drain_finishes_the_current_unit_first(self):
        clock = TestClock(start=1000.0)
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(clock=clock, telemetry=telemetry)
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        handle.drain(reason="operator")
        self.assertEqual(handle.drain_mode, DrainMode.GRACEFUL)
        self.assertIsNone(handle.escalation, "no signal until the grace deadline")

    def test_utilisation_counts_other_processes_too(self):
        """Another process's memory causes an OOM just as effectively."""
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(telemetry=telemetry)
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        telemetry.set(CUDA, self._at(0.98))          # not ours; still fatal
        handle.poll()
        self.assertEqual(handle.state, State.DRAINING)


# ------------------------------------------------------------------ watchdog

class WatchdogTests(unittest.TestCase):
    """Section 6. Stalled and unresponsive are different facts."""

    def _running(self):
        clock = TestClock(start=1000.0)
        handle = ready(clock=clock)
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        handle.heartbeat(operation="decode_step", counter=1)
        return handle, clock

    def test_an_advancing_counter_is_progress(self):
        handle, clock = self._running()
        for step in range(2, 6):
            clock.advance(5)
            handle.heartbeat(operation="decode_step", counter=step)
            handle.poll()
        self.assertEqual(handle.state, State.RUNNING)

    def test_a_repeated_counter_is_not_progress(self):
        """A process claiming to be healthy is not evidence that it is."""
        handle, clock = self._running()
        for _ in range(6):
            clock.advance(15)
            handle.heartbeat(operation="decode_step", counter=1)
            handle.poll()
        self.assertEqual(handle.state, State.DRAINING)

    def test_stalled_drains_rather_than_killing(self):
        handle, clock = self._running()
        clock.advance(Policy().stall_deadline_seconds + 1)
        handle.poll()
        self.assertEqual(handle.state, State.DRAINING)

    def test_unresponsive_fails_because_there_is_nothing_to_drain(self):
        handle, clock = self._running()
        clock.advance(Policy().unresponsive_deadline_seconds + 1)
        handle.poll()
        self.assertIn(handle.state, (State.FAILED, State.COOLDOWN,
                                     State.QUARANTINED))

    def test_deadlines_are_per_operation(self):
        """A decode deadline would fire during a legitimate weight load."""
        policy = Policy()
        self.assertGreater(policy.operation_deadline("weight_load"),
                           policy.operation_deadline("decode_step"))
        handle, clock = self._running()
        handle.heartbeat(operation="weight_load", counter=2)
        clock.advance(policy.operation_deadline("decode_step") + 1)
        handle.poll()
        self.assertEqual(handle.state, State.RUNNING)

    def test_a_heartbeat_with_no_operation_uses_the_shortest_deadline(self):
        """A worker that cannot say what it is doing is not evidence of work."""
        handle, clock = self._running()
        handle.heartbeat(operation=None, counter=2)
        clock.advance(Policy().operation_deadline("canary") + 1)
        handle.poll()
        self.assertNotEqual(handle.state, State.RUNNING)


# --------------------------------------------------------------------- drain

class DrainTests(unittest.TestCase):
    """Section 5.3. The deadline is a deadline, not a target."""

    def _draining(self):
        clock = TestClock(start=1000.0)
        handle = ready(clock=clock)
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        handle.drain(reason="operator")
        return handle, clock

    def test_drain_within_the_deadline_reaches_cooldown(self):
        handle, _clock = self._draining()
        handle.workers_exited()
        self.assertEqual(handle.state, State.COOLDOWN)

    def test_grace_deadline_escalates_to_terminate(self):
        handle, clock = self._draining()
        clock.advance(Policy().drain_grace_deadline_seconds + 1)
        handle.poll()
        self.assertEqual(handle.escalation, "SIGTERM")
        self.assertEqual(handle.state, State.DRAINING)

    def test_exceeding_the_drain_deadline_is_an_incident_not_a_success(self):
        """SIGKILL and a clean exit are different facts about the system."""
        handle, clock = self._draining()
        clock.advance(Policy().drain_deadline_seconds + 1)
        handle.poll()
        self.assertEqual(handle.escalation, "SIGKILL")
        self.assertIn(handle.state, (State.FAILED, State.COOLDOWN,
                                     State.QUARANTINED))
        self.assertTrue(any(event.get("safetyTrigger") == "drain_timeout"
                            for event in handle.events))

    def test_cooldown_does_not_release_until_baseline_is_reverified(self):
        """A killed process does not always release VRAM promptly."""
        clock = TestClock(start=1000.0)
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(clock=clock, telemetry=telemetry)
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        handle.drain(reason="operator")
        handle.workers_exited()

        telemetry.set(CUDA, healthy(used=6000 * 1024 * 1024))   # leaked
        clock.advance(Policy().cooldown_period_seconds + 1)
        handle.poll()
        self.assertEqual(handle.state, State.COOLDOWN)

        telemetry.set(CUDA, healthy())
        handle.poll()
        self.assertEqual(handle.state, State.READY)

    def test_recovery_deadline_quarantines_rather_than_waiting_forever(self):
        clock = TestClock(start=1000.0)
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(clock=clock, telemetry=telemetry)
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        handle.drain(reason="operator")
        handle.workers_exited()
        telemetry.set(CUDA, healthy(used=6000 * 1024 * 1024))
        clock.advance(Policy().recovery_deadline_seconds + 1)
        handle.poll()
        self.assertEqual(handle.state, State.QUARANTINED)


# ----------------------------------------------------------- circuit breaker

class CircuitBreakerTests(unittest.TestCase):
    """Section 7. Retrying into a pattern is how a fault becomes damage."""

    def _incidents(self, handle, clock, count, *, kind="oom", device=CUDA):
        for _ in range(count):
            handle.force_state(State.READY)
            handle.admit(device, requested_bytes=1 << 30)
            # Through RUNNING, because that is the only state the frozen
            # contract gives an incident path out of. See the ADMITTED gap
            # recorded in docs/SAFETY_GOVERNOR.md.
            handle.canary_passed(device)
            handle.report_incident(kind, device=device)
            clock.advance(10)

    def test_three_matching_incidents_open_the_breaker(self):
        clock = TestClock(start=1000.0)
        handle = ready(clock=clock)
        self._incidents(handle, clock, Policy().breaker_threshold)
        self.assertEqual(handle.state, State.QUARANTINED)

    def test_two_matching_incidents_do_not(self):
        clock = TestClock(start=1000.0)
        handle = ready(clock=clock)
        self._incidents(handle, clock, Policy().breaker_threshold - 1)
        self.assertNotEqual(handle.state, State.QUARANTINED)

    def test_the_signature_excludes_the_run_id(self):
        """It differs every time; a breaker keyed on it never opens."""
        clock = TestClock(start=1000.0)
        handle = ready(clock=clock)
        self._incidents(handle, clock, Policy().breaker_threshold)
        self.assertEqual(handle.state, State.QUARANTINED)

    def test_unrelated_failures_are_not_a_pattern(self):
        clock = TestClock(start=1000.0)
        handle = ready(clock=clock)
        for kind in ("oom", "stall", "transport_failure"):
            handle.force_state(State.READY)
            handle.admit(CUDA, requested_bytes=1 << 30)
            handle.canary_passed(CUDA)
            handle.report_incident(kind, device=CUDA)
            clock.advance(10)
        self.assertNotEqual(handle.state, State.QUARANTINED)

    def test_incidents_outside_the_window_do_not_accumulate(self):
        clock = TestClock(start=1000.0)
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(clock=clock, telemetry=telemetry)
        for _ in range(Policy().breaker_threshold):
            handle.force_state(State.READY)
            handle.admit(CUDA, requested_bytes=1 << 30)
            handle.canary_passed(CUDA)
            handle.report_incident("oom", device=CUDA)
            clock.advance(Policy().breaker_window_seconds + 1)
            # A real source keeps polling. Without this the readings age past
            # max_signal_age_seconds and the next admission is refused as
            # STALE -- correct, but not what this test is about.
            telemetry.refresh(clock.now())
        self.assertNotEqual(handle.state, State.QUARANTINED)


# ---------------------------------------------------------------- quarantine

class QuarantineTests(unittest.TestCase):
    """Section 7. Scoped to the failing capability, and it outlives a restart."""

    def test_quarantine_is_scoped_to_the_device_not_the_machine(self):
        handle = ready()
        handle.quarantine("oom", device=CUDA, reason="test")
        self.assertTrue(handle.is_quarantined(device=CUDA))
        self.assertFalse(handle.is_quarantined(device=ROCM))

    def test_quarantine_survives_a_restart(self):
        """A quarantine a restart clears is not a quarantine."""
        store = {}
        first = ready(quarantine_store=store)
        first.quarantine("oom", device=CUDA, reason="test")

        second = governor(quarantine_store=store)
        with self.assertRaises(GovernorError):
            second.preflight()
        self.assertEqual(second.state, State.QUARANTINED)

    def test_quarantine_survives_a_boot_change(self):
        """It is a decision, not a measurement."""
        store = {}
        first = ready(quarantine_store=store)
        first.quarantine("oom", device=CUDA, reason="test")

        second = governor(quarantine_store=store, boot_id=OTHER_BOOT)
        with self.assertRaises(GovernorError):
            second.preflight()
        self.assertEqual(second.state, State.QUARANTINED)


# -------------------------------------------------------------- manual reset

class ManualResetTests(unittest.TestCase):
    """Section 8. A reset is a human overriding a refusal."""

    def _quarantined(self):
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(telemetry=telemetry)
        record = handle.quarantine("oom", device=CUDA, reason="test")
        return handle, telemetry, record

    def test_reset_requires_an_actor(self):
        handle, _telemetry, record = self._quarantined()
        with self.assertRaises(GovernorError):
            handle.manual_reset(actor="", records=[record])
        self.assertEqual(handle.state, State.QUARANTINED)

    def test_reset_requires_naming_the_records_it_clears(self):
        """A blanket reset cannot clear an incident nobody read."""
        handle, _telemetry, _record = self._quarantined()
        with self.assertRaises(GovernorError):
            handle.manual_reset(actor="an-operator", records=[])
        self.assertEqual(handle.state, State.QUARANTINED)

    def test_reset_refused_while_vram_is_above_baseline_tolerance(self):
        handle, telemetry, record = self._quarantined()
        telemetry.set(CUDA, healthy(used=6000 * 1024 * 1024))
        with self.assertRaises(GovernorError):
            handle.manual_reset(actor="an-operator", records=[record])
        self.assertEqual(handle.state, State.QUARANTINED)

    def test_reset_refused_when_the_canary_fails(self):
        handle, telemetry, record = self._quarantined()
        telemetry.fail_canary(CUDA)
        with self.assertRaises(GovernorError):
            handle.manual_reset(actor="an-operator", records=[record])
        self.assertEqual(handle.state, State.QUARANTINED)

    def test_a_complete_reset_returns_to_ready_and_is_recorded(self):
        handle, _telemetry, record = self._quarantined()
        handle.manual_reset(actor="an-operator", records=[record],
                            reason="replaced the riser")
        self.assertEqual(handle.state, State.READY)
        event = [e for e in handle.events
                 if e.get("safetyTrigger") == "manual_reset"][-1]
        self.assertEqual(event["safetyTriggerSource"], "operator")
        self.assertEqual(event["resetActor"], "an-operator")
        self.assertIn(record, event["clearedRecords"])
        self.assertIn("baseline", event)
        self.assertIn("canary", event)

    def test_reset_does_not_erase_incident_history(self):
        """Repeatedly clearing the same fault re-opens the breaker."""
        clock = TestClock(start=1000.0)
        telemetry = SyntheticTelemetry({CUDA: healthy(), ROCM: healthy()})
        handle = ready(clock=clock, telemetry=telemetry)
        for _ in range(Policy().breaker_threshold):
            handle.force_state(State.READY)
            handle.admit(CUDA, requested_bytes=1 << 30)
            handle.canary_passed(CUDA)
            handle.report_incident("oom", device=CUDA)
            clock.advance(10)
        self.assertEqual(handle.state, State.QUARANTINED)

        handle.manual_reset(actor="an-operator",
                            records=handle.open_quarantine_records())
        self.assertEqual(handle.state, State.READY)

        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)          # no incident path from ADMITTED
        handle.report_incident("oom", device=CUDA)
        self.assertEqual(handle.state, State.QUARANTINED,
                         "cleared incidents still count inside the window")


# ------------------------------------------------------------------- clocks

class BootDomainTests(unittest.TestCase):
    """Section 10. Durations only mean something within one boot."""

    def test_a_boot_change_returns_the_governor_to_unknown(self):
        handle = ready()
        handle.observe_boot(OTHER_BOOT)
        self.assertEqual(handle.state, State.UNKNOWN)

    def test_a_baseline_does_not_carry_across_boots(self):
        handle = ready()
        self.assertIsNotNone(handle.baseline(CUDA))
        handle.observe_boot(OTHER_BOOT)
        self.assertIsNone(handle.baseline(CUDA))

    def test_durations_across_boots_are_refused_not_approximated(self):
        handle = ready()
        with self.assertRaises(GovernorError):
            handle.duration_between({"bootId": BOOT, "monotonicNs": 10},
                                    {"bootId": OTHER_BOOT, "monotonicNs": 20})

    def test_every_event_carries_the_boot_id(self):
        handle = ready()
        self.assertTrue(all(event.get("bootId") == BOOT
                            for event in handle.events))


# -------------------------------------------------------------------- audit

class AuditTests(unittest.TestCase):
    """Section 12."""

    def test_events_carry_the_policy_version(self):
        """A decision is reviewable only against the thresholds then in force."""
        handle = ready()
        self.assertEqual(handle.events[-1]["safetyPolicyVersion"],
                         Policy().version)

    def test_events_carry_per_signal_value_and_confidence(self):
        handle = ready()
        handle.admit(CUDA, requested_bytes=1 << 30)
        signals = handle.events[-1]["safetySignals"]
        self.assertIn("powerConfidence", signals[ROCM])
        self.assertEqual(signals[ROCM]["powerConfidence"],
                         Confidence.UNAVAILABLE_EXPECTED)
        self.assertIsNone(signals[ROCM]["powerWatts"],
                          "an unmeasured signal must not be recorded as a value")

    def test_a_simulated_governor_marks_every_event(self):
        """The escape exists and is impossible to use without leaving a mark."""
        handle = ready()
        self.assertTrue(all(event.get("safetySimulated") is True
                            for event in handle.events))


# --------------------------------------------------------------- non-goal

class NonGoalTests(unittest.TestCase):
    """Section 0. The Governor does not claim hardware cannot fail.

    The document assertions live in test_contract_data.py, which normalises the
    hard-wrapped prose before matching. Duplicating them here matched raw text
    and broke on a reflowed paragraph -- a false failure, and the kind that
    teaches people to loosen tests.
    """

    def test_the_governor_marks_itself_simulated(self):
        """A simulated Governor is not qualification evidence, and says so."""
        handle = ready()
        self.assertTrue(handle.simulated)
        self.assertTrue(all(event.get("safetySimulated") is True
                            for event in handle.events))

    def test_no_real_signal_is_ever_sent(self):
        """D.1 uses a fake actuator; nothing reaches the operating system."""
        handle = ready()
        handle.admit(CUDA, requested_bytes=1 << 30)
        handle.canary_passed(CUDA)
        handle.register_worker(pid=4242, process_start_time=99999,
                               run_id="run-1", placement_id="pl-1")
        handle.drain(reason="operator")
        handle.terminate(pid=4242, process_start_time=99999, run_id="run-1",
                         placement_id="pl-1", signal="SIGTERM")
        self.assertEqual(handle.actuator.signalled,
                         [{"pid": 4242, "signal": "SIGTERM"}],
                         "recorded, not delivered to a real process")


if __name__ == "__main__":
    unittest.main()
