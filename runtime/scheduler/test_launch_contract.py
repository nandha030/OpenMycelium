from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "cli"))

from coordinator import Coordinator


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


if __name__ == "__main__":
    unittest.main()
