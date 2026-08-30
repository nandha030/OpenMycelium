"""Adapter resolution: fail closed, from the checkpoint, before anything else.

Written before the implementation exists. These fail until the resolver lands,
which is the point: each one states a rule from docs/ADAPTER_SDK.md that nothing
currently enforces.

The Llama fixtures are synthetic and deliberately so. A real Llama checkpoint
would be a weaker test, because rejection could happen for the wrong reason --
a missing shard, an unreadable tensor -- and still look like success. A config
that is structurally valid in every respect except its architecture proves that
the architecture is what was rejected.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from adapters import (AdapterError, ErrorCode, resolve_adapter,  # noqa: E402
                      require_executable_adapter)


def mistral_config() -> dict:
    """The validated checkpoint's config, reduced to what construction needs."""
    return {
        "architectures": ["MistralForCausalLM"],
        "model_type": "mistral",
        "hidden_size": 5120, "num_attention_heads": 32,
        "num_key_value_heads": 8, "head_dim": 128,
        "num_hidden_layers": 40, "intermediate_size": 14336,
        "max_position_embeddings": 131072, "rms_norm_eps": 1e-05,
        "rope_theta": 1000000.0, "sliding_window": None,
        "tie_word_embeddings": False, "torch_dtype": "bfloat16",
        "vocab_size": 131072, "hidden_act": "silu",
        "attention_dropout": 0.0, "initializer_range": 0.02,
        "bos_token_id": 1, "eos_token_id": 2, "use_cache": True,
        "transformers_version": "4.43.0.dev0",
    }


def llama_config() -> dict:
    """Structurally complete. Only the architecture is unsupported."""
    config = mistral_config()
    config["architectures"] = ["LlamaForCausalLM"]
    config["model_type"] = "llama"
    return config


class ResolutionTests(unittest.TestCase):
    """Test 1: the resolver, on config alone."""

    def test_mistral_resolves_to_the_mistral_adapter(self):
        adapter = resolve_adapter(mistral_config())
        self.assertEqual(adapter.adapter_id, "mistral")
        self.assertEqual(adapter.adapter_version, "1")
        self.assertIsInstance(adapter.adapter_version, str)
        self.assertIsInstance(adapter.adapter_api_version, int)

    def test_llama_is_unsupported_not_merely_unknown(self):
        with self.assertRaises(AdapterError) as caught:
            resolve_adapter(llama_config())
        self.assertEqual(caught.exception.code, ErrorCode.UNSUPPORTED_ARCHITECTURE)
        # The message must name what was found and what is installed, or the
        # user cannot tell a typo from an unsupported model.
        self.assertIn("LlamaForCausalLM", str(caught.exception))

    def test_unknown_architecture_is_rejected(self):
        config = mistral_config()
        config["architectures"] = ["SomethingNobodyHasHeardOf"]
        config["model_type"] = "mystery"
        with self.assertRaises(AdapterError) as caught:
            resolve_adapter(config)
        self.assertEqual(caught.exception.code, ErrorCode.UNSUPPORTED_ARCHITECTURE)

    def test_every_architecture_entry_is_considered(self):
        """A checkpoint claiming two architectures claims both."""
        config = mistral_config()
        config["architectures"] = ["SomethingElse", "MistralForCausalLM"]
        adapter = resolve_adapter(config)
        self.assertEqual(adapter.adapter_id, "mistral")

    def test_empty_architecture_list_is_rejected(self):
        config = mistral_config()
        config["architectures"] = []
        with self.assertRaises(AdapterError) as caught:
            resolve_adapter(config)
        self.assertEqual(caught.exception.code, ErrorCode.UNSUPPORTED_ARCHITECTURE)

    def test_resolution_ignores_the_directory_name(self):
        """A Mistral checkpoint in a directory called llama-7b is Mistral."""
        with tempfile.TemporaryDirectory() as base:
            path = os.path.join(base, "llama-7b-instruct")
            os.makedirs(path)
            with open(os.path.join(path, "config.json"), "w") as handle:
                json.dump(mistral_config(), handle)
            adapter = resolve_adapter(mistral_config(), model_path=path)
            self.assertEqual(adapter.adapter_id, "mistral")


