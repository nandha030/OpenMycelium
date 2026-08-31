"""Two authority checks, machine-enforced rather than described.

Both existed only as prose before this. The scope of a record was a sentence
inside its evidence, which nothing read and nothing could refuse on; and the
evidence reader scanned forward to the first `{`, which means a file with
arbitrary text in front of it would still produce a qualification record.

A rule that no code consults is a comment.

Nothing here touches a GPU.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
for _relative in (".", "../serving", "../scheduler", "../fabric"):
    _path = os.path.normpath(os.path.join(_HERE, _relative))
    if _path not in sys.path:
        sys.path.insert(0, _path)

import qualify_cli  # noqa: E402
from adapters import AdapterError, QualificationScope  # noqa: E402
from adapters.qualification import (SITUATION_FIELDS,  # noqa: E402
                                    record_from_situation, record_scope,
                                    require_scope, scope_from_evidence)

SITUATION = {field: f"value-for-{field}" for field in SITUATION_FIELDS}

RUN_EVIDENCE = {"failures": 0, "gate": "single-run qualification",
                "derivedFrom": "run document"}
FULL_EVIDENCE = {"failures": 0, "gate": "full-gate-qualification"}


class ScopeDerivationTests(unittest.TestCase):
    def test_a_single_run_grants_execution_only(self):
        self.assertEqual(scope_from_evidence(RUN_EVIDENCE),
                         QualificationScope.EXECUTION_QUALIFIED)

    def test_the_full_gate_grants_full(self):
        self.assertEqual(scope_from_evidence(FULL_EVIDENCE),
                         QualificationScope.FULL_GATE_QUALIFIED)

    def test_evidence_cannot_promote_itself_by_declaring_it(self):
        # A hand-written file claiming the full scope, without naming a gate
        # that runs the battery, gets execution only. Unknown evidence gets the
        # least authority it could deserve, never the most.
        claimed = {"failures": 0, "gate": "something-i-made-up",
                   "qualificationScope": QualificationScope.FULL_GATE_QUALIFIED}
        self.assertEqual(scope_from_evidence(claimed),
                         QualificationScope.EXECUTION_QUALIFIED)

    def test_unrecognised_evidence_is_execution_not_full(self):
        self.assertEqual(scope_from_evidence({"failures": 0}),
                         QualificationScope.EXECUTION_QUALIFIED)
        self.assertEqual(scope_from_evidence(None),
                         QualificationScope.EXECUTION_QUALIFIED)


class RecordTests(unittest.TestCase):
    def test_the_scope_is_written_into_the_record_as_a_field(self):
        record = record_from_situation(SITUATION, RUN_EVIDENCE)
        self.assertEqual(record["qualificationScope"],
                         QualificationScope.EXECUTION_QUALIFIED)

    def test_a_record_written_before_this_field_existed_is_unknown(self):
        # Not full. A record cannot be retroactively assumed to have proven
        # something its evidence never claimed.
        self.assertEqual(record_scope({"recordId": "old"}),
                         QualificationScope.UNKNOWN)
        self.assertEqual(record_scope(None), QualificationScope.UNKNOWN)


class EnforcementTests(unittest.TestCase):
    """The half that refuses. Without this the field is decoration."""

    def test_execution_is_permitted_by_an_execution_record(self):
        records = [record_from_situation(SITUATION, RUN_EVIDENCE)]
        granted = require_scope(SITUATION,
                                QualificationScope.EXECUTION_QUALIFIED,
                                records=records)
        self.assertEqual(granted["qualificationScope"],
                         QualificationScope.EXECUTION_QUALIFIED)

    def test_a_single_run_record_does_not_permit_full_gate_work(self):
        # The whole point. Release sealing, an enforcement canary and paging
        # work ask for FULL_GATE_QUALIFIED and must not get it from one run.
        records = [record_from_situation(SITUATION, RUN_EVIDENCE)]
        with self.assertRaises(AdapterError) as raised:
            require_scope(SITUATION, QualificationScope.FULL_GATE_QUALIFIED,
                          records=records)
        self.assertIn("EXECUTION_QUALIFIED", str(raised.exception))
        self.assertIn("FULL_GATE_QUALIFIED", str(raised.exception))

    def test_a_full_gate_record_permits_full_gate_work(self):
        records = [record_from_situation(SITUATION, FULL_EVIDENCE)]
        granted = require_scope(SITUATION,
                                QualificationScope.FULL_GATE_QUALIFIED,
                                records=records)
        self.assertEqual(granted["qualificationScope"],
                         QualificationScope.FULL_GATE_QUALIFIED)

    def test_a_legacy_record_does_not_permit_full_gate_work(self):
        legacy = record_from_situation(SITUATION, FULL_EVIDENCE)
        legacy.pop("qualificationScope")
        with self.assertRaises(AdapterError):
            require_scope(SITUATION, QualificationScope.FULL_GATE_QUALIFIED,
                          records=[legacy])

    def test_no_record_at_all_permits_nothing(self):
        with self.assertRaises(AdapterError):
            require_scope(SITUATION, QualificationScope.EXECUTION_QUALIFIED,
                          records=[])

    def test_the_ladder_is_ordered(self):
        permits = QualificationScope.permits
        self.assertTrue(permits(QualificationScope.FULL_GATE_QUALIFIED,
                                QualificationScope.EXECUTION_QUALIFIED))
        self.assertFalse(permits(QualificationScope.EXECUTION_QUALIFIED,
                                 QualificationScope.FULL_GATE_QUALIFIED))
        self.assertFalse(permits(QualificationScope.UNKNOWN,
                                 QualificationScope.EXECUTION_QUALIFIED))


def written(text):
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8")
    handle.write(text)
    handle.close()
    return handle.name


RUN_DOC = {
    "exitCodes": {"cuda": 0, "rocm": 0},
    "result": {"generatedTokens": [1, 2, 3]},
    "placement": {"model": {"tensorCount": 2},
                  "stages": [{"tensors": ["a"]}, {"tensors": ["b"]}]},
}


class StrictParsingTests(unittest.TestCase):
    """Normal mode reads a document. It does not hunt for one."""

    def test_a_plain_document_is_accepted(self):
        evidence = qualify_cli._evidence(written(json.dumps(RUN_DOC)))
        self.assertEqual(evidence["failures"], 0)

    def test_leading_whitespace_is_accepted(self):
        evidence = qualify_cli._evidence(written("\n\n  " + json.dumps(RUN_DOC)))
        self.assertEqual(evidence["failures"], 0)

    def test_a_byte_order_mark_is_accepted(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8-sig")
        handle.write(json.dumps(RUN_DOC))
        handle.close()
        self.assertEqual(qualify_cli._evidence(handle.name)["failures"], 0)

    def test_prose_before_the_json_is_refused_by_default(self):
        path = written("Cross-vendor GPU inference is\n" + json.dumps(RUN_DOC))
        with self.assertRaises(SystemExit) as raised:
            qualify_cli._evidence(path)
        message = str(raised.exception)
        self.assertIn("Refusing to scan past it", message)
        self.assertIn("--legacy-prose-evidence", message)

    def test_prose_is_accepted_only_with_the_explicit_option(self):
        path = written("Cross-vendor GPU inference is\n" + json.dumps(RUN_DOC))
        evidence = qualify_cli._evidence(path, allow_prose=True)
        self.assertEqual(evidence["failures"], 0)

    def test_a_legacy_import_is_marked_in_the_record(self):
        # "must never silently create a qualification record" -- the record
        # carries the fact, so it is visible in the ledger afterwards and not
        # only in a console message nobody kept.
        path = written("tokens streamed here\n" + json.dumps(RUN_DOC))
        evidence = qualify_cli._evidence(path, allow_prose=True)
        self.assertTrue(evidence["evidenceLegacyImport"])
        self.assertEqual(evidence["evidenceProseBytesSkipped"], 21)

    def test_trailing_garbage_is_still_refused(self):
        path = written(json.dumps(RUN_DOC) + "\nand then some prose")
        with self.assertRaises(SystemExit):
            qualify_cli._evidence(path)

    def test_a_file_with_no_document_is_refused_even_with_the_option(self):
        path = written("no json here at all")
        with self.assertRaises(SystemExit):
            qualify_cli._evidence(path, allow_prose=True)


if __name__ == "__main__":
    unittest.main()
