import json
import unittest

from runtime.mycelium.adapter import RuntimeConfig


class RuntimeConfigTests(unittest.TestCase):
    def test_rank_is_derived_from_group_base_and_worker_index(self):
        environment = {
            "OPENMYCELIUM_EXECUTION_PLAN_ID": "plan-1",
            "OPENMYCELIUM_EXECUTION_PLAN_JSON": json.dumps({
                "id": "plan-1",
                "transport": "gloo",
                "optimization": {"algorithm": "Mycelium", "version": "1.0.0"},
            }),
            "OPENMYCELIUM_EXECUTION_TRANSPORT": "gloo",
            "OPENMYCELIUM_EXECUTION_GROUP_ID": "amd",
            "OPENMYCELIUM_EXECUTION_GROUP_SIZE": "2",
            "OPENMYCELIUM_RANK_BASE": "4",
            "OPENMYCELIUM_WORKER_INDEX": "1",
            "OPENMYCELIUM_WORLD_SIZE": "8",
            "OPENMYCELIUM_RENDEZVOUS_SERVICE": "training-workers",
        }
        config = RuntimeConfig.from_environment(environment)
        self.assertEqual(config.rank, 5)
        self.assertEqual(config.rendezvous_host, "training-workers")
        self.assertEqual(config.algorithm, "Mycelium")

    def test_invalid_world_rank_is_rejected(self):
        environment = {
            "OPENMYCELIUM_EXECUTION_PLAN_ID": "plan-1",
            "OPENMYCELIUM_EXECUTION_PLAN_JSON": '{"transport":"gloo"}',
            "OPENMYCELIUM_EXECUTION_TRANSPORT": "gloo",
            "OPENMYCELIUM_RANK_BASE": "3",
            "OPENMYCELIUM_WORKER_INDEX": "1",
            "OPENMYCELIUM_WORLD_SIZE": "4",
        }
        with self.assertRaisesRegex(ValueError, "outside"):
            RuntimeConfig.from_environment(environment)


if __name__ == "__main__":
    unittest.main()