class ConfigDigestTests(unittest.TestCase):
    def test_digest_is_stable_and_key_order_independent(self):
        config = mistral_config()
        shuffled = dict(reversed(list(config.items())))
        adapter = resolve_adapter(config)
        self.assertEqual(adapter.config_digest(config),
                         adapter.config_digest(shuffled))

    def test_excluded_keys_do_not_change_the_digest(self):
        adapter = resolve_adapter(mistral_config())
        base = adapter.config_digest(mistral_config())
        for key, value in (("transformers_version", "9.9.9"),
                           ("_name_or_path", "/somewhere/else")):
            config = mistral_config()
            config[key] = value
            self.assertEqual(adapter.config_digest(config), base,
                             f"{key} must not affect the digest")

    def test_any_other_key_changes_the_digest(self):
        """MistralConfig(**raw) consumes every key, so every key matters."""
        adapter = resolve_adapter(mistral_config())
        base = adapter.config_digest(mistral_config())
        for key, value in (("rope_theta", 500000.0),
                           ("num_key_value_heads", 4),
                           ("torch_dtype", "float16"),
                           ("tie_word_embeddings", True),
                           ("an_unlisted_future_key", "surprise")):
            config = mistral_config()
            config[key] = value
            self.assertNotEqual(adapter.config_digest(config), base,
                                f"{key} must change the digest")


class ExecutableAdapterTests(unittest.TestCase):
    """The gate that runs before planning."""

    def test_unsupported_architecture_is_refused_before_planning(self):
        with self.assertRaises(AdapterError) as caught:
            require_executable_adapter(llama_config())
        self.assertEqual(caught.exception.code, ErrorCode.UNSUPPORTED_ARCHITECTURE)

    def test_supported_architecture_passes(self):
        adapter = require_executable_adapter(mistral_config())
        self.assertEqual(adapter.adapter_id, "mistral")


class AmbiguityTests(unittest.TestCase):
    """Two adapters claiming one architecture is a registry defect."""

    def setUp(self):
        import adapters
        from adapters.mistral import MistralAdapter
        self.adapters = adapters

        class RivalAdapter(MistralAdapter):
            adapter_id = "mistral-rival"
            adapter_version = "1"

        self.original = adapters.installed_adapters
        self.rival = RivalAdapter()
        adapters.installed_adapters = lambda: [MistralAdapter(), self.rival]
        self.addCleanup(setattr, adapters, "installed_adapters", self.original)

    def test_two_claimants_are_refused_not_ordered(self):
        with self.assertRaises(AdapterError) as caught:
            resolve_adapter(mistral_config())
        self.assertEqual(caught.exception.code, ErrorCode.AMBIGUOUS_ADAPTER)
        self.assertEqual(caught.exception.exit_code, 70)

    def test_ambiguity_names_both_claimants(self):
        """A registry defect is only actionable if it says which two."""
        with self.assertRaises(AdapterError) as caught:
            resolve_adapter(mistral_config())
        message = str(caught.exception)
        self.assertIn("mistral@1", message)
        self.assertIn("mistral-rival@1", message)

    def test_reversing_registration_order_changes_nothing(self):
        """Ambiguity is never broken by ordering, specificity or arrival."""
        from adapters.mistral import MistralAdapter
        self.adapters.installed_adapters = lambda: [self.rival, MistralAdapter()]
        with self.assertRaises(AdapterError) as caught:
            resolve_adapter(mistral_config())
        self.assertEqual(caught.exception.code, ErrorCode.AMBIGUOUS_ADAPTER)


class IdentityTypeTests(unittest.TestCase):
    """The two version numbers are different things and different types."""

    def test_adapter_version_is_a_string_and_api_version_an_integer(self):
        adapter = resolve_adapter(mistral_config())
        self.assertIsInstance(adapter.adapter_version, str)
        self.assertIsInstance(adapter.adapter_api_version, int)
        self.assertNotIsInstance(adapter.adapter_api_version, bool)

    def test_identity_survives_a_json_round_trip_unchanged(self):
        """Why the version is a string: a manifest is JSON and is digested."""
        adapter = resolve_adapter(mistral_config())
        identity = adapter.identity()
        self.assertEqual(json.loads(json.dumps(identity)), identity)
        self.assertEqual(json.loads(json.dumps(identity))["adapterVersion"], "1")


class CustomCodeTests(unittest.TestCase):
    def test_trust_remote_code_is_refused(self):
        config = mistral_config()
        config["trust_remote_code"] = True
        with self.assertRaises(AdapterError) as caught:
            resolve_adapter(config)
        self.assertEqual(caught.exception.code, ErrorCode.CUSTOM_CODE_REFUSED)
        self.assertEqual(caught.exception.exit_code, 77)

    def test_auto_map_is_refused(self):
        config = mistral_config()
        config["auto_map"] = {"AutoModelForCausalLM": "modeling_x.XForCausalLM"}
        with self.assertRaises(AdapterError) as caught:
            resolve_adapter(config)
        self.assertEqual(caught.exception.code, ErrorCode.CUSTOM_CODE_REFUSED)


