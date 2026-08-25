import unittest

from hetccl.collective_frame import (
    AllGatherAssembler,
    FENCE_COLLECTIVE,
    FENCE_CONNECTION,
    ConnectionFenced,
    ConnectionMultiplexer,
    CollectiveError,
    CollectiveFrame,
    CollectiveTiming,
    FrameGate,
    StaleFrameError,
    degeneracy_note,
    frame_for,
)


def make(**over):
    fields = dict(operation="broadcast", collective_id=1, epoch=0,
                  root_vendor="cuda", root_rank=0, sequence=0,
                  dtype="f32", shape=(4, 8))
    fields.update(over)
    return CollectiveFrame(**fields)


class FramingTests(unittest.TestCase):
    def test_round_trip_preserves_every_field(self):
        f = make(collective_id=7, epoch=3, root_vendor="rocm", root_rank=2,
                 sequence=5, dtype="f16", shape=(2, 3, 4))
        back = CollectiveFrame.unpack(f.pack())
        for attr in ("operation", "collective_id", "epoch", "root_vendor",
                     "root_rank", "sequence", "dtype", "shape"):
            self.assertEqual(getattr(back, attr), getattr(f, attr), attr)

    def test_root_may_be_either_vendor(self):
        for vendor in ("cuda", "rocm"):
            back = CollectiveFrame.unpack(make(root_vendor=vendor).pack())
            self.assertEqual(back.root_vendor, vendor)

    def test_every_dtype_survives_the_wire(self):
        for dtype in ("f16", "bf16", "f32", "f64", "i8", "i32", "i64", "u8"):
            back = CollectiveFrame.unpack(make(dtype=dtype, shape=(6,)).pack())
            self.assertEqual(back.byte_size, make(dtype=dtype, shape=(6,)).byte_size)

    def test_zero_length_tensor_is_legal(self):
        f = make(shape=(0,))
        self.assertTrue(f.is_empty)
        self.assertEqual(f.byte_size, 0)
        f.validate()
        self.assertEqual(CollectiveFrame.unpack(f.pack()).shape, (0,))

    def test_zero_dimension_among_others_is_legal(self):
        f = make(shape=(4, 0, 8))
        self.assertTrue(f.is_empty)
        self.assertEqual(CollectiveFrame.unpack(f.pack()).shape, (4, 0, 8))

    def test_negative_dimension_is_rejected(self):
        with self.assertRaises(CollectiveError):
            make(shape=(4, -1)).validate()

    def test_bad_operation_root_and_dtype_are_rejected(self):
        for bad in (make(operation="all-reduce"), make(root_vendor="tpu"),
                    make(dtype="f8"), make(root_rank=-1),
                    make(collective_id=-1), make(epoch=-1), make(sequence=-1)):
            with self.assertRaises(CollectiveError):
                bad.validate()

    def test_corrupt_wire_frames_are_rejected(self):
        good = make().pack()
        with self.assertRaises(CollectiveError):
            CollectiveFrame.unpack(good[:6])
        with self.assertRaises(CollectiveError):
            CollectiveFrame.unpack(b"\x00\x00\x00\x00" + good[4:])

    def test_payload_length_must_agree_with_shape_and_dtype(self):
        # Rewrite the declared byte count so it no longer matches the shape.
        # The offset is derived, not hardcoded: an offset that silently lands on
        # a different field would make this test pass while proving nothing.
        import struct
        from hetccl.collective_frame import _NBYTES_OFFSET as offset
        raw = bytearray(make(dtype="f32", shape=(4, 8)).pack())
        struct.pack_into("<Q", raw, offset, 999)
        with self.assertRaises(CollectiveError):
            CollectiveFrame.unpack(bytes(raw))


