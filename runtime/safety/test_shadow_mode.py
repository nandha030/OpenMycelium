"""Shadow mode observes and cannot act. Synthetic only; no GPU, no process.

The evidence shadow mode produces is only worth having if shadow mode provably
changes nothing, so most of this file is about what does *not* happen: no
refusal reaches a caller, no lease moves, no production quarantine record is
written, no signal is sent, and `off` is the previous code path exactly.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from contract import CONTRACT_VERSION, Confidence, Policy  # noqa: E402
from governor import TestClock  # noqa: E402
from shadow import (MIN_POLL_INTERVAL_SECONDS, MODE_OFF,  # noqa: E402
                    MODE_SHADOW, MODES, FabricTelemetry, ShadowObserver,
                    safety_mode)

CUDA = "nvidia:GPU-cbb3d045-9d5f-a225-0f2e-adb1c6d6a033"
ROCM = "amd:pci-0000:04:00.0"
TOTAL = 17102864384


def fabric(cuda_free: int = TOTAL - 800 * 1024 * 1024,
           rocm_free: int = 16892866560) -> dict:
    """A Fabric snapshot shaped exactly as the real probe reports one.

    Including the AMD row as it genuinely arrives under WSL: power_watts None
    with power_source 'unavailable', never zero.
    """
    return {
        "schemaVersion": 2, "identitiesUnique": True,
        "devices": [
            {"identity": CUDA, "vendor": "nvidia", "runtime": "cuda",
             "free_bytes": cuda_free, "total_bytes": TOTAL,
             "power_watts": 24.61, "power_source": "nvidia-smi",
             "torch_version": "2.11.0+cu128"},
            {"identity": ROCM, "vendor": "amd", "runtime": "rocm",
             "free_bytes": rocm_free, "total_bytes": 16974905344,
             "power_watts": None, "power_source": "unavailable",
             "torch_version": "2.10.0+rocm7.0"},
        ],
    }


def observer(mode=MODE_SHADOW, clock=None) -> ShadowObserver:
    return ShadowObserver(mode=mode, boot_id="boot-test",
                          clock=clock or TestClock(start=1000.0))


# --------------------------------------------------------------------- modes

class ModeTests(unittest.TestCase):
    def test_only_off_and_shadow_exist(self):
        self.assertEqual(set(MODES), {"off", "shadow"})

    def test_the_default_is_off(self):
        self.assertEqual(safety_mode({}), MODE_OFF)

    def test_an_unknown_value_falls_back_to_off(self):
        """A typo must not stop a production run."""
        for value in ("enforce", "on", "true", "SHADOWY", ""):
            self.assertEqual(safety_mode({"OM_SAFETY_MODE": value}), MODE_OFF)

    def test_shadow_is_opt_in_by_exact_value(self):
        self.assertEqual(safety_mode({"OM_SAFETY_MODE": "shadow"}), MODE_SHADOW)
        self.assertEqual(safety_mode({"OM_SAFETY_MODE": "SHADOW"}), MODE_SHADOW)

    def test_an_off_observer_is_inert(self):
        handle = observer(mode=MODE_OFF)
        self.assertFalse(handle.active)
        handle.observe("admission", fabric(), force=True)
        self.assertEqual(handle.observations, [],
                         "off must be the previous code path exactly")


# ---------------------------------------------------------------- it cannot act

class CannotActTests(unittest.TestCase):
    """The properties that make the evidence worth collecting."""

    def test_observe_returns_nothing(self):
        """A return value is how an observer becomes a decision-maker."""
        self.assertIsNone(observer().observe("admission", fabric(), force=True))

    def test_the_observer_exposes_no_decision_method(self):
        surface = {name for name in dir(ShadowObserver)
                   if not name.startswith("_")}
        for forbidden in ("admit", "refuse", "drain", "terminate", "quarantine",
                          "release", "enforce", "veto", "manual_reset",
                          "report_incident"):
            self.assertNotIn(forbidden, surface,
                             f"shadow mode exposes {forbidden}()")

    def test_no_production_quarantine_record_is_written(self):
        """Its store is a throwaway dict that does not outlive the call."""
        handle = observer()
        handle.observe("running", fabric(cuda_free=1024), force=True)
        governor = handle._governor
        self.assertIsNotNone(governor)
        self.assertIsNone(governor.store._path,
                          "the shadow governor must have no file-backed store")

    def test_no_signal_is_ever_sent(self):
        handle = observer()
        handle.observe("running", fabric(cuda_free=1024), force=True)
        self.assertEqual(handle._governor.actuator.signalled, [])

    def test_every_observation_marks_itself_unenforced(self):
        handle = observer()
        handle.observe("admission", fabric(), force=True)
        record = handle.observations[-1]
        self.assertIs(record["safetyEnforced"], False)
        self.assertIs(record["safetySimulated"], True)
        self.assertEqual(record["safetyMode"], MODE_SHADOW)

    def test_a_malformed_report_is_observed_not_raised(self):
        """An observer that can stop a production run is not an observer.

        A device row with an identity and no readings is exactly what a partly
        failed probe produces. The observer records it as unavailable telemetry
        -- which is a finding -- rather than raising into the caller. The
        coordinator also swallows, but defence in depth is not an excuse for the
        first layer to throw.
        """
        handle = observer()
        handle.observe("admission", {"devices": [{"identity": CUDA}]},
                       force=True)
        signals = handle.observations[-1]["safetySignals"][CUDA]
        self.assertIsNone(signals["freeBytes"])
        self.assertIn("UNAVAILABLE_UNEXPECTED", signals["vramConfidence"])
        self.assertEqual(handle.observations[-1]["wouldAction"],
                         "refuse_admission")

    def test_an_empty_report_is_observed_not_raised(self):
        handle = observer()
        handle.observe("admission", {"devices": []}, force=True)
        self.assertEqual(handle.observations[-1]["safetySignals"], {})


# ------------------------------------------------------------- what it records

class ObservationContentTests(unittest.TestCase):
    def test_the_required_fields_are_present(self):
        handle = observer()
        handle.observe("admission", fabric(), force=True)
        record = handle.observations[-1]
        for field in ("safetyMode", "safetyContractVersion",
                      "safetyPolicyVersion", "safetySignals",
                      "wouldTransition", "wouldAction", "safetyPhase"):
            self.assertIn(field, record)

    def test_it_records_the_contract_and_provisional_policy_versions(self):
        handle = observer()
        handle.observe("admission", fabric(), force=True)
        record = handle.observations[-1]
        self.assertEqual(record["safetyContractVersion"], CONTRACT_VERSION)
        self.assertEqual(record["safetyPolicyVersion"], Policy().version)
        self.assertTrue(record["safetyPolicyVersion"].endswith("-provisional"))

    def test_signals_carry_confidence_alongside_every_value(self):
        handle = observer()
        handle.observe("admission", fabric(), force=True)
        signals = handle.observations[-1]["safetySignals"]
        for device in (CUDA, ROCM):
            for name in ("vramConfidence", "powerConfidence",
                         "temperatureConfidence"):
                self.assertIn(name, signals[device])

    def test_amd_power_stays_unavailable_and_never_becomes_zero(self):
        handle = observer()
        handle.observe("admission", fabric(), force=True)
        amd = handle.observations[-1]["safetySignals"][ROCM]
        self.assertIsNone(amd["powerWatts"])
        self.assertIn("UNAVAILABLE_EXPECTED", amd["powerConfidence"])
        self.assertNotEqual(amd["powerWatts"], 0)

    def test_a_working_nvidia_power_source_reads_as_available(self):
        handle = observer()
        handle.observe("admission", fabric(), force=True)
        nvidia = handle.observations[-1]["safetySignals"][CUDA]
        self.assertEqual(nvidia["powerWatts"], 24.61)
        self.assertIn("AVAILABLE", nvidia["powerConfidence"])

    def test_a_named_source_returning_nothing_is_unexpected_not_expected(self):
        report = fabric()
        report["devices"][0]["power_watts"] = None      # nvidia-smi named, silent
        handle = observer()
        handle.observe("admission", report, force=True)
        nvidia = handle.observations[-1]["safetySignals"][CUDA]
        self.assertIn("UNAVAILABLE_UNEXPECTED", nvidia["powerConfidence"])


# ------------------------------------------------------------ what it proposes

class ProposalTests(unittest.TestCase):
    def test_a_healthy_machine_proposes_no_action(self):
        handle = observer()
        handle.observe("admission", fabric(), force=True)
        record = handle.observations[-1]
        self.assertEqual(record["wouldAction"], "none")
        self.assertEqual(record["wouldTransition"], "READY->ADMITTED")

    def test_insufficient_headroom_proposes_refusing_admission(self):
        handle = observer()
        handle.observe("admission", fabric(cuda_free=64 * 1024 * 1024),
                       requested_bytes=1 << 30, force=True)
        record = handle.observations[-1]
        self.assertEqual(record["wouldAction"], "refuse_admission")
        self.assertIn("reserved", record["wouldDetail"])

    def test_the_hard_limit_proposes_an_immediate_drain(self):
        handle = observer()
        handle.observe("running", fabric(cuda_free=int(TOTAL * 0.02)),
                       force=True)
        record = handle.observations[-1]
        self.assertEqual(record["wouldAction"], "drain_immediate")
        self.assertEqual(record["wouldTransition"], "RUNNING->DRAINING")

    def test_the_soft_limit_proposes_a_conditional_graceful_drain(self):
        handle = observer()
        handle.observe("running", fabric(cuda_free=int(TOTAL * 0.08)),
                       force=True)
        self.assertEqual(handle.observations[-1]["wouldAction"],
                         "drain_graceful_if_sustained")

    def test_missing_vram_while_running_proposes_degrading(self):
        report = fabric()
        report["devices"][0]["free_bytes"] = None
        handle = observer()
        handle.observe("running", report, force=True)
        self.assertEqual(handle.observations[-1]["wouldAction"], "degrade")

    def test_preflight_without_vram_proposes_refusing(self):
        report = fabric()
        report["devices"][0]["free_bytes"] = None
        handle = observer()
        handle.observe("preflight", report, force=True)
        self.assertEqual(handle.observations[-1]["wouldAction"],
                         "refuse_preflight")


# ---------------------------------------------------------------- bounded poll

class PollBoundTests(unittest.TestCase):
    """The observer must not become the resource pressure it watches for."""

    def test_repeated_observation_is_rate_limited(self):
        clock = TestClock(start=1000.0)
        handle = observer(clock=clock)
        for _ in range(50):
            handle.observe("running", fabric())
        self.assertEqual(len(handle.observations), 1,
                         "an unbounded observer would poll 50 times")

    def test_the_interval_releases_after_it_elapses(self):
        clock = TestClock(start=1000.0)
        handle = observer(clock=clock)
        handle.observe("running", fabric())
        clock.advance(MIN_POLL_INTERVAL_SECONDS + 1)
        handle.observe("running", fabric())
        self.assertEqual(len(handle.observations), 2)

    def test_the_bound_is_a_real_interval(self):
        self.assertGreater(MIN_POLL_INTERVAL_SECONDS, 0)

    def test_it_issues_no_device_query_of_its_own(self):
        """It consumes the snapshot the coordinator already took.

        The strongest form of the bound: zero added device queries means it
        cannot contend with the workload it is watching, whatever the interval.

        Checked on the parsed import graph rather than on the text, so a mention
        inside a comment does not fail it and a real import cannot hide in one.
        """
        import ast
        tree = ast.parse(open(os.path.join(_HERE, "shadow.py"),
                              encoding="utf-8").read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("subprocess", "torch", "pynvml", "cupy", "pycuda"):
            self.assertNotIn(forbidden, imported,
                             f"shadow mode imports {forbidden}")


# ------------------------------------------------------------ fabric adapter

class FabricTelemetryTests(unittest.TestCase):
    def test_it_reads_both_devices_by_identity(self):
        telemetry = FabricTelemetry(fabric(), taken_at=1000.0)
        self.assertEqual(set(telemetry.devices()), {CUDA, ROCM})

    def test_a_device_without_an_identity_is_ignored(self):
        report = fabric()
        report["devices"].append({"runtime": "cuda", "free_bytes": 1})
        telemetry = FabricTelemetry(report, taken_at=1000.0)
        self.assertEqual(len(telemetry.devices()), 2)

    def test_the_canary_is_never_actually_run(self):
        """Running one would be real work on a real device -- an action."""
        telemetry = FabricTelemetry(fabric(), taken_at=1000.0)
        self.assertTrue(telemetry.canary(CUDA))


# ----------------------------------------------------------------- summary

class SummaryTests(unittest.TestCase):
    def test_the_summary_reports_enforcement_as_false(self):
        handle = observer()
        handle.observe("admission", fabric(), force=True)
        summary = handle.summary()
        self.assertIs(summary["enforced"], False)
        self.assertEqual(summary["safetyMode"], MODE_SHADOW)

    def test_it_distinguishes_would_have_acted_from_did_nothing(self):
        clock = TestClock(start=1000.0)
        handle = observer(clock=clock)
        handle.observe("admission", fabric(), force=True)
        handle.observe("running", fabric(cuda_free=int(TOTAL * 0.02)),
                       force=True)
        summary = handle.summary()
        self.assertIn("drain_immediate", summary["wouldHaveActed"])
        self.assertNotIn("none", summary["wouldHaveActed"])


if __name__ == "__main__":
    unittest.main()
