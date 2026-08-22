import unittest

from hetccl.discovery import CapabilityReport, Device
from hetccl.planner import plan_collective


class PlannerTests(unittest.TestCase):
    def report(self, runtime="", rdma="unavailable", direct="blocked-until-qualified"):
        devices = (Device("nvidia" if runtime == "cuda" else "amd", runtime, "virtual", 0),) if runtime else ()
        return CapabilityReport("host", "linux", "x86_64", devices, {"host": "ready"}, {"tcp": "ready", "rdma": rdma, "device_direct": direct})

    def test_heterogeneous_hosts_use_portable_backend_without_qualification(self):
        plan = plan_collective([self.report("cuda"), self.report("rocm")], prefer_device_direct=True)
        self.assertEqual(plan.backend, "tcp")
        self.assertTrue(plan.executable)
        self.assertIn("nccl", plan.native_backends)
        self.assertIn("rccl", plan.native_backends)
        self.assertTrue(plan.warnings)

    def test_qualified_direct_hosts_select_rdma(self):
        plan = plan_collective([
            self.report("cuda", "qualified", "qualified"),
            self.report("rocm", "qualified", "qualified"),
        ], prefer_device_direct=True)
        self.assertEqual(plan.backend, "rdma")
        self.assertEqual(plan.mode, "hierarchical-device-direct")


if __name__ == "__main__":
    unittest.main()