class GateTests(unittest.TestCase):
    def test_frames_are_accepted_in_order(self):
        gate = FrameGate(collective_id=1, epoch=0)
        for seq in range(3):
            gate.accept(make(sequence=seq))
        self.assertEqual(gate.next_sequence, 3)

    def test_out_of_order_frame_is_rejected(self):
        gate = FrameGate(collective_id=1)
        gate.accept(make(sequence=0))
        with self.assertRaises(CollectiveError):
            gate.accept(make(sequence=2))

    def test_frame_from_another_collective_is_rejected(self):
        gate = FrameGate(collective_id=1)
        with self.assertRaises(CollectiveError):
            gate.accept(make(collective_id=2))

    def test_failure_fences_off_frames_in_flight(self):
        gate = FrameGate(collective_id=1, epoch=0)
        gate.accept(make(sequence=0))
        new_epoch = gate.fail()
        self.assertEqual(new_epoch, 1)
        with self.assertRaises(StaleFrameError):
            gate.accept(make(sequence=1, epoch=0))

    def test_stale_epoch_is_rejected_after_recovery(self):
        gate = FrameGate(collective_id=1, epoch=0)
        gate.fail()
        gate.reset_for(2)
        # A frame left in flight by the failed collective must not be consumed.
        with self.assertRaises(StaleFrameError):
            gate.accept(make(collective_id=2, epoch=0, sequence=0))
        gate.accept(make(collective_id=2, epoch=1, sequence=0))

    def test_reset_clears_the_failed_flag(self):
        gate = FrameGate(collective_id=1)
        gate.fail()
        gate.reset_for(5)
        self.assertFalse(gate.failed)
        self.assertEqual(gate.collective_id, 5)


class TimingTests(unittest.TestCase):
    def test_three_phases_are_reported_separately(self):
        t = CollectiveTiming(local_ms=0.4, bridge_ms=2.1, total_ms=2.7,
                             local_degenerate=True)
        payload = t.to_dict()
        self.assertEqual(payload["localCollectiveMs"], 0.4)
        self.assertEqual(payload["bridgeMs"], 2.1)
        self.assertEqual(payload["totalMs"], 2.7)
        self.assertTrue(payload["localDegenerate"])


class DegeneracyTests(unittest.TestCase):
    def test_single_rank_groups_are_flagged(self):
        note = degeneracy_note({"cuda": 1, "rocm": 1})
        self.assertIsNotNone(note)
        self.assertIn("cuda", note)
        self.assertIn("rocm", note)
        self.assertIn("degenerate", note)

    def test_multi_rank_groups_need_no_note(self):
        self.assertIsNone(degeneracy_note({"cuda": 4, "rocm": 4}))

    def test_partially_degenerate_names_only_the_small_group(self):
        note = degeneracy_note({"cuda": 4, "rocm": 1})
        self.assertIn("rocm", note)
        self.assertNotIn("cuda,", note)


class HelperTests(unittest.TestCase):
    def test_frame_for_builds_a_validated_frame(self):
        f = frame_for((2, 2), "f32", root_vendor="rocm", collective_id=3)
        self.assertEqual(f.root_vendor, "rocm")
        self.assertEqual(f.byte_size, 16)


