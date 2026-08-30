"""Manifest schema behaviour: readable is not the same as executable.

Written before the implementation. These fail until the schema split lands.

The v1 fixture is the real frozen manifest from release/0.1.0a5, not a
synthesised one. A synthesised v1 would be built by today's code and would prove
only that the code agrees with itself; the frozen file is what actually has to
keep verifying, and it is the thing that would break if anything defaulted a
field before the digest was checked.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "serving"))
sys.path.insert(0, os.path.dirname(__file__))

from model_inspect import LayerGroup, ModelInspection, Tensor  # noqa: E402
from placement import (CURRENT_MANIFEST_SCHEMA_VERSION,  # noqa: E402
                       EXECUTABLE_MANIFEST_SCHEMA_VERSIONS,
                       READABLE_MANIFEST_SCHEMA_VERSIONS,
                       ManifestError, build_placement, validate_for_worker,
                       validate_manifest)

REPO = os.path.abspath(os.path.join(ROOT, ".."))
FROZEN_V1 = os.path.join(REPO, "release", "0.1.0a5", "placement.json")


def model() -> ModelInspection:
    config = {
        "architectures": ["MistralForCausalLM"], "model_type": "mistral",
        "hidden_size": 4, "num_attention_heads": 2, "num_key_value_heads": 1,
        "head_dim": 2, "torch_dtype": "float32",
    }
    prologue = [Tensor("model.embed_tokens.weight", "F32", (10,), "one")]
    epilogue = [Tensor("lm_head.weight", "F32", (10,), "one")]
    layers, tensors = {}, list(prologue)
    for index in range(4):
        tensor = Tensor(f"model.layers.{index}.weight", "F32", (10,), "one")
        layers[index] = LayerGroup(index, [tensor])
        tensors.append(tensor)
    tensors.extend(epilogue)
    return ModelInspection("/model", config, tensors, layers, prologue, epilogue)


def fabric() -> dict:
    return {
        "schemaVersion": 2, "probedAt": 1.0, "fromCache": False,
        "identitiesUnique": True, "devices": [
            # torch_version is present because a real fabric probe reports it
            # and the manifest records it; a fixture without it would silently
            # skip the runtime check it is meant to exercise.
            {"identity": "nvidia:GPU-test", "identity_source": "uuid",
             "identityConfidence": "strong", "runtime": "cuda",
             "freeBytes": 10000, "torch_version": "2.11.0+cu128"},
            {"identity": "amd:pci-0000:0c:00.0", "identity_source": "pci",
             "identityConfidence": "slot-stable", "runtime": "rocm",
             "freeBytes": 10000, "torch_version": "2.10.0+rocm7.0"},
        ],
    }


def new_manifest() -> dict:
    return build_placement(model(), "/model", fabric(), 1000, 1000, 16,
                           job_id="pl-test")


class SchemaSetTests(unittest.TestCase):
    def test_the_three_sets_are_distinct_and_correct(self):
        self.assertEqual(CURRENT_MANIFEST_SCHEMA_VERSION, 2)
        self.assertEqual(READABLE_MANIFEST_SCHEMA_VERSIONS, frozenset({1, 2}))
        self.assertEqual(EXECUTABLE_MANIFEST_SCHEMA_VERSIONS, frozenset({2}))
        self.assertLess(EXECUTABLE_MANIFEST_SCHEMA_VERSIONS,
                        READABLE_MANIFEST_SCHEMA_VERSIONS,
                        "everything executable must also be readable")

    def test_producers_write_the_current_schema(self):
        manifest = new_manifest()
        self.assertEqual(manifest["schemaVersion"],
                         CURRENT_MANIFEST_SCHEMA_VERSION)

    def test_new_manifests_pin_adapter_identity(self):
        manifest = new_manifest()
        self.assertEqual(manifest["adapterId"], "mistral")
        self.assertEqual(manifest["adapterVersion"], "1")
        self.assertIsInstance(manifest["adapterVersion"], str)
        self.assertRegex(manifest["adapterConfigDigest"], r"^[0-9a-f]{64}$")

    def test_adapter_fields_are_covered_by_the_digest(self):
        manifest = new_manifest()
        tampered = copy.deepcopy(manifest)
        tampered["adapterId"] = "somethingelse"
        with self.assertRaisesRegex(ManifestError, "digest mismatch"):
            validate_manifest(tampered)

        stripped = copy.deepcopy(manifest)
        del stripped["adapterConfigDigest"]
        with self.assertRaises(ManifestError):
            validate_manifest(stripped)


class LegacyV1Tests(unittest.TestCase):
    """Test 2: a v1 manifest is readable, replayable, and not executable."""

    def setUp(self):
        # Deliberately not a skip. A gate that passes because its evidence
        # vanished is worse than no gate, and this project has already shipped
        # one of those: a model-verify check that reported success on zero
        # shards. If the frozen manifest disappears, this must go red.
        assert os.path.isfile(FROZEN_V1), (
            f"frozen v1 manifest fixture is missing: {FROZEN_V1}. "
            "This test exists to prove frozen evidence still verifies; "
            "without the evidence it proves nothing.")
        with open(FROZEN_V1, "r", encoding="utf-8") as handle:
            self.legacy = json.load(handle)

    def test_the_fixture_really_is_v1_and_unpinned(self):
        self.assertEqual(self.legacy["schemaVersion"], 1)
        self.assertNotIn("adapterId", self.legacy)

    def test_v1_still_verifies_its_original_digest(self):
        """The whole point. Nothing may be injected before this check."""
        validate_manifest(self.legacy)

    def test_v1_verification_does_not_mutate_the_manifest(self):
        before = json.dumps(self.legacy, sort_keys=True)
        validate_manifest(self.legacy)
        self.assertEqual(json.dumps(self.legacy, sort_keys=True), before,
                         "validation must not default or normalise any field")

    def test_v1_refuses_to_execute(self):
        with self.assertRaises(ManifestError) as caught:
            validate_for_worker(self.legacy, model(), "cuda")
        error = caught.exception
        self.assertEqual(getattr(error, "error_code", None), "LEGACY_UNPINNED_MANIFEST")
        self.assertEqual(getattr(error, "remediation", None), "REPLAN_REQUIRED")

    def test_v1_reports_one_code_not_two(self):
        with self.assertRaises(ManifestError) as caught:
            validate_for_worker(self.legacy, model(), "cuda")
        payload = getattr(caught.exception, "as_dict", lambda: {})()
        self.assertEqual(payload.get("errorCode"), "LEGACY_UNPINNED_MANIFEST")
        self.assertNotIn("REPLAN_REQUIRED", str(payload.get("errorCode", "")))


class MalformedV2Tests(unittest.TestCase):
    """Test 3: an incomplete v2 is invalid, never demoted to legacy."""

    def _rebuild_digest(self, manifest: dict) -> dict:
        from placement import _digest
        manifest = copy.deepcopy(manifest)
        manifest.pop("manifestDigest", None)
        manifest["manifestDigest"] = _digest(manifest)
        return manifest

    def test_v2_missing_adapter_id_is_invalid_not_legacy(self):
        manifest = new_manifest()
        del manifest["adapterId"]
        manifest = self._rebuild_digest(manifest)
        with self.assertRaises(ManifestError) as caught:
            validate_for_worker(manifest, model(), "cuda")
        self.assertEqual(getattr(caught.exception, "error_code", None),
                         "INVALID_MANIFEST")

    def test_v2_missing_config_digest_is_invalid(self):
        manifest = new_manifest()
        del manifest["adapterConfigDigest"]
        manifest = self._rebuild_digest(manifest)
        with self.assertRaises(ManifestError) as caught:
            validate_for_worker(manifest, model(), "cuda")
        self.assertEqual(getattr(caught.exception, "error_code", None),
                         "INVALID_MANIFEST")

    def test_unknown_schema_version_is_rejected(self):
        manifest = new_manifest()
        manifest["schemaVersion"] = 99
        manifest = self._rebuild_digest(manifest)
        with self.assertRaises(ManifestError) as caught:
            validate_manifest(manifest)
        self.assertEqual(getattr(caught.exception, "error_code", None),
                         "UNSUPPORTED_MANIFEST_SCHEMA")


class ValidationOrderTests(unittest.TestCase):
    """Digest verification precedes every other decision."""

    def test_a_tampered_unknown_schema_reports_the_digest_first(self):
        manifest = new_manifest()
        manifest["schemaVersion"] = 99          # digest not rebuilt
        with self.assertRaises(ManifestError) as caught:
            validate_manifest(manifest)
        self.assertIn("digest", str(caught.exception).lower(),
                      "the digest is checked before the schema is judged")


if __name__ == "__main__":
    unittest.main()


class TypedErrorTests(unittest.TestCase):
    """Exit codes are declared on the exception, not decided by the caller."""

    def test_base_manifest_error_keeps_the_existing_exit_code(self):
        from placement import ManifestError as Base
        self.assertEqual(Base.error_code, "MANIFEST_ERROR")
        self.assertEqual(Base.exit_code, 66)
        self.assertIsNone(Base.remediation)

    def test_legacy_refusal_declares_65_and_a_remediation(self):
        from placement import LegacyUnpinnedManifestError
        self.assertEqual(LegacyUnpinnedManifestError.error_code,
                         "LEGACY_UNPINNED_MANIFEST")
        self.assertEqual(LegacyUnpinnedManifestError.exit_code, 65)
        self.assertEqual(LegacyUnpinnedManifestError.remediation,
                         "REPLAN_REQUIRED")
        self.assertTrue(issubclass(LegacyUnpinnedManifestError, ManifestError))

    def test_existing_failures_still_exit_66(self):
        """Digest mismatch and model drift must not change behaviour."""
        from placement import ManifestError as Base
        manifest = new_manifest()
        manifest["pipeline"]["boundaryAfterLayer"] += 1
        with self.assertRaises(Base) as caught:
            validate_manifest(manifest)
        self.assertEqual(caught.exception.exit_code, 66)


class QualificationEnforcementTests(unittest.TestCase):
    """Default-deny at the worker chokepoint, and what lifts it.

    These run against a temporary ledger rather than the machine's. A test that
    depended on the real one would pass or fail according to whether someone had
    run the gate, which is the opposite of what a test should do.
    """

    def setUp(self):
        import tempfile
        from adapters import qualification as qual
        self.qual = qual
        self.base = tempfile.TemporaryDirectory()
        self.addCleanup(self.base.cleanup)
        self.ledger = os.path.join(self.base.name, "ledger.json")

        # Point every lookup at the temporary ledger, and remove any override
        # inherited from the shell running the tests.
        self.original_path = qual.records_path
        qual.records_path = lambda explicit=None: explicit or self.ledger
        self.addCleanup(setattr, qual, "records_path", self.original_path)


        self.original_override = qual.override_from_environment
        qual.override_from_environment = lambda environ=None: None
        self.addCleanup(setattr, qual, "override_from_environment",
                        self.original_override)

        self.manifest = new_manifest()

    def test_a_valid_manifest_is_refused_when_nothing_has_measured_it(self):
        from adapters import AdapterError
        with self.assertRaises(AdapterError) as caught:
            validate_for_worker(self.manifest, model(), "cuda")
        self.assertEqual(caught.exception.error_code, "ADAPTER_UNQUALIFIED")
        self.assertEqual(caught.exception.exit_code, 65)
        self.assertEqual(caught.exception.remediation, "QUALIFICATION_REQUIRED")

    def test_the_same_manifest_is_permitted_once_a_record_exists(self):
        situation = self.qual.situation_from_manifest(self.manifest)
        self.qual.append_record(
            self.qual.record_from_situation(situation), self.ledger)
        stage = validate_for_worker(self.manifest, model(), "cuda")
        self.assertEqual(stage.runtime, "cuda")

    def _resign(self, manifest: dict) -> dict:
        from placement import _digest
        manifest = copy.deepcopy(manifest)
        manifest.pop("manifestDigest", None)
        manifest["manifestDigest"] = _digest(manifest)
        return manifest

    def test_a_record_does_not_carry_to_a_different_build(self):
        """The wheel is part of the tuple, so a new wheel refuses again.

        The build is altered in the manifest, not in this process, because the
        manifest is where a worker reads it from -- under the vendor
        interpreter the package is on PYTHONPATH but not installed, so asking
        the environment there returns nothing.
        """
        from adapters import AdapterError
        situation = self.qual.situation_from_manifest(self.manifest)
        self.qual.append_record(
            self.qual.record_from_situation(situation), self.ledger)

        rebuilt = copy.deepcopy(self.manifest)
        rebuilt["build"]["openmyceliumContent"] = "u" * 64   # same version
        rebuilt = self._resign(rebuilt)
        with self.assertRaises(AdapterError) as caught:
            validate_for_worker(rebuilt, model(), "cuda")
        self.assertEqual(caught.exception.error_code, "ADAPTER_UNQUALIFIED")

    def test_the_manifest_records_the_build_that_compiled_it(self):
        for field in ("openmycelium", "openmyceliumContent", "mccl",
                      "mcclContent"):
            self.assertTrue(self.manifest["build"][field],
                            f"build.{field} must never be blank; a blank is "
                            "indistinguishable from not filled in")

    def test_a_manifest_with_no_build_block_cannot_be_shown_qualified(self):
        from adapters import AdapterError
        stripped = copy.deepcopy(self.manifest)
        stripped["build"] = {"openmycelium": "", "openmyceliumContent": "",
                             "mccl": "", "mcclContent": ""}
        stripped = self._resign(stripped)
        with self.assertRaises(AdapterError) as caught:
            validate_for_worker(stripped, model(), "cuda")
        self.assertEqual(caught.exception.error_code, "ADAPTER_UNQUALIFIED")

    def test_a_record_does_not_carry_to_a_different_runtime(self):
        from adapters import AdapterError
        from placement import _digest
        situation = self.qual.situation_from_manifest(self.manifest)
        self.qual.append_record(
            self.qual.record_from_situation(situation), self.ledger)

        moved = copy.deepcopy(self.manifest)
        moved["fabric"]["cudaRuntime"] = "2.12.0+cu129"
        moved.pop("manifestDigest")
        moved["manifestDigest"] = _digest(moved)
        with self.assertRaises(AdapterError) as caught:
            validate_for_worker(moved, model(), "cuda")
        self.assertEqual(caught.exception.error_code, "ADAPTER_UNQUALIFIED")

    def test_an_explicit_override_permits_it_and_reaches_the_audit(self):
        class Audit:
            def __init__(self):
                self.events = []

            def emit_qualification_override(self, entry):
                self.events.append(entry)

        audit = Audit()
        self.qual.override_from_environment = lambda environ=None: {
            "mode": "override", "actor": "an-operator", "reason": "measuring"}
        stage = validate_for_worker(self.manifest, model(), "cuda", audit=audit)
        self.assertEqual(stage.runtime, "cuda")
        self.assertEqual(len(audit.events), 1)
        self.assertEqual(audit.events[0]["actor"], "an-operator")
        self.assertIsNone(audit.events[0]["againstRecord"])

    def test_a_worker_on_a_different_torch_refuses_before_qualification(self):
        """Runtime drift is a manifest problem, and is reported as one."""
        from placement import AdapterMismatchError
        situation = self.qual.situation_from_manifest(self.manifest)
        self.qual.append_record(
            self.qual.record_from_situation(situation), self.ledger)
        with self.assertRaises(AdapterMismatchError) as caught:
            validate_for_worker(self.manifest, model(), "cuda",
                                runtime_version="2.99.0+cu999")
        self.assertEqual(caught.exception.error_code, "ADAPTER_MISMATCH")
        self.assertEqual(caught.exception.exit_code, 65)

    def test_structural_failures_are_reported_before_qualification(self):
        """An unqualified situation must not mask a broken manifest."""
        from placement import ManifestError
        with self.assertRaises(ManifestError) as caught:
            validate_for_worker(self.manifest, model(), "rocm-not-a-role")
        self.assertNotEqual(getattr(caught.exception, "error_code", ""),
                            "ADAPTER_UNQUALIFIED")
