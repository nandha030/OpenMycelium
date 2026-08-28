import concurrent.futures
import unittest

from mccl.collective import CollectiveConfig, MCCLCollective
from mccl.protocol import ProtocolError
from mccl.server import Coordinator


class CollectiveTests(unittest.TestCase):
    def test_two_rank_sum_and_sequence(self):
        with Coordinator("127.0.0.1", 0, timeout_seconds=3) as coordinator:
            host, port = coordinator.address
            clients = [MCCLCollective(CollectiveConfig(rank, 2, host, port, "test", 3)) for rank in range(2)]
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                first = list(executor.map(lambda item: item[1].all_reduce([item[0] + 1, 2.0], "f64"), enumerate(clients)))
                second = list(executor.map(lambda item: item[1].all_reduce([item[0] + 3], "i32", "max"), enumerate(clients)))
        self.assertEqual(first, [[3.0, 4.0], [3.0, 4.0]])
        self.assertEqual(second, [[4], [4]])

    def test_metadata_mismatch_fails_all_participants(self):
        with Coordinator("127.0.0.1", 0, timeout_seconds=2) as coordinator:
            host, port = coordinator.address
            clients = [MCCLCollective(CollectiveConfig(rank, 2, host, port, "mismatch", 2)) for rank in range(2)]
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(clients[0].all_reduce, [1.0], "f64"),
                    executor.submit(clients[1].all_reduce, [1.0, 2.0], "f64"),
                ]
                for future in futures:
                    with self.assertRaises(ProtocolError):
                        future.result()


if __name__ == "__main__":
    unittest.main()