class AllGatherTests(unittest.TestCase):
    """Ordering must come from rank metadata, never from arrival order."""

    def contribution(self, rank, world=4, cid=1, epoch=0, dtype="f32", shape=(2, 3)):
        f = CollectiveFrame("all-gather", cid, epoch, "cuda", rank, 0, dtype, shape)
        return f, bytes([rank + 1]) * f.byte_size

    def test_output_is_rank_ordered_regardless_of_arrival(self):
        forward = AllGatherAssembler(4, 1, 0)
        reverse = AllGatherAssembler(4, 1, 0)
        for r in range(4):
            forward.accept(*self.contribution(r))
        for r in reversed(range(4)):
            reverse.accept(*self.contribution(r))
        self.assertEqual(forward.result(), reverse.result())
        # And the bytes really are in rank order, not merely equal to each other.
        expected = b"".join(bytes([r + 1]) * 24 for r in range(4))
        self.assertEqual(forward.result(), expected)

    def test_interleaved_arrival_still_orders_correctly(self):
        a = AllGatherAssembler(4, 1, 0)
        for r in (2, 0, 3, 1):
            a.accept(*self.contribution(r))
        self.assertEqual(a.result(), b"".join(bytes([r + 1]) * 24 for r in range(4)))

    def test_no_designated_root_is_required(self):
        a = AllGatherAssembler(2, 1, 0)
        for r in range(2):
            a.accept(*self.contribution(r, world=2))
        self.assertTrue(a.is_complete)

    def test_duplicate_rank_is_rejected(self):
        a = AllGatherAssembler(4, 1, 0)
        a.accept(*self.contribution(1))
        with self.assertRaises(CollectiveError):
            a.accept(*self.contribution(1))

    def test_out_of_range_rank_is_rejected(self):
        a = AllGatherAssembler(2, 1, 0)
        for bad in (2, 7, -1):
            with self.assertRaises(CollectiveError):
                a.accept(*self.contribution(bad, world=2))

    def test_stale_epoch_contribution_is_rejected(self):
        a = AllGatherAssembler(4, 1, epoch=2)
        with self.assertRaises(StaleFrameError):
            a.accept(*self.contribution(0, epoch=1))

    def test_foreign_collective_id_is_rejected(self):
        a = AllGatherAssembler(4, 1, 0)
        with self.assertRaises(CollectiveError):
            a.accept(*self.contribution(0, cid=9))

    def test_mismatched_dtype_and_shape_are_rejected(self):
        a = AllGatherAssembler(4, 1, 0)
        a.accept(*self.contribution(0))
        with self.assertRaises(CollectiveError):
            a.accept(*self.contribution(1, dtype="f16"))
        with self.assertRaises(CollectiveError):
            a.accept(*self.contribution(2, shape=(9, 9)))

    def test_payload_length_must_match_the_frame(self):
        a = AllGatherAssembler(4, 1, 0)
        f, _ = self.contribution(0)
        with self.assertRaises(CollectiveError):
            a.accept(f, b"short")

    def test_incomplete_output_is_never_returned(self):
        a = AllGatherAssembler(4, 1, 0)
        a.accept(*self.contribution(0))
        a.accept(*self.contribution(2))
        self.assertFalse(a.is_complete)
        self.assertEqual(a.missing, (1, 3))
        with self.assertRaises(CollectiveError):
            a.result()

    def test_fencing_discards_partial_state_and_bumps_epoch(self):
        a = AllGatherAssembler(4, 1, 0)
        a.accept(*self.contribution(0))
        self.assertEqual(a.fail(), 1)
        with self.assertRaises(StaleFrameError):
            a.result()
        with self.assertRaises(StaleFrameError):
            a.accept(*self.contribution(1, epoch=1))

    def test_allocation_shape_derives_from_validated_metadata(self):
        a = AllGatherAssembler(4, 1, 0)
        with self.assertRaises(CollectiveError):
            a.output_shape()          # nothing validated yet, so nothing to size
        a.accept(*self.contribution(0))
        self.assertEqual(a.output_shape(), (8, 3))

    def test_broadcast_frame_is_refused_by_the_gatherer(self):
        a = AllGatherAssembler(2, 1, 0)
        f = CollectiveFrame("broadcast", 1, 0, "cuda", 0, 0, "f32", (2, 3))
        with self.assertRaises(CollectiveError):
            a.accept(f, b"\x00" * f.byte_size)


