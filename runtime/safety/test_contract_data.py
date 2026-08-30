"""The contract's own consistency. These pass now; no implementation needed.

Split from the behaviour tests deliberately. This file asserts that the
canonical table is coherent and that the prose agrees with it -- questions
answerable today. test_governor_contract.py asserts what an implementation must
do, and stays red until Gate D.

The point of the split is that "the contract contradicts itself" and "the
implementation is missing" are different failures and should not arrive as one
red block.
"""

from __future__ import annotations

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from contract import (ADVISORY_ONLY_SIGNALS, AUDIT_FIELDS,  # noqa: E402
                      CONTRACT_VERSION, INCIDENT_CLASSES, OPERATION_DEADLINES,
                      QUARANTINE_RECORD_FIELDS, QUARANTINE_SCOPES,
                      SIGNAL_RULES, TRANSITION_EXECUTOR, TRANSITIONS,
                      Confidence, Disposition, DrainMode, Policy, State,
                      TriggerSource, permitted, signal_rule, triggers_from)

DOC = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "docs",
                   "SAFETY_GOVERNOR.md")


class TableCoherenceTests(unittest.TestCase):
    """The table must not contradict itself."""

    def test_the_eight_states_and_no_others(self):
        self.assertEqual(
            {state.value for state in State},
            {"UNKNOWN", "READY", "ADMITTED", "RUNNING", "DRAINING",
             "COOLDOWN", "QUARANTINED", "FAILED"})

    def test_every_transition_uses_declared_states(self):
        for transition in TRANSITIONS:
            self.assertIsInstance(transition.source, State)
            self.assertIsInstance(transition.target, State)

    def test_no_state_and_trigger_pair_appears_twice(self):
        """Two rows for one (state, trigger) is an ambiguous machine."""
        seen = {}
        for transition in TRANSITIONS:
            self.assertNotIn(
                transition.key, seen,
                f"{transition.key} maps to both {seen.get(transition.key)} "
                f"and {transition.target}")
            seen[transition.key] = transition.target

    def test_every_state_is_reachable_from_unknown(self):
        reachable, frontier = {State.UNKNOWN}, [State.UNKNOWN]
        while frontier:
            current = frontier.pop()
            for transition in TRANSITIONS:
                if transition.source == current and transition.target not in reachable:
                    reachable.add(transition.target)
                    frontier.append(transition.target)
        self.assertEqual(reachable, set(State),
                         f"unreachable: {set(State) - reachable}")

    def test_every_state_has_an_exit_except_none(self):
        """A state with no exit is a hang, whatever the prose says."""
        for state in State:
            self.assertTrue(triggers_from(state),
                            f"{state.value} has no outgoing transition")

    def test_failed_is_a_recording_state_not_a_resting_one(self):
        targets = {t.target for t in TRANSITIONS if t.source == State.FAILED}
        self.assertTrue(targets)
        self.assertNotIn(State.FAILED, targets)

    def test_forbidden_transitions_resolve_to_none(self):
        self.assertIsNone(permitted(State.UNKNOWN, "admission_granted"))
        self.assertIsNone(permitted(State.QUARANTINED, "preflight_passed"))


class TransitionAuthorityTests(unittest.TestCase):
    """Blocker 3: only the Governor mutates state."""

    def test_the_executor_is_always_the_governor(self):
        self.assertEqual(TRANSITION_EXECUTOR, "governor")

    def test_trigger_source_is_recorded_separately_from_the_executor(self):
        """'Who noticed' and 'who decided' must not collapse into one field."""
        self.assertIn("safetyTriggerSource", AUDIT_FIELDS)
        self.assertIn("safetyTransitionExecutor", AUDIT_FIELDS)

    def test_workers_and_operators_only_source_triggers(self):
        for transition in TRANSITIONS:
            self.assertIsInstance(transition.trigger_source, TriggerSource)

    def test_only_the_operator_can_source_a_quarantine_release(self):
        release = [t for t in TRANSITIONS
                   if t.source == State.QUARANTINED and t.target == State.READY]
        self.assertEqual(len(release), 1)
        self.assertEqual(release[0].trigger_source, TriggerSource.OPERATOR)

    def test_the_governor_cannot_source_its_own_quarantine_release(self):
        for transition in TRANSITIONS:
            if transition.source == State.QUARANTINED:
                self.assertNotEqual(transition.trigger_source,
                                    TriggerSource.GOVERNOR)


