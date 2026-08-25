import json
import subprocess
import unittest
from unittest import mock

from hetccl.amd import (
    AMDAccelerator,
    AMDInventory,
    AMDProcessor,
    discover_amd,
    discover_amd_gpus,
)


def runner(outputs):
    """Fake command runner returning canned stdout keyed by executable name."""

    def run(arguments, **_):
        for fragment, payload in outputs.items():
            if fragment in arguments[0]:
                return subprocess.CompletedProcess(arguments, 0, payload, "")
        return subprocess.CompletedProcess(arguments, 1, "", "not found")

    return run


ROCM_SMI_JSON = json.dumps(
    {
        "card0": {
            "Card series": "AMD Radeon RX 9060 XT",
            "VRAM Total Memory (B)": str(16 * 1024 * 1024 * 1024),
            "Driver version": "6.10.5",
            "Card SKU": "gfx1200",
        }
    }
)


class RocmSmiTests(unittest.TestCase):
    """`_command` resolves the binary on PATH first, so a fake runner alone is
    not enough; `shutil.which` has to answer for the tool as well."""

    def setUp(self):
        patcher = mock.patch("hetccl.amd.shutil.which", side_effect=lambda name: f"/usr/bin/{name}")
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_rocm_smi_devices_are_compute_ready(self):
        found = discover_amd_gpus(runner({"rocm-smi": ROCM_SMI_JSON}), system="Linux", tools=("rocm-smi",))
        self.assertEqual(len(found), 1)
        device = found[0]
        self.assertEqual(device.name, "AMD Radeon RX 9060 XT")
        self.assertEqual(device.memory_mib, 16384)
        self.assertTrue(device.compute_ready)
        self.assertEqual(device.compute_runtime, "rocm")
        self.assertEqual(device.runtime, "rocm")
        self.assertEqual(device.source, "rocm-smi")

    def test_malformed_rocm_output_yields_nothing_rather_than_guesses(self):
        found = discover_amd_gpus(runner({"rocm-smi": "not json"}), system="Linux", tools=("rocm-smi",))
        self.assertEqual(found, [])


class ComputeClaimTests(unittest.TestCase):
    """A card without a ROCm stack must never be presented as compute capacity."""

    def test_display_only_adapter_is_not_rocm(self):
        adapter = AMDAccelerator(name="AMD Radeon RX 9060 XT", index=0, memory_mib=16301)
        self.assertFalse(adapter.compute_ready)
        self.assertEqual(adapter.runtime, "amd-display")
        self.assertEqual(adapter.compute_runtime, "")

    def test_inventory_is_not_compute_ready_without_rocm(self):
        inventory = AMDInventory(accelerators=(AMDAccelerator("RX 9060 XT", 0, 16301),))
        self.assertFalse(inventory.compute_ready)

    def test_inventory_is_compute_ready_when_rocm_answers(self):
        ready = AMDAccelerator("RX 9060 XT", 0, 16384, compute_ready=True, compute_runtime="rocm")
        self.assertTrue(AMDInventory(accelerators=(ready,)).compute_ready)

    def test_windows_reports_that_rocm_has_no_windows_build(self):
        # rocm_tools() consults the real PATH, so the lookup must be stubbed:
        # otherwise this passes or fails depending on whether the machine
        # running the suite happens to have ROCm installed.
        with mock.patch("hetccl.amd.shutil.which", return_value=None):
            inventory = discover_amd(runner({}), system="Windows")
        self.assertEqual(inventory.rocm_tools, ())
        self.assertFalse(inventory.compute_ready)


class ProcessorTests(unittest.TestCase):
    def test_avx512_is_reported_from_simd_flags(self):
        zen5 = AMDProcessor("AMD Ryzen 7 9700X", simd=("avx", "avx2", "avx512f", "fma"))
        self.assertTrue(zen5.supports_avx512)

    def test_processor_without_avx512_reports_false(self):
        older = AMDProcessor("AMD Ryzen 5 3600", simd=("avx", "avx2", "fma"))
        self.assertFalse(older.supports_avx512)

    def test_processor_serializes_for_the_control_plane(self):
        payload = AMDProcessor("AMD Ryzen 7 9700X", physical_cores=8, logical_cores=16).to_dict()
        self.assertEqual(payload["physical_cores"], 8)
        self.assertEqual(payload["logical_cores"], 16)
        self.assertEqual(payload["vendor"], "amd")


if __name__ == "__main__":
    unittest.main()
