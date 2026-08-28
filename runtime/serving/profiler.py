"""Per-operation timing for the two stages, by stream event or by device sync.

Two methods, because one of them is the thing being validated and the other is
the thing it is validated against:

* ``stream-event`` -- CUDA/HIP events recorded on the operation's own stream.
  Events are recorded during the pass and read back *once* at the end, so
  timing does not serialise the queue it is measuring. This is the method
  Intelligence v0 will use.
* ``global-sync-control`` -- ``torch.cuda.synchronize()`` on both sides of each
  operation, timed with the host clock. This is what the runtime already did
  everywhere, so it is the baseline the event method must reconcile with. It
  distorts overlap by construction, which is why it is a control and not the
  measurement.

Neither method may change what the model produces. The profiler observes; it
never alters dtype, order, or control flow, and with ``method="off"`` it costs
one attribute lookup per call.

See docs/INTELLIGENCE_V0_CONTRACT.md §8.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

OFF = "off"
STREAM_EVENT = "stream-event"
GLOBAL_SYNC = "global-sync-control"
METHODS = (OFF, STREAM_EVENT, GLOBAL_SYNC)


class Profiler:
    """Collects labelled durations for one forward pass."""

    def __init__(self, torch: Any, device: str, method: str = OFF):
        if method not in METHODS:
            raise ValueError(f"timing method {method!r} is not one of {METHODS}")
        self.torch = torch
        self.device = device
        self.method = method
        self.on_gpu = device.startswith("cuda")
        if method == STREAM_EVENT and not self.on_gpu:
            # Stream events do not exist off-device. Falling back silently would
            # label host timings as event timings in the record.
            self.method = GLOBAL_SYNC
        self._pending: List[Tuple[str, Any, Any]] = []
        self._host: List[Tuple[str, float]] = []
        self.samples: Dict[str, List[float]] = {}

    @property
    def enabled(self) -> bool:
        return self.method != OFF

    # ----------------------------------------------------------------- timing
    @contextmanager
    def time(self, label: str):
        if self.method == OFF:
            yield
            return
        if self.method == STREAM_EVENT:
            stream = self.torch.cuda.current_stream()
            start = self.torch.cuda.Event(enable_timing=True)
            end = self.torch.cuda.Event(enable_timing=True)
            start.record(stream)
            try:
                yield
            finally:
                end.record(stream)
                # Read back later: synchronising here would serialise every
                # layer and measure a queue this profiler created.
                self._pending.append((label, start, end))
            return
        # global-sync control
        if self.on_gpu:
            self.torch.cuda.synchronize()
        began = time.perf_counter()
        try:
            yield
        finally:
            if self.on_gpu:
                self.torch.cuda.synchronize()
            self._host.append((label, (time.perf_counter() - began) * 1000.0))

    def collect(self) -> Dict[str, List[float]]:
        """Resolve everything recorded since the last collect."""
        if self._pending:
            # One synchronisation for the whole pass, on the last event only.
            self._pending[-1][2].synchronize()
            for label, start, end in self._pending:
                self.samples.setdefault(label, []).append(
                    start.elapsed_time(end))
            self._pending.clear()
        for label, value in self._host:
            self.samples.setdefault(label, []).append(value)
        self._host.clear()
        return self.samples

    def mark(self) -> Dict[str, float]:
        """Per-label totals right now, for measuring one step as a delta.

        Resetting instead would give correct per-step numbers and destroy the
        accumulated profile the run reports at the end. A snapshot gives both.
        """
        self.collect()
        return {label: sum(values) for label, values in self.samples.items()}

    def since(self, mark: Dict[str, float]) -> Dict[str, float]:
        """Per-label time spent since `mark`."""
        self.collect()
        out: Dict[str, float] = {}
        for label, values in self.samples.items():
            delta = sum(values) - mark.get(label, 0.0)
            if delta > 0:
                out[label] = delta
        return out

    def reset(self) -> None:
        self._pending.clear()
        self._host.clear()
        self.samples.clear()

    # ----------------------------------------------------------------- report
    def report(self) -> Dict[str, Any]:
        """Per-label statistics, plus the totals used for reconciliation."""
        self.collect()
        rows: Dict[str, Any] = {}
        for label, values in sorted(self.samples.items()):
            ordered = sorted(values)
            count = len(ordered)
            rows[label] = {
                "count": count,
                "meanMs": round(sum(ordered) / count, 4),
                "p50Ms": round(_percentile(ordered, 50), 4),
                "p95Ms": round(_percentile(ordered, 95), 4),
                "minMs": round(ordered[0], 4),
                "maxMs": round(ordered[-1], 4),
                # Within-label repetition is the only valid estimator of
                # measurement noise; spread between different labels may be
                # real signal and is never pooled into this.
                "spreadMs": round(ordered[-1] - ordered[0], 4),
            }
        return {"timingMethod": self.method, "device": self.device,
                "labels": rows,
                "summedMs": round(sum(sum(v) for v in self.samples.values()), 4)}

    def layer_totals(self, prefix: str) -> Dict[str, Any]:
        """Aggregate every label starting with `prefix` (e.g. 'layer.')."""
        picked = {k: v for k, v in self.samples.items() if k.startswith(prefix)}
        if not picked:
            return {}
        per_layer = {k: round(sum(v) / len(v), 4) for k, v in picked.items()}
        values = sorted(per_layer.values())
        return {
            "layers": len(per_layer),
            "meanMs": round(sum(values) / len(values), 4),
            "minMs": values[0],
            "maxMs": values[-1],
            # Between-layer spread. Reported next to, never merged with, the
            # within-layer spread above: layers share shapes but not weights,
            # so a difference here may be signal rather than noise.
            "betweenLayerSpreadMs": round(values[-1] - values[0], 4),
            "perLayerMeanMs": per_layer,
        }


def _percentile(ordered: List[float], pct: float) -> float:
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * pct / 100.0
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


#: A profiler that is always off, so call sites need no conditionals.
class NullProfiler(Profiler):
    def __init__(self) -> None:                              # noqa: D107
        self.torch = None
        self.device = ""
        self.method = OFF
        self.on_gpu = False
        self._pending = []
        self._host = []
        self.samples = {}
