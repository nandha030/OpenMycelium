import unittest

from hetccl.xvendor import (
    QUALIFIED_CHUNK_MAX,
    QUALIFIED_CHUNK_MIN,
    DEFAULT_CHUNK,
    DEFAULT_SLOTS,
    TRANSPORT,
    ActivationHeader,
    QualificationLedger,
    QualificationRecord,
    TransferMetrics,
    TransportConfig,
    TransportError,
    describe,
)


def record(direction="cuda->rocm", verified=True, **over):
    fields = dict(
        direction=direction, send_gpu="NVIDIA GeForce RTX 5060 Ti",
        recv_gpu="AMD Radeon RX 9060 XT", windows_version="10.0.26200",
        wsl_kernel="6.18.33.2-microsoft-standard-WSL2", cuda_driver="591.86",
        rocm_version="7.2.0", chunk_bytes=DEFAULT_CHUNK, slots=DEFAULT_SLOTS,
        verified_bytes=268435456, throughput_mbps=459.1, byte_verified=verified,
    )
    fields.update(over)
    return QualificationRecord(**fields)


class ActivationHeaderTests(unittest.TestCase):
    def test_round_trip_preserves_shape_and_dtype(self):
        h = ActivationHeader("f16", (4, 512, 1024), sequence=7)
        back = ActivationHeader.unpack(h.pack())
        self.assertEqual(back.dtype, "f16")
        self.assertEqual(back.shape, (4, 512, 1024))
        self.assertEqual(back.sequence, 7)

    def test_dynamic_shapes_are_supported_per_transfer(self):
        for shape in [(1,), (2, 3), (8, 128, 64), (2, 4, 8, 16, 32)]:
            h = ActivationHeader("f32", shape)
            self.assertEqual(ActivationHeader.unpack(h.pack()).shape, shape)

    def test_byte_size_tracks_dtype(self):
        self.assertEqual(ActivationHeader("f32", (10, 10)).byte_size, 400)
        self.assertEqual(ActivationHeader("f16", (10, 10)).byte_size, 200)
        self.assertEqual(ActivationHeader("i8", (10, 10)).byte_size, 100)

    def test_metadata_validation_rejects_bad_headers(self):
        for bad in [
            ActivationHeader("f9", (2, 2)),
            ActivationHeader("f32", ()),
            ActivationHeader("f32", (2, 0)),
            ActivationHeader("f32", (-1,)),
            ActivationHeader("f32", tuple(range(1, 11))),
        ]:
            with self.assertRaises(TransportError):
                bad.validate()

    def test_corrupt_wire_header_is_rejected(self):
        good = ActivationHeader("f32", (4, 4)).pack()
        with self.assertRaises(TransportError):
            ActivationHeader.unpack(good[:8])
        with self.assertRaises(TransportError):
            ActivationHeader.unpack(b"\x00\x00\x00\x00" + good[4:])


class ConfigTests(unittest.TestCase):
    def test_defaults_match_the_measured_plateau(self):
        c = TransportConfig()
        self.assertEqual(c.slots, 2)
        self.assertTrue(QUALIFIED_CHUNK_MIN <= c.chunk_bytes <= QUALIFIED_CHUNK_MAX)
        self.assertFalse(c.verify_payload)
        c.validate()

    def test_chunk_outside_the_qualified_profile_is_refused_by_default(self):
        for outside in (1024 * 1024, 64 * 1024 * 1024):
            with self.assertRaises(TransportError):
                TransportConfig(chunk_bytes=outside).validate()

    def test_unqualified_chunk_is_permitted_with_an_explicit_opt_in(self):
        # The profile is this platform's measurement, not a universal law:
        # another PCIe or NUMA layout may have a different optimum.
        TransportConfig(chunk_bytes=64 * 1024 * 1024,
                        allow_unqualified_chunk=True).validate()

    def test_chunk_outside_the_framing_limits_is_always_invalid(self):
        with self.assertRaises(TransportError):
            TransportConfig(chunk_bytes=64,
                            allow_unqualified_chunk=True).validate()

    def test_single_slot_is_refused(self):
        with self.assertRaises(TransportError):
            TransportConfig(slots=1).validate()

    def test_window_may_not_exceed_slots(self):
        with self.assertRaises(TransportError):
            TransportConfig(slots=2, window=4).validate()

    def test_qualification_mode_enables_payload_crc(self):
        self.assertTrue(TransportConfig.for_qualification().verify_payload)
        self.assertFalse(TransportConfig().verify_payload)


class LedgerTests(unittest.TestCase):
    def test_unqualified_direction_fails_closed(self):
        ledger = QualificationLedger()
        with self.assertRaises(TransportError):
            ledger.assert_permitted("cuda", "rocm")

    def test_qualification_is_directional(self):
        # The measured failure was asymmetric, so one direction must not imply
        # the other.
        ledger = QualificationLedger([record("rocm->cuda")])
        ledger.assert_permitted("rocm", "cuda")
        with self.assertRaises(TransportError):
            ledger.assert_permitted("cuda", "rocm")

    def test_unverified_run_cannot_be_recorded(self):
        with self.assertRaises(TransportError):
            QualificationLedger([record(verified=False)])

    def test_record_round_trips_through_json(self):
        ledger = QualificationLedger([record("cuda->rocm"), record("rocm->cuda")])
        back = QualificationLedger.from_json(ledger.to_json())
        self.assertEqual(back.directions(), ("cuda->rocm", "rocm->cuda"))
        self.assertEqual(back.get("cuda->rocm").rocm_version, "7.2.0")

    def test_record_captures_the_platform_tuple(self):
        r = record()
        for field in ("send_gpu", "recv_gpu", "windows_version", "wsl_kernel",
                      "cuda_driver", "rocm_version", "chunk_bytes", "slots"):
            self.assertTrue(getattr(r, field), f"{field} must be recorded")

    def test_incomplete_record_is_rejected(self):
        with self.assertRaises(TransportError):
            QualificationRecord.from_mapping({"direction": "cuda->rocm"})


class MetricsTests(unittest.TestCase):
    def test_throughput_and_latency_are_accumulated(self):
        m = TransferMetrics()
        m.observe(1024 * 1024, 0.001)
        m.observe(1024 * 1024, 0.003)
        self.assertEqual(m.transfers, 2)
        self.assertEqual(m.bytes_moved, 2 * 1024 * 1024)
        self.assertGreater(m.throughput_mbps, 0)
        self.assertAlmostEqual(m.mean_latency_ms, 2.0, places=3)

    def test_percentiles_on_an_empty_series_are_zero(self):
        self.assertEqual(TransferMetrics().percentile_ms(0.99), 0.0)

    def test_metrics_serialize_for_the_control_plane(self):
        m = TransferMetrics()
        m.observe(4 * 1024 * 1024, 0.01)
        payload = m.to_dict()
        self.assertIn("throughputMBps", payload)
        self.assertIn("p99LatencyMs", payload)


class DescribeTests(unittest.TestCase):
    def test_transport_declares_itself_point_to_point_not_collective(self):
        d = describe()
        self.assertEqual(d["transport"], TRANSPORT)
        self.assertEqual(d["kind"], "point-to-point")
        self.assertFalse(d["collective"])
        self.assertIn("activation", d["operations"])


if __name__ == "__main__":
    unittest.main()