class QualificationTests(unittest.TestCase):
    """Default-deny, scoped to a situation, override attributed and recorded."""

    def situation(self, **changes):
        from adapters.qualification import build_situation
        fields = {
            "model_fingerprint": "ff74ccb7" + "0" * 56,
            "adapter_id": "mistral", "adapter_version": "1",
            "adapter_config_digest": "a" * 64,
            "openmycelium_version": "0.3.0a1",
            "openmycelium_content": "d" * 64,
            "mccl_version": "0.2.0a3",
            "mccl_content": "m" * 64,
            "transport": "host-staged-xvendor",
            "cuda_runtime": "torch 2.11.0+cu128",
            "rocm_runtime": "torch 2.10.0+rocm7.0",
            "topology": "cuda:nvidia:GPU-x + rocm:amd:pci-0000:0c:00.0, "
                        "boundary after layer 19",
        }
        fields.update(changes)
        return build_situation(**fields)

    def test_a_situation_cannot_have_empty_fields(self):
        with self.assertRaises(ValueError):
            self.situation(mccl_version="")

    def test_unqualified_execution_is_refused_by_default(self):
        from adapters.qualification import (QualificationError,
                                            require_qualified_execution)
        with self.assertRaises(QualificationError) as caught:
            require_qualified_execution(self.situation(), records=[])
        self.assertEqual(caught.exception.code, ErrorCode.ADAPTER_UNQUALIFIED)
        self.assertEqual(caught.exception.remediation, "QUALIFICATION_REQUIRED")

    def test_a_matching_record_permits_execution(self):
        from adapters.qualification import (record_from_situation,
                                            require_qualified_execution)
        situation = self.situation()
        record = record_from_situation(situation)
        evidence = require_qualified_execution(situation, records=[record])
        self.assertEqual(evidence["qualificationStatus"], "HARDWARE_QUALIFIED")
        self.assertEqual(evidence["recordId"], record["recordId"])
        self.assertNotIn("qualificationOverride", evidence)

    def test_every_element_of_the_tuple_scopes_the_record(self):
        """Change any one field and the record stops applying."""
        from adapters.qualification import (QualificationError,
                                            record_from_situation,
                                            require_qualified_execution)
        record = record_from_situation(self.situation())
        for field, value in (
                ("model_fingerprint", "b" * 64),
                ("adapter_version", "2"),
                ("adapter_config_digest", "c" * 64),
                ("openmycelium_version", "0.3.0a2"),
                # The same version, a different wheel. This is the case a
                # version string alone cannot express.
                ("openmycelium_content", "e" * 64),
                ("mccl_version", "0.2.0a4"),
                # The transport layer needs this most: MCCL owns the wire
                # protocol, so the same version with different code moves the
                # boundary bytes every byte-exactness claim rests on.
                ("mccl_content", "n" * 64),
                ("cuda_runtime", "torch 2.12.0+cu128"),
                ("rocm_runtime", "torch 2.11.0+rocm7.0"),
                ("transport", "direct-p2p"),
                ("topology", "a different pair of cards")):
            with self.subTest(field=field):
                with self.assertRaises(QualificationError):
                    require_qualified_execution(self.situation(**{field: value}),
                                                records=[record])

    def test_override_is_permitted_and_recorded_in_audit_evidence(self):
        from adapters.qualification import require_qualified_execution

        class Audit:
            def __init__(self):
                self.events = []

            def emit_qualification_override(self, entry):
                self.events.append(entry)

        audit = Audit()
        situation = self.situation()
        evidence = require_qualified_execution(
            situation, override={"mode": "qualify", "actor": "gate-operator",
                                 "reason": "post-refactor hardware gate"},
            records=[], audit=audit)

        self.assertEqual(evidence["qualificationStatus"], "UNQUALIFIED")
        override = evidence["qualificationOverride"]
        self.assertEqual(override["actor"], "gate-operator")
        self.assertEqual(override["mode"], "qualify")
        self.assertEqual(override["reason"], "post-refactor hardware gate")
        self.assertIsNone(override["againstRecord"],
                          "there was no record; the override says so")
        self.assertEqual(override["situation"]["modelFingerprint"],
                         situation["modelFingerprint"])
        self.assertEqual(audit.events, [override],
                         "the override must reach the audit trail, not only "
                         "the return value")

    def test_an_override_without_an_actor_is_refused(self):
        from adapters.qualification import override_from_environment
        with self.assertRaises(AdapterError):
            override_from_environment({"OM_QUALIFICATION_MODE": "override"})

    def test_an_unknown_mode_is_refused(self):
        from adapters.qualification import override_from_environment
        with self.assertRaises(AdapterError):
            override_from_environment({"OM_QUALIFICATION_MODE": "yes",
                                       "OM_QUALIFICATION_ACTOR": "someone"})

    def test_no_mode_means_no_override(self):
        from adapters.qualification import override_from_environment
        self.assertIsNone(override_from_environment({}))

    def test_a_missing_records_file_refuses_rather_than_crashing(self):
        from adapters.qualification import load_records
        with tempfile.TemporaryDirectory() as base:
            self.assertEqual(load_records(os.path.join(base, "absent.json")), [])

    def test_a_record_round_trips_through_the_ledger(self):
        """Written, then found again, with the rest of the ledger intact."""
        from adapters.qualification import (append_record, find_record,
                                            record_from_situation)
        with tempfile.TemporaryDirectory() as base:
            ledger = os.path.join(base, "xvendor_qualification.json")
            with open(ledger, "w", encoding="utf-8") as handle:
                json.dump({"provision": {"environments": {"cuda": {"ok": True}}}},
                          handle)

            situation = self.situation()
            append_record(record_from_situation(situation), ledger)

            self.assertIsNotNone(find_record(situation, path=ledger))
            with open(ledger, "r", encoding="utf-8") as handle:
                document = json.load(handle)
            self.assertEqual(document["provision"]["environments"]["cuda"],
                             {"ok": True},
                             "the adapter section must not disturb the rest")
            self.assertIn("adapterQualification", document)

    def test_writing_a_record_leaves_no_partial_file_behind(self):
        from adapters.qualification import append_record, record_from_situation
        with tempfile.TemporaryDirectory() as base:
            ledger = os.path.join(base, "ledger.json")
            append_record(record_from_situation(self.situation()), ledger)
            self.assertEqual(sorted(os.listdir(base)), ["ledger.json"])

    def test_a_second_record_does_not_replace_the_first(self):
        from adapters.qualification import (append_record, find_record,
                                            record_from_situation)
        with tempfile.TemporaryDirectory() as base:
            ledger = os.path.join(base, "ledger.json")
            first = self.situation()
            second = self.situation(model_fingerprint="f" * 64)
            append_record(record_from_situation(first), ledger)
            append_record(record_from_situation(second), ledger)
            self.assertIsNotNone(find_record(first, path=ledger))
            self.assertIsNotNone(find_record(second, path=ledger))

    def test_re_recording_the_same_situation_replaces_it(self):
        from adapters.qualification import (append_record, load_records,
                                            record_from_situation)
        with tempfile.TemporaryDirectory() as base:
            ledger = os.path.join(base, "ledger.json")
            situation = self.situation()
            append_record(record_from_situation(situation), ledger)
            append_record(record_from_situation(situation), ledger)
            self.assertEqual(len(load_records(ledger)), 1)