class HardLimitTests(unittest.TestCase):
    """Blocker 1: one sequence for exhaustion, not two."""

    def test_the_hard_limit_drains_it_does_not_jump_to_failed(self):
        transition = permitted(State.RUNNING, "hard_limit_breached")
        self.assertIsNotNone(transition)
        self.assertEqual(transition.target, State.DRAINING)

    def test_the_hard_limit_drain_is_immediate_with_no_grace(self):
        transition = permitted(State.RUNNING, "hard_limit_breached")
        self.assertEqual(transition.drain_mode, DrainMode.IMMEDIATE)

    def test_the_soft_limit_drain_is_graceful(self):
        transition = permitted(State.RUNNING, "soft_limit_sustained")
        self.assertEqual(transition.drain_mode, DrainMode.GRACEFUL)

    def test_an_immediate_drain_kills_sooner_than_a_graceful_one(self):
        policy = Policy()
        self.assertLess(policy.hard_drain_deadline_seconds,
                        policy.drain_deadline_seconds)

    def test_running_to_failed_is_reserved_for_nothing_left_to_drain(self):
        triggers = {t.trigger for t in TRANSITIONS
                    if t.source == State.RUNNING and t.target == State.FAILED}
        self.assertEqual(triggers, {"worker_died", "unresponsive"})


class DeadlineTests(unittest.TestCase):
    """Blocker 2: one named value for the canary, everywhere."""

    def test_the_canary_deadline_has_a_single_value(self):
        policy = Policy()
        self.assertEqual(OPERATION_DEADLINES["canary"],
                         policy.canary_deadline_seconds)

    def test_the_admitted_to_running_transition_uses_that_same_name(self):
        transition = permitted(State.ADMITTED, "canary_passed")
        self.assertEqual(transition.timeout_key, "canary_deadline_seconds")

    def test_the_canary_failure_transition_uses_it_too(self):
        transition = permitted(State.ADMITTED, "canary_failed")
        self.assertEqual(transition.timeout_key, "canary_deadline_seconds")

    def test_every_timeout_key_names_a_real_policy_field(self):
        policy = Policy()
        for transition in TRANSITIONS:
            if transition.timeout_key:
                self.assertTrue(
                    hasattr(policy, transition.timeout_key),
                    f"{transition.trigger} names a missing policy field "
                    f"{transition.timeout_key!r}")

    def test_operation_deadlines_are_ordered_sensibly(self):
        """A decode deadline would fire during a legitimate weight load."""
        self.assertGreater(OPERATION_DEADLINES["weight_load"],
                           OPERATION_DEADLINES["prefill"])
        self.assertGreater(OPERATION_DEADLINES["prefill"],
                           OPERATION_DEADLINES["decode_step"])

    def test_an_undeclared_operation_gets_the_shortest_deadline(self):
        policy = Policy()
        self.assertEqual(policy.operation_deadline(None),
                         min(OPERATION_DEADLINES.values()))
        self.assertEqual(policy.operation_deadline("something_invented"),
                         min(OPERATION_DEADLINES.values()))


class HysteresisTests(unittest.TestCase):
    def test_release_is_below_enter_and_both_edges_dwell(self):
        """A gap alone flaps, and a dwell alone flaps. Both are required."""
        policy = Policy()
        self.assertLess(policy.soft_release_fraction, policy.soft_limit_fraction)
        self.assertGreater(policy.soft_dwell_seconds, 0)
        self.assertGreater(policy.soft_release_dwell_seconds, 0)

    def test_the_hard_limit_sits_above_the_soft_limit(self):
        policy = Policy()
        self.assertGreater(policy.hard_limit_fraction, policy.soft_limit_fraction)


