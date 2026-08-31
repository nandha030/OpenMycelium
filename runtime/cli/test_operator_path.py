"""The operator path: the commands the product tells you to run must work.

Every defect covered here was found by trying to clear one red gate on the
console. None of them was in the safety or qualification logic -- that was
correct throughout. All of them were in the path around it, which is the part
nobody exercises because it only matters when something is already wrong.

The shape worth remembering: `qualify record`'s own docstring pointed at a gate
summary, the console told operators to use `run --json`, and the two had never
been run against each other. The ledger proves it -- every existing record came
from a gate script, never from the documented route.

Nothing here touches a GPU.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import qualify_cli  # noqa: E402


def run_document(exit_codes=None, tensors=(181, 182), declared=363,
                 generated=24, overlap=0):
    """A `run --json` document, shaped as the coordinator emits one."""
    cuda = [f"t{i}" for i in range(tensors[0])]
    rocm = [f"t{i}" for i in range(tensors[0] - overlap,
                                   tensors[0] + tensors[1] - overlap)]
    return {
        "exitCodes": {"rocm": 0, "cuda": 0} if exit_codes is None else exit_codes,
        "result": {"generatedTokens": list(range(generated)),
                   "stopReason": "length", "promptTokens": 14},
        "placement": {
            "placementId": "pl-test", "manifestDigest": "d" * 64,
            "model": {"tensorCount": declared},
            "stages": [{"tensors": cuda}, {"tensors": rocm}],
        },
    }


def written(document, prefix=""):
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8")
    handle.write(prefix + json.dumps(document))
    handle.close()
    return handle.name


class EvidenceFromRunTests(unittest.TestCase):
    """`qualify record --evidence` must accept what the console tells you to make."""

    def test_a_clean_run_document_is_accepted(self):
        evidence = qualify_cli._evidence(written(run_document()))
        self.assertEqual(evidence["failures"], 0)
        self.assertEqual(evidence["derivedFrom"], "run document")

    def test_the_verdict_is_computed_not_taken_on_trust(self):
        # The run says nothing about passing; the checks decide.
        derived = qualify_cli._verdict_from_run(run_document())
        self.assertEqual(derived["failures"], 0)
        self.assertEqual(derived["checksRun"], 4)

    def test_a_nonzero_exit_is_refused(self):
        path = written(run_document(exit_codes={"rocm": 0, "cuda": 5}))
        with self.assertRaises(SystemExit) as raised:
            qualify_cli._evidence(path)
        self.assertIn("failure", str(raised.exception))

    def test_a_duplicated_tensor_is_refused(self):
        # Disjoint layer ranges with a duplicated embedding would look fine and
        # be two copies on two devices. This is the substantive check.
        path = written(run_document(overlap=3))
        with self.assertRaises(SystemExit) as raised:
            qualify_cli._evidence(path)
        self.assertIn("failure", str(raised.exception))

    def test_a_missing_tensor_is_refused(self):
        path = written(run_document(declared=400))
        with self.assertRaises(SystemExit):
            qualify_cli._evidence(path)

    def test_a_run_that_produced_no_tokens_is_refused(self):
        path = written(run_document(generated=0))
        with self.assertRaises(SystemExit):
            qualify_cli._evidence(path)

    def test_a_gate_summary_still_works_unchanged(self):
        # The existing producer must not regress: every record in the ledger
        # was written this way.
        path = written({"failures": 0, "gate": "full-gate-qualification"})
        evidence = qualify_cli._evidence(path)
        self.assertEqual(evidence["failures"], 0)
        self.assertNotIn("derivedFrom", evidence)

    def test_a_failing_gate_summary_is_still_refused(self):
        path = written({"failures": 2, "gate": "full-gate-qualification"})
        with self.assertRaises(SystemExit):
            qualify_cli._evidence(path)

    def test_something_that_is_neither_is_refused(self):
        path = written({"hello": "world"})
        with self.assertRaises(SystemExit) as raised:
            qualify_cli._evidence(path)
        self.assertIn("not a run document", str(raised.exception))

    def test_a_file_with_text_before_the_json_is_refused_by_default(self):
        # This test asserted the opposite until the strict-parsing authority
        # check reversed it. Tolerating prose meant a reader that hunts for the
        # first `{` and writes a qualification record from whatever follows,
        # which is a different and much weaker guarantee than reading evidence.
        # Legacy files are still importable, with --legacy-prose-evidence, and
        # the record is marked -- see test_qualification_scope.py.
        path = written(run_document(), prefix="Cross-vendor GPU inference is\n")
        with self.assertRaises(SystemExit) as raised:
            qualify_cli._evidence(path)
        self.assertIn("--legacy-prose-evidence", str(raised.exception))

    def test_a_byte_order_mark_is_not_reported_as_prose(self):
        # PowerShell's `>` writes a UTF-8 BOM. Counting it as text before the
        # JSON blamed the runtime for the shell's redirection, in a note that
        # told the operator to rebuild.
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8-sig")
        handle.write(json.dumps(run_document()))
        handle.close()
        evidence = qualify_cli._evidence(handle.name)
        self.assertEqual(evidence["failures"], 0)

    def test_a_file_with_no_json_at_all_is_refused(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8")
        handle.write("not json, no document here")
        handle.close()
        with self.assertRaises(SystemExit):
            qualify_cli._evidence(handle.name)


class StdoutPurityTests(unittest.TestCase):
    """Under --json, stdout carries the document and nothing else."""

    def test_tokens_are_written_to_stderr_when_json_is_asked_for(self):
        source = os.path.join(_HERE, "coordinator.py")
        with open(source, "r", encoding="utf-8") as handle:
            text = handle.read()
        # The token write must choose its stream from args.json rather than
        # writing to stdout unconditionally.
        self.assertIn("sys.stderr if self.args.json else sys.stdout", text)
        self.assertNotIn("            if not self.args.quiet:\n"
                         "                sys.stdout.write(piece)", text)


class ConsoleRemediationTests(unittest.TestCase):
    """The gate names a command that works, or it names nothing useful."""

    def setUp(self):
        source = os.path.join(_HERE, "console.py")
        with open(source, "r", encoding="utf-8") as handle:
            self.text = handle.read()

    def test_the_remediation_asks_for_json(self):
        # Without --json the file it tells you to create is not a document.
        self.assertIn("--json > gate.json", self.text)

    def test_the_remediation_sets_the_actor_on_both_steps(self):
        # `qualify record` needs the override too; an instruction that sets it
        # only on the first step fails at the second.
        detail = self.text[self.text.index("no record covers"):]
        detail = detail[:detail.index('"""') if '"""' in detail else 1200]
        self.assertEqual(detail.count("OM_QUALIFICATION_ACTOR"), 2)


if __name__ == "__main__":
    unittest.main()