class SituationFromManifestTests(unittest.TestCase):
    """The tuple a worker checks is read out of the digest-protected manifest."""

    def manifest(self) -> dict:
        return {
            "schemaVersion": 2,
            "adapterId": "mistral", "adapterVersion": "1",
            "adapterConfigDigest": "a" * 64,
            "model": {"fingerprint": "f" * 64},
            "pipeline": {"transport": "host-staged-xvendor",
                         "boundaryAfterLayer": 19},
            "fabric": {"cudaRuntime": "2.11.0+cu128",
                       "rocmRuntime": "2.10.0+rocm7.0"},
            "stages": [{"role": "cuda", "deviceIdentity": "nvidia:GPU-x"},
                       {"role": "rocm", "deviceIdentity": "amd:pci-0"}],
        }

    def identity(self) -> dict:
        return {"openmyceliumVersion": "0.3.0a1",
                "openmyceliumContent": "c" * 64,
                "mcclVersion": "0.2.0a3",
                "mcclContent": "d" * 64}

    def test_every_field_comes_from_the_manifest_or_the_build(self):
        from adapters.qualification import (SITUATION_FIELDS,
                                            situation_from_manifest)
        situation = situation_from_manifest(self.manifest(), self.identity())
        self.assertEqual(sorted(situation), sorted(SITUATION_FIELDS))
        self.assertEqual(situation["cudaRuntime"], "2.11.0+cu128")
        self.assertEqual(situation["rocmRuntime"], "2.10.0+rocm7.0")
        self.assertIn("boundary after layer 19", situation["topology"])
        self.assertIn("nvidia:GPU-x", situation["topology"])
        self.assertIn("amd:pci-0", situation["topology"])

    def test_the_topology_is_stable_under_stage_order(self):
        from adapters.qualification import situation_from_manifest
        manifest = self.manifest()
        reversed_manifest = self.manifest()
        reversed_manifest["stages"] = list(reversed(manifest["stages"]))
        self.assertEqual(
            situation_from_manifest(manifest, self.identity())["topology"],
            situation_from_manifest(reversed_manifest, self.identity())["topology"])

    def test_a_manifest_without_runtimes_cannot_form_a_situation(self):
        """Better to refuse than to hash an empty string as if it were a fact."""
        from adapters.qualification import situation_from_manifest
        manifest = self.manifest()
        del manifest["fabric"]["cudaRuntime"]
        with self.assertRaises(ValueError):
            situation_from_manifest(manifest, self.identity())


if __name__ == "__main__":
    unittest.main()

