import unittest

from hetccl.qualification import (
    DIRECT,
    HOST_STAGED,
    VendorAccess,
    decide_receive_transport,
    qualify,
    run_probe,
)


class DecisionTests(unittest.TestCase):
    def test_verified_receive_permits_direct_transport(self):
        d = decide_receive_transport(
            VendorAccess("cuda", available=True, allocated=True,
                         host_store_ok=True, host_load_ok=True),
            transport_receive_verified=True,
        )
        self.assertEqual(d.transport, DIRECT)
        self.assertTrue(d.direct_receive_permitted)

    def test_inaccessible_memory_may_still_be_direct_when_verified(self):
        # The measured CUDA case: device memory faults on CPU access, yet the
        # transport receives into it correctly via the vendor copy API.
        d = decide_receive_transport(
            VendorAccess("cuda", available=True, allocated=True,
                         host_store_ok=False, host_load_ok=False),
            transport_receive_verified=True,
        )
        self.assertEqual(d.transport, DIRECT)

    def test_unverified_receive_fails_closed(self):
        d = decide_receive_transport(
            VendorAccess("cuda", available=True, allocated=True,
                         host_store_ok=True, host_load_ok=True)
        )
        self.assertEqual(d.transport, HOST_STAGED)
        self.assertIn("not been verified", " ".join(d.reasons))

    def test_failed_verification_forces_host_staging(self):
        # The measured ROCm-for-WSL case: allocation succeeds, CPU access does
        # not, and transport receive segfaults.
        d = decide_receive_transport(
            VendorAccess("rocm", available=True, allocated=True,
                         host_store_ok=False, host_load_ok=False),
            transport_receive_verified=False,
        )
        self.assertEqual(d.transport, HOST_STAGED)
        self.assertFalse(d.direct_receive_permitted)
        self.assertIn("faulted on host store and load", " ".join(d.reasons))

    def test_store_only_fault_is_reported(self):
        d = decide_receive_transport(
            VendorAccess("rocm", available=True, allocated=True,
                         host_store_ok=False, host_load_ok=True),
            transport_receive_verified=False,
        )
        self.assertEqual(d.transport, HOST_STAGED)
        self.assertIn("faulted on host store", " ".join(d.reasons))

    def test_missing_runtime_fails_closed(self):
        d = decide_receive_transport(VendorAccess("rocm"))
        self.assertEqual(d.transport, HOST_STAGED)
        self.assertFalse(d.direct_receive_permitted)

    def test_allocation_failure_fails_closed(self):
        d = decide_receive_transport(VendorAccess("cuda", available=True, allocated=False))
        self.assertEqual(d.transport, HOST_STAGED)


class QualifyTests(unittest.TestCase):
    def test_mixed_host_reflects_each_vendor_separately(self):
        decisions = qualify(
            {
                "cuda": {"available": True, "allocated": True,
                         "host_store_ok": False, "host_load_ok": False},
                "rocm": {"available": True, "allocated": True,
                         "host_store_ok": False, "host_load_ok": False},
            },
            transport_receive_verified={"cuda": True, "rocm": False},
        )
        self.assertEqual(decisions["cuda"].transport, DIRECT)
        self.assertEqual(decisions["rocm"].transport, HOST_STAGED)

    def test_empty_probe_output_stages_everything(self):
        decisions = qualify({})
        self.assertEqual(decisions["cuda"].transport, HOST_STAGED)
        self.assertEqual(decisions["rocm"].transport, HOST_STAGED)

    def test_malformed_payload_is_not_trusted(self):
        decisions = qualify({"rocm": "yes it works"})
        self.assertEqual(decisions["rocm"].transport, HOST_STAGED)


class ProbeRunnerTests(unittest.TestCase):
    def test_unparseable_probe_output_yields_empty_report(self):
        def run(_args, **_kw):
            class R:
                returncode = 0
                stdout = "not json"
            return R()
        self.assertEqual(run_probe("/bin/true", run=run), {})

    def test_failed_probe_yields_empty_report(self):
        def run(_args, **_kw):
            class R:
                returncode = 1
                stdout = "{}"
            return R()
        self.assertEqual(run_probe("/bin/true", run=run), {})




if __name__ == "__main__":
    unittest.main()
