import unittest

from hetccl.router import HetRouter, InferenceNode, InferenceRequest


class RouterTests(unittest.TestCase):
    def test_selects_disaggregated_prefill_and_decode_when_transfer_is_worthwhile(self):
        nodes = [
            InferenceNode("amd", "rocm", 32000, 1000, 100, network_mbps=10000),
            InferenceNode("nvidia", "cuda", 32000, 100, 1000, network_mbps=10000),
        ]
        request = InferenceRequest("model", 1000, 100, 16000, 16384)
        route = HetRouter().plan(request, nodes)
        self.assertEqual(route.mode, "disaggregated-prefill-decode")
        self.assertEqual([item.node_id for item in route.assignments], ["amd", "nvidia"])

    def test_selects_cross_vendor_speculation_when_it_beats_target_decode(self):
        nodes = [
            InferenceNode("m5", "metal", 64000, 50, 50, draft_tokens_per_second=1000, network_mbps=10000),
            InferenceNode("target", "cuda", 64000, 500, 100, network_mbps=10000),
        ]
        request = InferenceRequest("model", 1000, 100, 32000, 1024, allow_disaggregation=False, allow_speculative=True, minimum_acceptance_rate=0.9)
        route = HetRouter().plan(request, nodes)
        self.assertEqual(route.mode, "cross-vendor-speculative")
        self.assertEqual(route.assignments[1].node_id, "m5")

    def test_combines_amd_prefill_metal_draft_and_cuda_verification(self):
        nodes = [
            InferenceNode("amd", "rocm", 64000, 1800, 180, network_mbps=25000),
            InferenceNode("nvidia", "cuda", 80000, 900, 650, network_mbps=25000),
            InferenceNode("m5", "metal", 64000, 250, 120, draft_tokens_per_second=850, network_mbps=10000),
        ]
        request = InferenceRequest("model", 2048, 256, 24000, 524288, allow_disaggregation=True, allow_speculative=True, minimum_acceptance_rate=0.7)
        route = HetRouter().plan(request, nodes)
        self.assertEqual(route.mode, "disaggregated-speculative")
        self.assertEqual([item.node_id for item in route.assignments], ["amd", "m5", "nvidia"])


if __name__ == "__main__":
    unittest.main()
