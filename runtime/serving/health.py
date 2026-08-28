"""Runtime health, driven by events rather than by the absence of output.

This exists because of a concrete failure: a run was reported as "still loading
weights" when in fact the WSL VM had shut down and taken both workers with it.
Nothing had crashed and nothing had logged an error -- the kernel was simply
gone -- so silence looked exactly like progress.

The rule that follows is that no state is ever inferred from a quiet log. Every
transition needs a worker event or a heartbeat, and a worker that stops sending
heartbeats is dead, not busy.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

STARTING = "STARTING"
LOADING_CUDA = "LOADING_CUDA"
LOADING_ROCM = "LOADING_ROCM"
QUALIFYING = "QUALIFYING"
READY = "READY"
DEGRADED = "DEGRADED"
RECOVERING = "RECOVERING"
FAILED = "FAILED"

#: Which states may follow which. A transition outside this map is a bug in the
#: supervisor, not a condition to paper over.
ALLOWED: Dict[str, tuple] = {
    STARTING: (LOADING_CUDA, LOADING_ROCM, FAILED),
    LOADING_CUDA: (LOADING_ROCM, QUALIFYING, DEGRADED, FAILED),
    LOADING_ROCM: (LOADING_CUDA, QUALIFYING, DEGRADED, FAILED),
    QUALIFYING: (READY, DEGRADED, FAILED),
    READY: (DEGRADED, RECOVERING, FAILED),
    DEGRADED: (RECOVERING, READY, FAILED),
    RECOVERING: (STARTING, LOADING_CUDA, LOADING_ROCM, QUALIFYING, READY, FAILED),
    FAILED: (RECOVERING,),
}

TERMINAL = (FAILED,)


class HealthError(RuntimeError):
    """An illegal transition, or a report of readiness that is not earned."""


@dataclass
class Worker:
    """One stage process, as the supervisor sees it from outside."""

    name: str
    stage: str = STARTING
    last_beat: float = field(default_factory=time.monotonic)
    resident_mib: float = 0.0
    device: str = ""
    detail: str = ""
    exit_code: Optional[int] = None

    def beat(self, now: Optional[float] = None) -> None:
        self.last_beat = time.monotonic() if now is None else now

    def silent_for(self, now: Optional[float] = None) -> float:
        return (time.monotonic() if now is None else now) - self.last_beat

    @property
    def on_gpu(self) -> bool:
        return self.device.startswith("cuda")

    @property
    def loaded(self) -> bool:
        """Weights are in place. On a GPU that means resident bytes, measured.

        The CPU smoke path reports no resident device memory because there is
        none; requiring it there would make the supervisor unable to reach READY
        in exactly the configuration used to test the supervisor.
        """
        if self.stage not in (QUALIFYING, READY):
            return False
        return self.resident_mib > 0 or not self.on_gpu


@dataclass
class Health:
    """The runtime's state, and the evidence for it."""

    state: str = STARTING
    workers: Dict[str, Worker] = field(default_factory=dict)
    heartbeat_timeout: float = 90.0
    history: List[Dict[str, Any]] = field(default_factory=list)
    listener: Optional[Callable[[Dict[str, Any]], None]] = None

    def register(self, name: str) -> Worker:
        worker = Worker(name)
        self.workers[name] = worker
        return worker

    def to(self, state: str, detail: str = "") -> None:
        if state == self.state:
            return
        if state not in ALLOWED.get(self.state, ()):
            raise HealthError(
                f"illegal transition {self.state} -> {state}"
                + (f" ({detail})" if detail else ""))
        # READY is a claim about both GPUs holding weights. It is checked here
        # rather than trusted, because "ready" is the one state a caller acts on.
        if state == READY and not self._both_loaded():
            raise HealthError(
                "refusing READY: "
                + ", ".join(f"{w.name} resident {w.resident_mib:.0f} MiB "
                            f"stage {w.stage}" for w in self.workers.values()))
        previous, self.state = self.state, state
        self._emit({"event": "state", "from": previous, "to": state,
                    "detail": detail})

    def _both_loaded(self) -> bool:
        return bool(self.workers) and all(
            worker.loaded for worker in self.workers.values())

    def observe(self, event: Dict[str, Any]) -> None:
        """Fold one worker event into the runtime's view of itself."""
        name = event.get("worker")
        worker = self.workers.get(name) if name else None
        if worker is None:
            self._emit(dict(event))
            return
        worker.beat()
        kind = event.get("event")
        if kind == "loading":
            worker.stage = LOADING_CUDA if worker.name == "cuda" else LOADING_ROCM
            worker.device = event.get("device", worker.device)
        elif kind == "loaded":
            worker.stage = QUALIFYING
            worker.resident_mib = float(event.get("residentMiB", 0.0))
            worker.device = event.get("device", worker.device)
        elif kind == "ready":
            worker.stage = READY
            worker.resident_mib = float(
                event.get("residentMiB", worker.resident_mib))
        elif kind == "failed":
            worker.stage = FAILED
            worker.detail = str(event.get("detail", ""))[:400]
        self._emit(dict(event))

    def sweep(self, now: Optional[float] = None) -> Optional[str]:
        """Fail workers that have gone quiet. Silence is death, not patience."""
        stale = [w for w in self.workers.values()
                 if w.stage not in TERMINAL
                 and w.silent_for(now) > self.heartbeat_timeout]
        if not stale:
            return None
        for worker in stale:
            worker.stage = FAILED
            worker.detail = (f"no heartbeat for {worker.silent_for(now):.0f}s "
                             f"(limit {self.heartbeat_timeout:.0f}s)")
            self._emit({"event": "failed", "worker": worker.name,
                        "detail": worker.detail})
        reason = "; ".join(f"{w.name}: {w.detail}" for w in stale)
        if self.state not in TERMINAL:
            self.to(DEGRADED if self.state == READY else FAILED, reason)
        return reason

    def worker_exited(self, name: str, code: int) -> None:
        worker = self.workers.get(name)
        if worker is None:
            return
        worker.exit_code = code
        if code != 0 and worker.stage != FAILED:
            worker.stage = FAILED
            worker.detail = f"exited with code {code}"
            self._emit({"event": "failed", "worker": name,
                        "detail": worker.detail})

    def summary(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "workers": {name: {"stage": w.stage, "device": w.device,
                               "residentMiB": round(w.resident_mib, 1),
                               "silentForS": round(w.silent_for(), 1),
                               "exitCode": w.exit_code, "detail": w.detail}
                        for name, w in self.workers.items()},
        }

    def _emit(self, record: Dict[str, Any]) -> None:
        record.setdefault("t", round(time.time(), 3))
        self.history.append(record)
        if self.listener is not None:
            self.listener(record)


def emit(stream, worker: str, event: str, **fields: Any) -> None:
    """Write one event line from inside a worker.

    JSON Lines on a dedicated stream, so the supervisor never has to guess what
    a worker is doing from prose it happened to print.
    """
    record = {"worker": worker, "event": event, "t": round(time.time(), 3)}
    record.update(fields)
    stream.write(json.dumps(record) + "\n")
    stream.flush()