class SignalRuleTests(unittest.TestCase):
    """Blocker 7: the claim boundary is data, not a sentence to remember."""

    def test_every_rule_uses_declared_dispositions(self):
        for rule in SIGNAL_RULES:
            self.assertIsInstance(rule.at_admission, Disposition)
            self.assertIsInstance(rule.while_running, Disposition)

    def test_power_and_temperature_are_advisory_only_in_policy_v1(self):
        self.assertIn("power", ADVISORY_ONLY_SIGNALS)
        self.assertIn("temperature", ADVISORY_ONLY_SIGNALS)

    def test_an_expected_absent_power_signal_never_blocks(self):
        """Gating on it would refuse all work on the only qualified platform."""
        rule = signal_rule("power_expected_absent")
        self.assertEqual(rule.at_admission, Disposition.FAIL_SAFE)
        self.assertEqual(rule.while_running, Disposition.FAIL_SAFE)

    def test_an_unexpectedly_absent_power_signal_does_block_admission(self):
        rule = signal_rule("power_unexpected_absent")
        self.assertEqual(rule.at_admission, Disposition.FAIL_CLOSED)

    def test_vram_fails_closed_at_admission_and_safe_then_closed_running(self):
        rule = signal_rule("vram")
        self.assertEqual(rule.at_admission, Disposition.FAIL_CLOSED)
        self.assertEqual(rule.while_running, Disposition.FAIL_SAFE_THEN_CLOSED)

    def test_an_unreadable_quarantine_store_fails_closed_in_both_phases(self):
        rule = signal_rule("quarantine_store")
        self.assertEqual(rule.at_admission, Disposition.FAIL_CLOSED)
        self.assertEqual(rule.while_running, Disposition.FAIL_CLOSED)

    def test_confidence_distinguishes_expected_from_unexpected_absence(self):
        self.assertNotEqual(Confidence.UNAVAILABLE_EXPECTED,
                            Confidence.UNAVAILABLE_UNEXPECTED)


class QuarantineDataTests(unittest.TestCase):
    def test_a_record_must_carry_a_digest_and_a_boot_id(self):
        self.assertIn("digest", QUARANTINE_RECORD_FIELDS)
        self.assertIn("bootId", QUARANTINE_RECORD_FIELDS)

    def test_scopes_are_declared_and_exclude_nothing_narrower_than_device(self):
        self.assertEqual(set(QUARANTINE_SCOPES),
                         {"device", "adapter", "transport", "machine"})

    def test_incident_classes_do_not_include_the_run_id(self):
        """A breaker keyed on the run id would never open."""
        self.assertNotIn("run", INCIDENT_CLASSES)
        self.assertNotIn("runId", INCIDENT_CLASSES)


class AuditFieldTests(unittest.TestCase):
    def test_a_state_revision_is_carried(self):
        """Blocker 4: stale triggers are rejected against a revision."""
        self.assertIn("safetyStateRevision", AUDIT_FIELDS)

    def test_policy_and_contract_versions_are_both_recorded(self):
        """A decision is reviewable only against the rules then in force."""
        self.assertIn("safetyPolicyVersion", AUDIT_FIELDS)
        self.assertIn("safetyContractVersion", AUDIT_FIELDS)

    def test_simulation_is_marked(self):
        self.assertIn("safetySimulated", AUDIT_FIELDS)


