"""Gate D.3: the enforcement contract, asserted as data.

These tests hold the contract to account. They do not test an implementation --
there is none, deliberately. The ones that describe behaviour the Governor does
not yet have are expected to fail until D.3's implementation is separately
authorised, which is the C.1 pattern: the contract and its failing tests are
frozen first, so the implementation is measured against something written before
it existed rather than described by it afterwards.

Nothing here touches a GPU.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

DOC = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "docs",
                   "SAFETY_ENFORCEMENT.md")

from contract import CONTRACT_VERSION, TRANSITIONS  # noqa: E402
from enforcement import (ACTION_REQUIRES, AUTHORITY_ORDER,  # noqa: E402
                         CANARY_SEQUENCE, DEFAULT_AUTHORITY,
                         ENFORCEMENT_AUDIT_FIELDS,
                         ENFORCEMENT_CONTRACT_VERSION, ENFORCEMENT_REVISIONS,
                         ENFORCEMENT_SCHEMA_VERSION, FAULTS,
                         HARDWARE_STOPPING_CONDITIONS, NON_GOALS,
                         PREDICTION_MISMATCH_IS_FATAL, ROLLBACK,
                         UNATTRIBUTABLE_SIGNALS, Action, Authority, Confidence,
                         Fault, fault, permitted_action, stage)


class VersioningTests(unittest.TestCase):
    """Four versions that move independently, and must stay distinguishable."""

    def test_the_enforcement_contract_has_its_own_version(self):
        self.assertTrue(ENFORCEMENT_CONTRACT_VERSION.startswith("safety-enforcement-"))
        self.assertNotEqual(ENFORCEMENT_CONTRACT_VERSION, CONTRACT_VERSION)

    def test_every_version_is_recorded_with_a_reason(self):
        self.assertIn(ENFORCEMENT_CONTRACT_VERSION, ENFORCEMENT_REVISIONS)
        for version, reason in ENFORCEMENT_REVISIONS.items():
            self.assertGreater(len(reason), 80,
                               f"{version} records no usable reason")

    def test_the_schema_version_is_not_the_contract_version(self):
        # A record's shape and an action's meaning change for different reasons.
        self.assertNotEqual(str(ENFORCEMENT_SCHEMA_VERSION),
                            ENFORCEMENT_CONTRACT_VERSION)


class AuthorityLadderTests(unittest.TestCase):
    def test_the_default_is_off(self):
        # Freezing an enforcement contract is not enabling enforcement.
        self.assertIs(DEFAULT_AUTHORITY, Authority.OFF)

    def test_the_ladder_is_ordered_and_complete(self):
        self.assertEqual(len(AUTHORITY_ORDER), len(Authority))
        self.assertEqual(AUTHORITY_ORDER[0], Authority.OFF)
        self.assertEqual(AUTHORITY_ORDER[-1], Authority.ENFORCE_FULL)

    def test_every_action_declares_the_authority_it_needs(self):
        for action in Action:
            self.assertIn(action, ACTION_REQUIRES,
                          f"{action} names no required authority")

    def test_actions_requiring_more_authority_are_harder_to_undo(self):
        # The ladder must not permit quarantine before drain, or drain before
        # refusal: reversibility decreases as authority increases.
        expected = [Action.REFUSE_ADMISSION, Action.DRAIN_GRACEFUL,
                    Action.OPEN_BREAKER, Action.QUARANTINE]
        levels = [AUTHORITY_ORDER.index(ACTION_REQUIRES[a]) for a in expected]
        self.assertEqual(levels, sorted(levels))

    def test_no_action_is_permitted_below_its_authority(self):
        for entry in FAULTS:
            required = ACTION_REQUIRES[entry.permitted_action]
            below = AUTHORITY_ORDER.index(required) - 1
            if below < 0:
                continue
            weaker = AUTHORITY_ORDER[below]
            self.assertIs(permitted_action(entry.name, weaker), Action.NONE,
                          f"{entry.name} acts at {weaker}, below {required}")

    def test_authority_never_promotes_an_action(self):
        # ENFORCE_FULL enables every rung; it must not turn a refusal into a
        # quarantine. Authority says what is switched on, the fault says what it
        # warrants, and an action needs both.
        for entry in FAULTS:
            self.assertIs(permitted_action(entry.name, Authority.ENFORCE_FULL),
                          entry.permitted_action)

    def test_an_unknown_fault_warrants_nothing(self):
        self.assertIs(permitted_action("not_a_fault", Authority.ENFORCE_FULL),
                      Action.NONE)


class FaultMatrixTests(unittest.TestCase):
    def test_every_named_trigger_exists_in_the_state_contract(self):
        # The matrix and the transition table are joined by name, so a fault
        # cannot name a trigger the state machine has never heard of. Writing
        # this test first caught four that did.
        triggers = {t.trigger for t in TRANSITIONS}
        for entry in FAULTS:
            if entry.contract_trigger is None:
                continue
            self.assertIn(entry.contract_trigger, triggers,
                          f"{entry.name} raises {entry.contract_trigger!r}, "
                          "which no transition accepts")

    def test_a_fault_without_a_trigger_changes_no_state(self):
        # The converse, and the reason the field is optional: DEGRADE stops
        # admission and leaves in-flight work alone, which is not a move through
        # the state machine. A fault that claims no trigger while taking a state
        # changing action would be inventing a transition the frozen table does
        # not have.
        stateless = (Action.DEGRADE, Action.NONE)
        for entry in FAULTS:
            if entry.contract_trigger is None:
                self.assertIn(entry.permitted_action, stateless,
                              f"{entry.name} takes {entry.permitted_action} "
                              "without naming a transition")

    def test_a_state_changing_action_always_names_its_trigger(self):
        for entry in FAULTS:
            if entry.permitted_action in (Action.DEGRADE, Action.NONE):
                continue
            self.assertIsNotNone(
                entry.contract_trigger,
                f"{entry.name} takes {entry.permitted_action} but names no trigger")

    def test_belief_revision_needs_no_authority(self):
        # boot_changed is READY -> UNKNOWN: the Governor correcting what it
        # thinks is true, not acting on the world. It must be available at every
        # rung, including OFF, or the Governor would keep believing a lease from
        # a previous boot is live.
        revision = fault("boot_changed")
        self.assertIsNotNone(revision)
        self.assertIs(revision.permitted_action, Action.NONE)
        self.assertIs(permitted_action("boot_changed", Authority.OFF),
                      Action.NONE)

    def test_every_fault_names_a_rollback(self):
        for entry in FAULTS:
            self.assertTrue(entry.rollback.strip(),
                            f"{entry.name} names no rollback")

    def test_fault_names_are_unique(self):
        names = [entry.name for entry in FAULTS]
        self.assertEqual(len(names), len(set(names)))

    def test_expected_unavailability_is_not_a_fault(self):
        # AMD power under WSL is a platform gap, not a failure. A matrix that
        # treats it as one would degrade every run on this hardware.
        for entry in FAULTS:
            self.assertIsNot(entry.requires_confidence,
                             Confidence.UNAVAILABLE_EXPECTED,
                             f"{entry.name} acts on expected unavailability")

    def test_only_one_fault_quarantines_on_a_single_occurrence(self):
        single = [e for e in FAULTS if e.permitted_action is Action.QUARANTINE]
        self.assertEqual([e.name for e in single], ["boundary_integrity_failed"])

    def test_admission_faults_fail_closed(self):
        # An unreadable card is not an empty one.
        unreadable = fault("vram_unreadable_at_admission")
        self.assertIsNotNone(unreadable)
        self.assertIs(unreadable.permitted_action, Action.REFUSE_ADMISSION)
        self.assertIs(unreadable.requires_confidence,
                      Confidence.UNAVAILABLE_UNEXPECTED)

    def test_stale_telemetry_does_not_interrupt_running_work(self):
        # Fail-safe in flight: a stale reading is not a fault report, so it
        # stops admission and leaves in-flight work alone.
        stale = fault("telemetry_stale")
        self.assertIsNotNone(stale)
        self.assertIs(stale.permitted_action, Action.DEGRADE)


class CanarySequenceTests(unittest.TestCase):
    def test_the_sequence_is_ordered_from_one(self):
        self.assertEqual([s.order for s in CANARY_SEQUENCE],
                         list(range(1, len(CANARY_SEQUENCE) + 1)))

    def test_admission_refusal_is_first(self):
        first = stage(1)
        self.assertIs(first.authority, Authority.ENFORCE_ADMISSION)
        self.assertIn(Action.REFUSE_ADMISSION, first.actions_enabled)

    def test_quarantine_is_last(self):
        last = CANARY_SEQUENCE[-1]
        self.assertIs(last.authority, Authority.ENFORCE_QUARANTINE)

    def test_stage_authority_increases_monotonically(self):
        levels = [AUTHORITY_ORDER.index(s.authority) for s in CANARY_SEQUENCE]
        self.assertEqual(levels, sorted(levels))
        self.assertEqual(len(levels), len(set(levels)))

    def test_every_stage_states_both_acceptance_and_rejection(self):
        # A stage with only acceptance criteria cannot fail, and a gate that
        # cannot fail is not a gate.
        for entry in CANARY_SEQUENCE:
            self.assertTrue(entry.accept_when.strip(), f"stage {entry.order}")
            self.assertTrue(entry.reject_when.strip(), f"stage {entry.order}")
            self.assertNotEqual(entry.accept_when, entry.reject_when)

    def test_every_stage_has_a_synthetic_gate_before_a_hardware_gate(self):
        for entry in CANARY_SEQUENCE:
            self.assertTrue(entry.synthetic_gate.strip(), f"stage {entry.order}")
            self.assertTrue(entry.hardware_gate.strip(), f"stage {entry.order}")

    def test_every_stage_gives_a_reason_for_its_position(self):
        for entry in CANARY_SEQUENCE:
            self.assertGreater(len(entry.rationale), 60,
                               f"stage {entry.order} does not justify its order")

    def test_every_enabled_action_is_permitted_by_the_stage_authority(self):
        for entry in CANARY_SEQUENCE:
            for action in entry.actions_enabled:
                required = ACTION_REQUIRES[action]
                self.assertLessEqual(
                    AUTHORITY_ORDER.index(required),
                    AUTHORITY_ORDER.index(entry.authority),
                    f"stage {entry.order} enables {action} above its authority")


class RollbackTests(unittest.TestCase):
    def test_every_rollback_survives_a_crash(self):
        # A rollback that needs the thing that failed is not a rollback.
        for entry in ROLLBACK:
            self.assertTrue(entry.survives_crash, entry.mechanism)

    def test_at_least_one_rollback_needs_no_restart(self):
        self.assertTrue(any(not e.requires_restart for e in ROLLBACK))

    def test_a_kill_switch_exists_that_pins_authority_off(self):
        self.assertTrue(any("kill switch" in e.mechanism for e in ROLLBACK))

    def test_quarantine_is_undone_only_by_a_named_actor(self):
        manual = [e for e in ROLLBACK if "quarantine" in e.mechanism]
        self.assertTrue(manual)
        self.assertTrue(any("actor" in e.note for e in manual))


class StoppingConditionTests(unittest.TestCase):
    def test_a_surviving_worker_stops_a_campaign(self):
        self.assertTrue(any("worker process survives" in c
                            for c in HARDWARE_STOPPING_CONDITIONS))

    def test_an_action_above_the_authority_under_test_stops_a_campaign(self):
        self.assertTrue(any("above the authority" in c
                            for c in HARDWARE_STOPPING_CONDITIONS))

    def test_an_unpredicted_action_stops_a_campaign(self):
        self.assertTrue(any("no matching shadow prediction" in c
                            for c in HARDWARE_STOPPING_CONDITIONS))

    def test_whole_device_memory_totals_are_not_a_stopping_condition(self):
        # It moved 722 -> 824 -> 678 MiB in one sealing run with nothing of ours
        # alive. A number that moves on its own would abort valid campaigns.
        for condition in HARDWARE_STOPPING_CONDITIONS:
            self.assertNotIn("memory total", condition)
        self.assertTrue(any("memory total" in s for s in UNATTRIBUTABLE_SIGNALS))


class EvidenceSchemaTests(unittest.TestCase):
    def test_the_record_carries_the_correlation_fields_shadow_added(self):
        # These are why 0.3.0a9 was superseded: an action that cannot be tied to
        # a run, a placement, a boot and a moment cannot be compared with
        # anything.
        for name in ("runId", "placementId", "manifestDigest", "bootId",
                     "wallTimeUtc", "monotonicNs"):
            self.assertIn(name, ENFORCEMENT_AUDIT_FIELDS)

    def test_the_record_carries_the_shadow_prediction_it_must_match(self):
        for name in ("shadowPredictedAction", "shadowPredictedTransition",
                     "predictionMatched"):
            self.assertIn(name, ENFORCEMENT_AUDIT_FIELDS)

    def test_the_record_names_all_four_versions(self):
        for name in ("schemaVersion", "enforcementContractVersion",
                     "safetyContractVersion", "safetyPolicyVersion"):
            self.assertIn(name, ENFORCEMENT_AUDIT_FIELDS)

    def test_the_record_states_the_authority_and_the_rollback(self):
        self.assertIn("authority", ENFORCEMENT_AUDIT_FIELDS)
        self.assertIn("rollbackAvailable", ENFORCEMENT_AUDIT_FIELDS)

    def test_a_prediction_mismatch_is_fatal(self):
        # The comparison exists to find disagreements. A run that suppresses
        # them has destroyed its own purpose.
        self.assertTrue(PREDICTION_MISMATCH_IS_FATAL)

    def test_audit_fields_are_unique(self):
        self.assertEqual(len(ENFORCEMENT_AUDIT_FIELDS),
                         len(set(ENFORCEMENT_AUDIT_FIELDS)))


class NonGoalTests(unittest.TestCase):
    def test_default_enforcement_is_a_stated_non_goal(self):
        self.assertTrue(any("no default enforcement" in g for g in NON_GOALS))

    def test_memory_os_work_is_prohibited_on_this_branch(self):
        listed = " ".join(NON_GOALS)
        for symbol in ("MemoryObject", "ResidencyManager", "bind_working_set"):
            self.assertIn(symbol, listed)

    def test_the_state_contract_is_declared_unchanged(self):
        self.assertTrue(any("safety-contract-1.1" in g for g in NON_GOALS))

    def test_no_thermal_or_power_claim(self):
        self.assertTrue(any("thermal" in g for g in NON_GOALS))


class StateContractUntouchedTests(unittest.TestCase):
    """D.3 adds authority. It must not have moved the state machine."""

    def test_the_state_contract_version_is_still_1_1(self):
        self.assertEqual(CONTRACT_VERSION, "safety-contract-1.1")

    def test_the_transition_count_is_unchanged(self):
        # 31 at the C.1 freeze, amended to 31 + the three ADMITTED exits at 1.1.
        self.assertEqual(len(TRANSITIONS), 31)


class ProseMatchesDataTests(unittest.TestCase):
    """The document is checked against the table, not trusted.

    Prose and code both holding a table is how they drift, and the drift is
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
                f"the enforcement contract is missing: {DOC}. This is only "
                f"tolerated inside an installed package, and this is not one "
                f"({_HERE}).")
            raise unittest.SkipTest(
                "prose-vs-data consistency is a property of the repository. An "
                "installed wheel ships no document for the data to drift from.")
        with open(DOC, "r", encoding="utf-8") as handle:
            cls.text = handle.read()

    def test_every_fault_appears_in_the_document(self):
        for entry in FAULTS:
            self.assertIn(f"`{entry.name}`", self.text,
                          f"{entry.name} is in the matrix and not in the document")

    def test_every_authority_appears_in_the_document(self):
        for authority in Authority:
            self.assertIn(f"`{authority.value}`", self.text)

    def test_the_document_invents_no_fault(self):
        # The other direction: a row someone typed into the prose that the data
        # does not have would otherwise read as contract.
        import re
        known = {entry.name for entry in FAULTS}
        for match in re.finditer(r"^\| `([a-z_]+)` \| ", self.text, re.M):
            name = match.group(1)
            if name in {a.value for a in Authority}:
                continue
            self.assertIn(name, known,
                          f"the document has a fault row {name!r} that the data "
                          "does not define")

    def test_the_document_names_every_stopping_condition(self):
        # Compared against backtick-stripped prose: the document renders
        # identifiers in code style, which is correct writing, and the data
        # holds the bare sentence. Normalising one is the fix; weakening the
        # comparison to a substring of a few words would let a condition be
        # silently reworded.
        plain = self.text.replace("`", "")
        for condition in HARDWARE_STOPPING_CONDITIONS:
            self.assertIn(condition, plain,
                          f"stopping condition absent from the document: {condition}")

    def test_the_document_states_the_default_authority_is_off(self):
        self.assertIn("default is `off`", self.text)

    def test_the_document_states_memory_os_is_prohibited(self):
        self.assertIn("No Memory OS work of any kind on this branch", self.text)

    def test_the_document_records_the_unattributable_signal(self):
        # The 722 -> 824 -> 678 reading, kept in the contract so a future reader
        # does not reintroduce it as a stopping condition.
        self.assertIn("722", self.text)
        self.assertIn("678", self.text)

    def test_the_document_declares_a_mismatch_fatal(self):
        self.assertIn("prediction mismatch is fatal", self.text.lower())


if __name__ == "__main__":
    unittest.main()
