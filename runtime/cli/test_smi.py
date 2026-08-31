"""`openmycelium smi` renders facts, and refuses to render a number it lacks.

The formatting is the least of it. What these tests hold is the set of claims a
status screen is most tempted to make: that a missing reading is a zero, that
two cards' memory is one pool, and that a whole-device total describes this
runtime. Each of those has already been got wrong somewhere in this project, and
a dashboard is where a wrong number does the most damage, because it is the
thing people read instead of the evidence.

Nothing here touches a device.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
for _relative in ("../fabric",):
    _path = os.path.normpath(os.path.join(_HERE, _relative))
    if _path not in sys.path:
        sys.path.insert(0, _path)

import smi  # noqa: E402

GIB = 1024 ** 3

NVIDIA = {
    "vendor": "nvidia", "name": "NVIDIA GeForce RTX 5060 Ti", "runtime": "cuda",
    "identity": "nvidia:GPU-cbb3d045-9d5f-a225-0f2e-adb1c6d6a033",
    "identity_source": "uuid", "identityConfidence": "strong",
    "total_bytes": 17 * GIB, "free_bytes": 15 * GIB,
    "power_watts": 17.6, "power_limit_watts": 180.0, "power_source": "nvidia-smi",
    "torch_version": "2.11.0+cu128",
}
#: As the AMD card genuinely reports under WSL: no source, no value.
AMD = {
    "vendor": "amd", "name": "AMD Radeon RX 9060 XT", "runtime": "rocm",
    "identity": "amd:pci-0000:04:00.0",
    "identity_source": "pci", "identityConfidence": "slot-stable",
    "total_bytes": 16 * GIB, "free_bytes": 15 * GIB,
    "power_watts": None, "power_source": "unavailable",
    "torch_version": "2.10.0+rocm7.0",
}
VERSIONS = {"openmycelium": "0.3.0a13", "mccl": "0.2.0a3"}


def screen(devices=(NVIDIA, AMD), runs=(), records=(), safety="off"):
    return smi.render({"devices": list(devices), "identitiesUnique": True},
                      VERSIONS, list(runs), list(records), safety,
                      now=1788000000.0)


def row_for(text, vendor):
    """The table row for one vendor, so an assertion cannot match elsewhere."""
    for line in text.splitlines():
        if f"  {vendor:<8}" in line or line.strip().startswith(f"0   {vendor}") \
                or f" {vendor} " in line[:20]:
            return line
    raise AssertionError(f"no row for {vendor} in:\n{text}")


class MissingReadingTests(unittest.TestCase):
    def test_amd_power_is_not_rendered_as_zero(self):
        # Asserted on the AMD row alone. A naive search of the whole screen
        # matches "0W" inside the NVIDIA row's "180W" limit, which would make
        # this test pass or fail for reasons that have nothing to do with the
        # claim.
        import re
        row = row_for(screen(), "amd")
        self.assertIn("n/a", row)
        self.assertIsNone(re.search(r"\d+(\.\d+)?\s*W", row),
                          f"a wattage was rendered for a card with no source: {row}")

    def test_amd_power_renders_as_unavailable_with_a_reason(self):
        text = screen()
        self.assertIn("n/a", text)
        self.assertIn("rocm-smi", text)
        self.assertIn("/dev/dxg", text)

    def test_a_source_that_failed_is_distinguished_from_one_that_never_existed(self):
        # Expected unavailability is a platform gap; a named source returning
        # nothing is a fault, and the screen must not read the same for both.
        broken = dict(AMD, power_source="rocm-smi")
        text = screen(devices=(NVIDIA, broken))
        self.assertIn("returned nothing", text)

    def test_a_present_reading_is_shown(self):
        self.assertIn("18W/180W", screen())

    def test_no_footnote_appears_when_every_reading_is_present(self):
        text = screen(devices=(NVIDIA,))
        self.assertNotIn("rocm-smi", text)
        self.assertNotIn("returned nothing", text)


class AggregateTests(unittest.TestCase):
    def test_the_aggregate_is_never_called_a_pool(self):
        text = screen().lower()
        self.assertIn("not pooled", text)
        self.assertNotIn("pooled vram", text.replace("not pooled", ""))

    def test_the_screen_says_one_model_spans_both_cards(self):
        # The earlier wording said "no single allocation can use the aggregate",
        # which collapsed two different claims and denied the product's whole
        # capability. A 22.84 GiB model runs across these two cards today; what
        # cannot happen is one tensor spanning them.
        text = screen()
        self.assertIn("One model spans both", text)
        self.assertIn("too large for either card", text)

    def test_the_limit_that_remains_is_stated_exactly(self):
        text = screen()
        self.assertIn("no allocation crosses the boundary", text)
        self.assertIn("a single tensor still has to fit 17.00 GiB", text)

    def test_the_largest_single_memory_is_shown_beside_the_aggregate(self):
        # The number that matters for "will one tensor fit" is the largest
        # single memory; the aggregate is what a partitioned model can use.
        text = screen()
        self.assertIn("33.00 GiB across 2 separate memories", text)
        self.assertIn("largest single 17.00 GiB", text)

    def test_the_json_form_says_the_aggregate_is_not_pooled(self):
        # A machine consumer cannot read a footnote.
        import json
        self.assertIn('"aggregateIsPooled": false',
                      json.dumps({"aggregateIsPooled": False}, indent=2))


class AttributionTests(unittest.TestCase):
    def test_device_memory_is_labelled_as_the_whole_card(self):
        # Under WSL the total includes the Windows host's own usage and cannot
        # be attributed to this runtime.
        self.assertIn("Memory (whole card)", screen())

    def test_transport_is_not_described_as_gpu_direct(self):
        self.assertIn("not GPU-direct", screen())

    def test_shadow_mode_says_it_enforces_nothing(self):
        self.assertIn("enforces nothing", screen(safety="shadow"))

    def test_identity_and_its_confidence_are_both_shown(self):
        text = screen()
        self.assertIn("identity from uuid (strong)", text)
        self.assertIn("identity from pci (slot-stable)", text)


class EmptyStateTests(unittest.TestCase):
    def test_no_devices_is_stated_rather_than_shown_blank(self):
        self.assertIn("no accelerators discovered", screen(devices=()))

    def test_no_qualification_says_a_run_would_be_refused(self):
        self.assertIn("a run will be refused", screen())

    def test_no_runs_says_what_a_run_would_occupy(self):
        self.assertIn("occupies both cards", screen())


class LayoutTests(unittest.TestCase):
    def test_a_long_name_is_elided_not_silently_clipped(self):
        long = dict(NVIDIA, name="NVIDIA GeForce RTX 9999 Ti Super Extended")
        self.assertIn("…", screen(devices=(long,)))

    def test_the_full_name_fits_when_it_can(self):
        self.assertIn("NVIDIA GeForce RTX 5060 Ti", screen())

    def test_no_row_exceeds_the_rule(self):
        # A table wider than its own rule is unreadable in a terminal.
        for line in screen().splitlines():
            self.assertLessEqual(len(line), smi.WIDTH + 4, line)


if __name__ == "__main__":
    unittest.main()