class ProseMatchesDataTests(unittest.TestCase):
    """Blocker 10: the document is checked against the table, not trusted.

    Prose and code both holding the table is how they drift, and the drift is
    invisible until someone reads both carefully at the same moment.
    """

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(DOC):
            # Not a skip-on-file-missing, which is how a gate comes to pass
            # because its evidence vanished -- this project has shipped one of
            # those already. The installed context is proven positively before
            # anything is skipped, and a missing document anywhere else is a
            # hard failure.
            installed = ("site-packages" in _HERE or "dist-packages" in _HERE)
            package = os.path.isfile(
                os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                             "__init__.py"))
            assert installed and package, (
                f"the contract is missing: {DOC}. This is only tolerated inside "
                f"an installed package, and this is not one ({_HERE}).")
            raise unittest.SkipTest(
                "prose-vs-data consistency is a property of the repository. An "
                "installed wheel ships no document for the data to drift from, "
                "so there is nothing here to check. The repository suite runs "
                "these.")
        with open(DOC, "r", encoding="utf-8") as handle:
            cls.text = handle.read()
        # Prose is hard-wrapped, so a claim can span a line break. Assertions
        # about wording match against this, or they fail on reflowing a
        # paragraph -- a false failure that teaches people to loosen the test.
        cls.flat = re.sub(r"\s+", " ", cls.text)

    def test_every_transition_row_appears_in_the_document(self):
        for transition in TRANSITIONS:
            pattern = (rf"\|\s*`{transition.source.value}`\s*\|"
                       rf"\s*`{transition.target.value}`\s*\|"
                       rf"[^|]*`{re.escape(transition.trigger)}`")
            self.assertRegex(
                self.text, pattern,
                f"{transition.source.value} -> {transition.target.value} on "
                f"{transition.trigger} is in the table and not in the document")

    def test_the_document_declares_no_transition_the_table_lacks(self):
        rows = re.findall(r"^\|\s*`([A-Z]+)`\s*\|\s*`([A-Z]+)`\s*\|\s*`([a-z_]+)`",
                          self.text, re.M)
        self.assertTrue(rows, "no transition rows parsed from the document")
        for source, target, trigger in rows:
            transition = permitted(State(source), trigger)
            self.assertIsNotNone(
                transition,
                f"the document declares {source} -> {target} on {trigger}, "
                "which the table does not")
            self.assertEqual(transition.target.value, target)

    def test_the_non_goal_precedes_the_scope(self):
        self.assertLess(self.text.index("## 0. Non-goal"),
                        self.text.index("## 1. Scope"))
        self.assertIn("cannot guarantee that hardware will never fail", self.flat)

    def test_policy_v1_states_it_performs_no_thermal_protection(self):
        """Blocker 7: the boundary must be stated, not merely implied."""
        self.assertIn("performs no thermal protection", self.flat)
        for signal in ADVISORY_ONLY_SIGNALS:
            self.assertIn(signal, self.flat)

    def test_the_hard_limit_sequence_is_stated_once(self):
        """Blocker 1: drain then bounded kill, never a jump to FAILED."""
        self.assertIn("Both limits drain. Neither jumps to", self.flat)
        self.assertIn("hard_drain_deadline_seconds", self.flat)

    def test_termination_identity_is_specified(self):
        """Blocker 5: PIDs are reused; start time is what makes it sound."""
        for field_name in ("processStartTime", "runId", "placementId"):
            self.assertIn(field_name, self.flat)
        self.assertIn("safety_termination_intent", self.flat)
        self.assertIn("safety_termination_outcome", self.flat)

    def test_durable_quarantine_write_is_specified(self):
        """Blocker 6: fsync the directory too, or the rename is not durable."""
        for step in ("fsync", "os.replace", ".partial", "digest"):
            self.assertIn(step, self.flat)

    def test_state_revision_and_idempotent_rejection_are_specified(self):
        """Blocker 4: duplicate and out-of-order triggers must be safe."""
        self.assertIn("stateRevision", self.flat)
        self.assertIn("idempotently", self.flat)
        self.assertIn("Lease admission is atomic", self.flat)

    def test_the_document_names_the_contract_and_policy_versions(self):
        self.assertIn(CONTRACT_VERSION, self.text)
        self.assertIn(Policy().version, self.text)

    def test_the_policy_version_carries_its_provisional_status(self):
        """In the string, not a footnote: every event carrying it says so.

        The semantics were reviewed and are frozen. The numbers were reasoned
        from the hardware and never measured against a failure, and a record
        must not later read as though they had been.
        """
        self.assertTrue(Policy().version.endswith("-provisional"),
                        f"{Policy().version} does not declare itself provisional")
        self.assertNotIn("-provisional", CONTRACT_VERSION,
                         "the contract semantics are frozen, not provisional")

    def test_the_document_states_both_identities_and_their_status(self):
        self.assertIn("FROZEN", self.flat)
        self.assertIn("PROVISIONAL", self.flat)
        self.assertIn("not derived from observed failures", self.flat)

    def test_the_canary_section_reference_is_right(self):
        """Blocker 8: the compute canary is 4.5, not 4.4."""
        canary = self.text.index("### 4.5 Compute canary")
        confidence = self.text.index("### 4.4 Telemetry confidence")
        self.assertLess(confidence, canary)
        self.assertNotRegex(self.text, r"canary.{0,40}§\s*4\.4")


if __name__ == "__main__":
    unittest.main()
