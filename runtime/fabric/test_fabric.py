from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

import fabric


def torch_record(runtime: str, **overrides):
    record = {
        "available": True,
        "runtime": runtime,
        "name": "test gpu",
        "uuid": None,
        "pciBus": None,
        "pciDevice": None,
        "pciDomain": None,
        "free": 8 << 30,
        "total": 16 << 30,
        "torch": "test",
        "arch": "test",
    }
    record.update(overrides)
    return record


class FabricIdentityTests(unittest.TestCase):
    def test_amd_prefers_complete_pci_location(self):
        info = torch_record("rocm", pciBus=12, pciDevice=0, pciDomain=0,
                            uuid="unstable")
        with mock.patch.object(fabric, "_torch_probe", return_value=info):
            device = fabric.probe_amd(sys.executable)[0]
        self.assertEqual(device.identity, "amd:pci-0000:0c:00.0")
        self.assertEqual(device.identity_source, fabric.IDENTITY_PCI)
        self.assertEqual(device.identity_confidence, "slot-stable")
        self.assertFalse(device.uuid_trusted)

    def test_amd_bus_only_is_explicitly_weaker(self):
        info = torch_record("rocm", pciBus=12)
        with mock.patch.object(fabric, "_torch_probe", return_value=info):
            device = fabric.probe_amd(sys.executable)[0]
        self.assertEqual(device.identity, "amd:pci-bus-0c")
        self.assertEqual(device.identity_source, fabric.IDENTITY_PCI_BUS)
        self.assertEqual(device.identity_confidence, "bus-stable")

    def test_stringified_missing_nvidia_uuid_is_not_identity(self):
        info = torch_record("cuda", uuid="None")
        with mock.patch.object(fabric, "_torch_probe", return_value=info), \
                mock.patch.object(fabric, "_nvidia_smi", return_value=None):
            device = fabric.probe_nvidia(sys.executable)[0]
        self.assertEqual(device.identity, "nvidia:index-0")
        self.assertEqual(device.identity_source, fabric.IDENTITY_INDEX)

    def test_cache_schema_change_forces_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fabric.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"schemaVersion": fabric.CACHE_SCHEMA_VERSION - 1}, handle)
            self.assertIsNone(fabric._read_cache(path, 60))

    def test_duplicate_identity_is_a_fault(self):
        duplicate = fabric.Device("amd", "amd:pci-0000:0c:00.0",
                                  fabric.IDENTITY_PCI)
        probes = {"amd": lambda _: [duplicate, duplicate]}
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(fabric, "PROBES", probes):
            report = fabric.discover(
                {"amd": sys.executable}, use_cache=False,
                cache_path=os.path.join(directory, "fabric.json"))
        self.assertFalse(report["identitiesUnique"])
        self.assertEqual(report["duplicateIdentities"], [duplicate.identity])

    def test_atomic_cache_round_trip(self):
        report = {"schemaVersion": fabric.CACHE_SCHEMA_VERSION,
                  "probedAt": time.time(), "devices": []}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fabric.json")
            fabric._write_cache(path, report)
            self.assertEqual(fabric._read_cache(path, 60), report)


if __name__ == "__main__":
    unittest.main()
