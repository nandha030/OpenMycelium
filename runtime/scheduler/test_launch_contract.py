from __future__ import annotations

import argparse
import atexit
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "cli"))

# Before importing the coordinator, not in setUp: `control.CONTROL_DIR` is
# resolved from configuration at import time and then bound as a default
# argument, so a later patch would never be seen. Without this the test writes
# a control record into the real state directory -- which fails with
# PermissionError wherever /var/lib/openmycelium is not writable, and, worse,
# succeeds by writing into the live machine's state where it is.
_STATE = tempfile.mkdtemp(prefix="om-launch-contract-")
os.environ["OPENMYCELIUM_STATE"] = _STATE
atexit.register(shutil.rmtree, _STATE, True)

from coordinator import Coordinator  # noqa: E402
import control  # noqa: E402


class LaunchContractTests(unittest.TestCase):
    def args(self, work_dir: str):
        return argparse.Namespace(
            heartbeat_timeout=10.0, work_dir=work_dir, port=31970,
            placement=os.path.join(work_dir, "placement.json"),
            root="/repo", rocm_python="/rocm/python",
            cuda_python="/cuda/python", model="/model", prompt="hello",
            max_new_tokens=4, load_timeout=30.0, context_length=16,
            allow_cpu=False,
        )

    def test_both_workers_receive_the_same_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = Coordinator(self.args(directory))
            cuda = coordinator._command("cuda")
            rocm = coordinator._command("rocm")
        for command in (cuda, rocm):
            index = command.index("--placement")
            self.assertEqual(command[index + 1], os.path.join(directory,
                                                               "placement.json"))
            self.assertNotIn("--cuda-budget", command)
            self.assertNotIn("--rocm-budget", command)

    def test_worker_source_does_not_plan(self):
        path = os.path.join(ROOT, "serving", "pipeline_run.py")
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("plan_pipeline", source)
        self.assertIn("--placement", source)

    def test_persistent_chat_issues_placement_before_launch(self):
        path = os.path.join(ROOT, "cli", "chat.py")
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("prepare_placement(args)", source)
        self.assertIn('parser.add_argument("--cuda-budget"', source)
        self.assertIn('parser.add_argument("--rocm-budget"', source)

    def test_the_suite_writes_control_records_only_into_its_own_directory(self):
        """The isolation above is load-bearing; prove it rather than assume it.

        Without it this test module wrote into the machine's real state
        directory: a PermissionError where that is not writable, and silent
        contamination of live state where it is. The second is the worse
        outcome and the one that leaves no trace.
        """
        self.assertTrue(control.CONTROL_DIR.startswith(_STATE),
                        f"control records would go to {control.CONTROL_DIR}")
        with tempfile.TemporaryDirectory() as directory:
            Coordinator(self.args(directory))
        written = os.listdir(control.CONTROL_DIR)
        self.assertTrue(written, "the coordinator wrote no control record")
        self.assertTrue(all(name.endswith(".json") for name in written))


if __name__ == "__main__":
    unittest.main()
