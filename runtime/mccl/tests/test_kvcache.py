import struct
import unittest

from mccl.kvcache import KVCacheBroker, KVCacheClient, KVCacheDescriptor
from mccl.protocol import ProtocolError


class KVCacheTests(unittest.TestCase):
    def descriptor(self):
        return KVCacheDescriptor("cache-1", "tiny-model", "cuda", "f32", (2, 1, 1, 1, 2, 2), sequence=2)

    def test_authenticated_round_trip_and_delete(self):
        payload = struct.pack("<8f", *range(8))
        with KVCacheBroker("127.0.0.1", 0, max_bytes=1024, ttl_seconds=5, auth_token="secret") as broker:
            host, port = broker.address
            client = KVCacheClient(host, port, auth_token="secret")
            checksum = client.put(self.descriptor(), payload)
            record = client.get("cache-1")
            self.assertEqual(record.payload, payload)
            self.assertEqual(record.checksum, checksum)
            self.assertEqual(record.descriptor.shape, (2, 1, 1, 1, 2, 2))
            client.delete("cache-1")
            with self.assertRaises(ProtocolError):
                client.get("cache-1")

    def test_authentication_and_size_are_enforced(self):
        payload = struct.pack("<8f", *range(8))
        with KVCacheBroker("127.0.0.1", 0, max_bytes=1024, ttl_seconds=5, auth_token="secret") as broker:
            host, port = broker.address
            with self.assertRaises(ProtocolError):
                KVCacheClient(host, port, auth_token="wrong").put(self.descriptor(), payload)
            with self.assertRaises(ProtocolError):
                KVCacheClient(host, port, auth_token="secret").put(self.descriptor(), payload[:-1])


if __name__ == "__main__":
    unittest.main()