class MultiplexerTests(unittest.TestCase):
    """Interleaved collectives on one persistent connection."""

    def frame(self, cid, seq, epoch=0, op="broadcast"):
        return CollectiveFrame(op, cid, epoch, "cuda", 0, seq, "f32", (2, 2))

    def test_two_concurrent_collectives_progress_independently(self):
        m = ConnectionMultiplexer()
        m.open(1); m.open(2)
        m.accept(self.frame(1, 0))
        m.accept(self.frame(2, 0))
        m.accept(self.frame(1, 1))
        m.accept(self.frame(2, 1))
        self.assertEqual(m.state()["nextSequence"], {1: 2, 2: 2})
        self.assertFalse(m.fenced)

    def test_frames_may_interleave_in_any_order(self):
        m = ConnectionMultiplexer()
        m.open(7); m.open(8)
        for cid, seq in ((8, 0), (7, 0), (8, 1), (7, 1), (7, 2), (8, 2)):
            m.accept(self.frame(cid, seq))
        self.assertEqual(m.state()["nextSequence"], {7: 3, 8: 3})

    def test_opposite_order_delivery_is_accepted_per_collective(self):
        # Interleaving across collectives is free; ordering within one is not.
        m = ConnectionMultiplexer()
        m.open(1); m.open(2)
        m.accept(self.frame(2, 0))
        m.accept(self.frame(1, 0))
        self.assertFalse(m.fenced)

    def test_duplicate_sequence_fences_the_connection(self):
        m = ConnectionMultiplexer()
        m.open(1); m.open(2)
        m.accept(self.frame(1, 0))
        with self.assertRaises(ConnectionFenced):
            m.accept(self.frame(1, 0))          # replayed sequence
        self.assertTrue(m.fenced)
        self.assertEqual(m.state()["fencedBy"], 1)

    def test_out_of_order_sequence_fences_the_connection(self):
        m = ConnectionMultiplexer()
        m.open(1)
        m.accept(self.frame(1, 0))
        with self.assertRaises(ConnectionFenced):
            m.accept(self.frame(1, 5))

    def test_stale_epoch_fences_the_connection(self):
        m = ConnectionMultiplexer()
        m.open(1, epoch=3)
        with self.assertRaises(ConnectionFenced):
            m.accept(self.frame(1, 0, epoch=2))
        self.assertTrue(m.fenced)

    def test_frame_for_an_unopened_collective_fences(self):
        m = ConnectionMultiplexer()
        m.open(1)
        with self.assertRaises(ConnectionFenced):
            m.accept(self.frame(99, 0))
        self.assertIn("unopened", m.state()["fenceReason"])

    def test_one_collective_failing_takes_the_whole_connection(self):
        # The explicit policy: a violation in collective 1 stops collective 2,
        # because the shared byte stream can no longer be trusted.
        m = ConnectionMultiplexer()
        m.open(1); m.open(2)
        m.accept(self.frame(2, 0))
        with self.assertRaises(ConnectionFenced):
            m.accept(self.frame(1, 9))
        with self.assertRaises(ConnectionFenced):
            m.accept(self.frame(2, 1))          # healthy collective, still refused
        self.assertTrue(m.gates[2].failed)

    def test_no_frame_leaks_into_another_collective_after_failure(self):
        m = ConnectionMultiplexer()
        m.open(1); m.open(2)
        m.accept(self.frame(1, 0))
        try:
            m.accept(self.frame(1, 7))
        except ConnectionFenced:
            pass
        # Collective 2 never advances on a frame belonging to a failed neighbour.
        self.assertEqual(m.gates[2].next_sequence, 0)
        with self.assertRaises(ConnectionFenced):
            m.accept(self.frame(2, 0))

    def test_opening_a_new_collective_on_a_fenced_link_is_refused(self):
        m = ConnectionMultiplexer()
        m.open(1)
        m.fence(1, "test")
        with self.assertRaises(ConnectionFenced):
            m.open(3)

    def test_duplicate_open_is_rejected(self):
        m = ConnectionMultiplexer()
        m.open(1)
        with self.assertRaises(CollectiveError):
            m.open(1)

    def test_per_collective_fencing_policy_is_refused_not_downgraded(self):
        with self.assertRaises(CollectiveError):
            ConnectionMultiplexer(policy=FENCE_COLLECTIVE)

    def test_state_reports_the_active_policy(self):
        self.assertEqual(ConnectionMultiplexer().state()["policy"], FENCE_CONNECTION)


if __name__ == "__main__":
    unittest.main()
