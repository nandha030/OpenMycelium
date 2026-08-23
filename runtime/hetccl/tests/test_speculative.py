import unittest

from hetccl.protocol import ProtocolError
from hetccl.speculative import DraftProposal, SpeculativeDecoder


class _Draft:
    def __init__(self, proposal):
        self.proposal = proposal

    def propose(self, context, max_tokens):
        return self.proposal


class _Target:
    def __init__(self, rows):
        self.rows = rows

    def score(self, context, candidates):
        return self.rows


class SpeculativeTests(unittest.TestCase):
    def test_accepts_draft_and_emits_target_bonus(self):
        draft = DraftProposal((1, 2), ({1: 0.8, 9: 0.2}, {2: 0.7, 8: 0.3}))
        target = ({1: 0.9, 9: 0.1}, {2: 0.8, 8: 0.2}, {5: 1.0})
        result = SpeculativeDecoder(_Draft(draft), _Target(target), seed=4).step([10], 2)
        self.assertEqual(result.tokens, (1, 2, 5))
        self.assertEqual(result.accepted_draft_tokens, 2)
        self.assertIsNone(result.rejected_at)

    def test_rejection_samples_positive_target_difference(self):
        draft = DraftProposal((1,), ({1: 1.0},))
        target = ({2: 1.0}, {3: 1.0})
        result = SpeculativeDecoder(_Draft(draft), _Target(target), seed=1).step([], 1)
        self.assertEqual(result.tokens, (2,))
        self.assertEqual(result.accepted_draft_tokens, 0)
        self.assertEqual(result.rejected_at, 0)

    def test_rejects_unnormalized_probability_rows(self):
        draft = DraftProposal((1,), ({1: 0.4},))
        with self.assertRaises(ProtocolError):
            SpeculativeDecoder(_Draft(draft), _Target(({1: 1.0}, {2: 1.0}))).step([], 1)


if __name__ == "__main__":
    unittest.main()
