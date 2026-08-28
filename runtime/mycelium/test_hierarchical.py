import unittest

from mycelium.hierarchical import (
    HierarchicalCollective,
    HierarchyConfig,
    Topology,
    VendorGroup,
    expected_all_reduce,
)


class TopologyTests(unittest.TestCase):
    def test_spec_partitions_global_ranks_in_order(self):
        topology = Topology.from_spec("cuda:2,rocm:2")
        self.assertEqual(topology.world_size, 4)
        self.assertEqual(topology.group_count, 2)
        self.assertEqual(topology.groups[0].ranks, (0, 1))
        self.assertEqual(topology.groups[1].ranks, (2, 3))
        self.assertEqual(topology.leaders, (0, 2))
        self.assertTrue(topology.is_heterogeneous)

    def test_single_vendor_is_not_heterogeneous(self):
        self.assertFalse(Topology.from_spec("cuda:4").is_heterogeneous)

    def test_group_of_finds_the_owning_group(self):
        topology = Topology.from_spec("cuda:2,rocm:3")
        self.assertEqual(topology.group_of(1).vendor, "cuda")
        self.assertEqual(topology.group_of(4).vendor, "rocm")
        with self.assertRaises(ValueError):
            topology.group_of(5)

    def test_native_backend_names_the_vendor_library(self):
        self.assertEqual(VendorGroup(0, "cuda", (0,)).native_backend, "nccl")
        self.assertEqual(VendorGroup(0, "rocm", (0,)).native_backend, "rccl")
        self.assertEqual(VendorGroup(0, "cpu", (0,)).native_backend, "gloo")

    def test_rejects_unsupported_vendor(self):
        with self.assertRaises(ValueError):
            Topology.from_spec("quantum:2")

    def test_rejects_malformed_and_empty_specs(self):
        for spec in ("cuda", "cuda:0", "cuda:x", ""):
            with self.assertRaises(ValueError):
                Topology.from_spec(spec)

    def test_rejects_ranks_that_do_not_cover_the_world(self):
        broken = Topology((VendorGroup(0, "cuda", (0, 1)), VendorGroup(1, "rocm", (3, 4))))
        with self.assertRaises(ValueError):
            broken.validate()

    def test_rejects_duplicate_ranks_across_groups(self):
        broken = Topology((VendorGroup(0, "cuda", (0, 1)), VendorGroup(1, "rocm", (1, 2))))
        with self.assertRaises(ValueError):
            broken.validate()


class HierarchyConfigTests(unittest.TestCase):
    def test_reads_topology_and_rank_from_the_environment(self):
        config = HierarchyConfig.from_environment(
            {"MYCELIUM_TOPOLOGY": "cuda:2,rocm:2", "RANK": "3", "MCCL_COORDINATOR_PORT": "29777"}
        )
        self.assertEqual(config.rank, 3)
        self.assertEqual(config.coordinator_port, 29777)
        self.assertEqual(config.topology.world_size, 4)

    def test_requires_a_topology(self):
        with self.assertRaises(ValueError):
            HierarchyConfig.from_environment({"RANK": "0"})

    def test_rejects_a_rank_outside_the_world(self):
        with self.assertRaises(ValueError):
            HierarchyConfig.from_environment({"MYCELIUM_TOPOLOGY": "cuda:2", "RANK": "2"})


class _CPUOnlyTorch:
    """Minimal stand-in so topology behaviour is testable without PyTorch."""

    class cuda:
        @staticmethod
        def is_available():
            return False


class LeaderSelectionTests(unittest.TestCase):
    """Leader election and bridge topology must hold without PyTorch present."""

    def _collective(self, rank, spec="cuda:2,rocm:2"):
        config = HierarchyConfig(rank=rank, topology=Topology.from_spec(spec))
        return HierarchicalCollective(config, torch_module=_CPUOnlyTorch())

    def test_lowest_rank_in_each_group_is_the_leader(self):
        self.assertTrue(self._collective(0).is_leader)
        self.assertFalse(self._collective(1).is_leader)
        self.assertTrue(self._collective(2).is_leader)
        self.assertFalse(self._collective(3).is_leader)

    def test_exactly_one_leader_per_vendor_group(self):
        topology = Topology.from_spec("cuda:3,rocm:2,cpu:2")
        leaders = [rank for rank in range(topology.world_size) if rank in topology.leaders]
        self.assertEqual(len(leaders), topology.group_count)

    def test_bridge_world_size_is_the_group_count_not_the_rank_count(self):
        # The bridge carries one participant per vendor group, so an eight-rank
        # job crosses the vendor boundary with a world size of two.
        collective = self._collective(0, "cuda:4,rocm:4")
        self.assertEqual(collective.config.topology.world_size, 8)
        self.assertEqual(collective.config.topology.group_count, 2)

    def test_backend_is_chosen_per_group_not_per_local_build(self):
        # Every rank must agree on every group's backend, so the choice depends
        # only on the group's vendor. A CPU rank still names nccl for the CUDA
        # group it is not a member of.
        cpu_rank = self._collective(2, "cuda:2,cpu:2")
        groups = cpu_rank.config.topology.groups
        self.assertEqual(cpu_rank.backend_for(groups[0]), "nccl")
        self.assertEqual(cpu_rank.backend_for(groups[1]), "gloo")
        self.assertEqual(cpu_rank.intra_backend, "gloo")

    def test_rocm_group_also_selects_the_nccl_backend_name(self):
        collective = self._collective(2, "cuda:2,rocm:2")
        self.assertEqual(collective.intra_backend, "nccl")
        self.assertEqual(collective.group.native_backend, "rccl")

    def test_describe_reports_the_vendor_library_and_bridge(self):
        described = self._collective(2).describe()
        self.assertEqual(described["vendor"], "rocm")
        self.assertEqual(described["nativeBackend"], "rccl")
        self.assertEqual(described["bridge"], "mccl-tcp")
        self.assertTrue(described["isLeader"])
        self.assertTrue(described["heterogeneous"])


class GroundTruthTests(unittest.TestCase):
    def test_expected_values_are_independent_of_the_collective(self):
        contributions = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(expected_all_reduce(contributions, "sum"), 10.0)
        self.assertEqual(expected_all_reduce(contributions, "min"), 1.0)
        self.assertEqual(expected_all_reduce(contributions, "max"), 4.0)
        self.assertEqual(expected_all_reduce(contributions, "avg"), 2.5)

    def test_rejects_an_unknown_reduction(self):
        with self.assertRaises(ValueError):
            expected_all_reduce([1.0], "product")


class BridgeNecessityTests(unittest.TestCase):
    """A homogeneous job must not depend on the cross-vendor coordinator."""

    def _collective(self, rank, spec):
        config = HierarchyConfig(rank=rank, topology=Topology.from_spec(spec))
        return HierarchicalCollective(config, torch_module=_CPUOnlyTorch())

    def test_single_group_needs_no_bridge(self):
        self.assertFalse(self._collective(0, "cuda:4").needs_bridge)

    def test_multiple_groups_need_the_bridge(self):
        self.assertTrue(self._collective(0, "cuda:2,rocm:2").needs_bridge)
        self.assertTrue(self._collective(0, "cpu:1,cpu:1,cpu:1").needs_bridge)


if __name__ == "__main__":
    unittest.main()
