from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "serving"))
sys.path.insert(0, os.path.dirname(__file__))

from model_inspect import LayerGroup, ModelInspection, Tensor
from placement import (ManifestError, build_placement, load_placement,
                       stage_assignment, validate_for_worker, validate_manifest,
                       write_placement)


def model() -> ModelInspection:
    config = {
        "architectures": ["MistralForCausalLM"], "hidden_size": 4,
        "num_attention_heads": 2, "num_key_value_heads": 1,
        "head_dim": 2, "torch_dtype": "float32",
    }
    prologue = [Tensor("model.embed_tokens.weight", "F32", (10,), "one")]
    epilogue = [Tensor("lm_head.weight", "F32", (10,), "one")]
    layers = {}
    tensors = list(prologue)
    for index in range(4):
        tensor = Tensor(f"model.layers.{index}.weight", "F32", (10,), "one")
        layers[index] = LayerGroup(index, [tensor])
        tensors.append(tensor)
    tensors.extend(epilogue)
    return ModelInspection("/model", config, tensors, layers, prologue, epilogue)


def fabric():
    return {
        "schemaVersion": 2, "probedAt": 1.0, "fromCache": False,
        "identitiesUnique": True, "devices": [
            {"identity": "nvidia:GPU-test", "identity_source": "uuid",
             "identityConfidence": "strong", "runtime": "cuda",
             "freeBytes": 10000},
            {"identity": "amd:pci-0000:0c:00.0", "identity_source": "pci",
             "identityConfidence": "slot-stable", "runtime": "rocm",
             "freeBytes": 10000},
        ],
    }


class PlacementTests(unittest.TestCase):
    def build(self):
        return build_placement(model(), "/model", fabric(), 1000, 1000, 16,
                               job_id="pl-test")

    def test_one_authoritative_manifest_owns_every_tensor_once(self):
        manifest = self.build()
        names = [name for stage in manifest["stages"] for name in stage["tensors"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), {tensor.name for tensor in model().tensors})
        self.assertEqual(stage_assignment(manifest, "cuda").device_identity,
                         "nvidia:GPU-test")
        self.assertEqual(stage_assignment(manifest, "rocm").device_identity,
                         "amd:pci-0000:0c:00.0")

    def test_tampering_is_rejected(self):
        manifest = self.build()
        manifest["pipeline"]["boundaryAfterLayer"] += 1
        with self.assertRaisesRegex(ManifestError, "digest mismatch"):
            validate_manifest(manifest)

    def test_model_drift_is_rejected(self):
        manifest = self.build()
        changed = model()
        changed.config["hidden_size"] = 8
        with self.assertRaisesRegex(ManifestError, "different model"):
            validate_for_worker(manifest, changed, "cuda")

    def test_duplicate_fabric_identity_is_rejected(self):
        report = fabric()
        report["identitiesUnique"] = False
        report["duplicateIdentities"] = ["same"]
        with self.assertRaisesRegex(ManifestError, "ambiguous"):
            build_placement(model(), "/model", report, 1000, 1000, 16)

    def test_unstable_identity_is_rejected(self):
        report = fabric()
        report["devices"][1]["identity_source"] = "index"
        with self.assertRaisesRegex(ManifestError, "unstable index"):
            build_placement(model(), "/model", report, 1000, 1000, 16)

    def test_budget_above_current_free_memory_is_rejected(self):
        with self.assertRaisesRegex(ManifestError, "exceeds Fabric free"):
            build_placement(model(), "/model", fabric(), 10001, 1000, 16)

    def test_atomic_write_and_load(self):
        manifest = self.build()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "placement.json")
            write_placement(path, manifest)
            loaded = load_placement(path)
        self.assertEqual(loaded["manifestDigest"], manifest["manifestDigest"])

    def test_same_device_cannot_back_both_stages(self):
        manifest = self.build()
        manifest["stages"][1]["deviceIdentity"] = manifest["stages"][0]["deviceIdentity"]
        manifest["manifestDigest"] = __import__("placement")._digest(manifest)
        with self.assertRaisesRegex(ManifestError, "same Fabric identity"):
            validate_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
