"""The control record a running coordinator publishes, so it can be found.

`stop` must not kill a PID it merely hopes is the right one. PIDs are reused,
and killing whatever now holds a recycled number is how a supervisor destroys an
unrelated process. So a running coordinator writes a record naming itself, and
`stop` acts only when the record still matches the process it describes.

Three things together identify a process, and all three are checked:

* the PID
* the PID's **start time** from /proc, which a reused PID will not reproduce
* the `runId`, so a record left by an earlier attempt cannot authorise a stop

A record whose process is gone is stale and is reported as such, never treated
as a live runtime.
"""

from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load as _load_config  # noqa: E402

#: Where running runtimes publish their control records.
CONTROL_DIR = os.path.join(_load_config().state_dir, "run")
RECORD_SCHEMA = 1


def boot_of() -> str:
    """The kernel boot this record belongs to.

    A record written before a WSL VM restart describes processes that no longer
    exist, and PIDs from the previous boot may now belong to something else.
    """
    try:
        with open("/proc/sys/kernel/random/boot_id", "r", encoding="utf-8") as h:
            return h.read().strip()
    except OSError:
        return "unknown"


def process_stat(pid: int) -> Optional[Tuple[str, int]]:
    """The process state character and start time from /proc/<pid>/stat.

    Start time is what makes a PID check meaningful: a recycled PID has a
    different one, so the pair identifies a process rather than a number.

    The state matters just as much. `/proc/<pid>/stat` still exists for a
    **zombie** -- a process that has exited but has not yet been reaped by its
    parent -- so reading only the start time reports an exited process as
    alive. Measured: `stop` escalated to SIGKILL and then reported
    "unresponsive: still running after SIGKILL" about a process that had
    already exited cleanly.
    """
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as handle:
            data = handle.read()
    except OSError:
        return None
    # The command field may contain spaces and is parenthesised; everything
    # after the final ')' is positional, starting with the state character.
    tail = data[data.rfind(")") + 2:].split()
    try:
        return tail[0], int(tail[19])
    except (IndexError, ValueError):
        return None


def process_start_ticks(pid: int) -> Optional[int]:
    """Start time only, for records that store it."""
    stat = process_stat(pid)
    return stat[1] if stat else None


#: Zombie and dead. Present in /proc, but not running.
FINISHED_STATES = ("Z", "X", "x")


def alive(pid: int, start_ticks: Optional[int]) -> bool:
    """Whether this exact process is still running -- not merely present."""
    if pid <= 0:
        return False
    stat = process_stat(pid)
    if stat is None:
        return False
    state, current = stat
    if state in FINISHED_STATES:
        # Exited, awaiting its parent's wait(). Not running.
        return False
    if start_ticks is not None and current != start_ticks:
        # The PID exists but belongs to something else now.
        return False
    return True


@dataclass
class ControlRecord:
    run_id: str
    pid: int
    start_ticks: Optional[int]
    boot_id: str
    model: str
    model_name: str
    placement_id: str
    manifest_digest: str
    workers: Dict[str, Any] = field(default_factory=dict)
    state: str = "STARTING"
    port: int = 0
    work_dir: str = ""
    command: str = ""
    started_at: float = field(default_factory=time.time)
    schema: int = RECORD_SCHEMA

    def __post_init__(self) -> None:
        # Derived here, not passed in: a caller that supplied the wrong pid or
        # a stale start time would make `stop` unable to verify the process.
        if not self.pid:
            self.pid = os.getpid()
        if self.start_ticks is None:
            self.start_ticks = process_start_ticks(self.pid)
        if not self.boot_id:
            self.boot_id = boot_of()

    def path(self, directory: str = CONTROL_DIR) -> str:
        return os.path.join(directory, f"{self.run_id}.json")

    def write(self, directory: str = CONTROL_DIR) -> str:
        os.makedirs(directory, exist_ok=True)
        target = self.path(directory)
        temporary = target + ".partial"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(asdict(self), handle, indent=2, sort_keys=True)
        # Atomic rename, so a reader never sees a half-written record.
        os.replace(temporary, target)
        return target

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def list_records(directory: str = CONTROL_DIR) -> List[Dict[str, Any]]:
    """Every published record, each annotated with whether it is still live."""
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        record = load(os.path.join(directory, name))
        if record is None:
            continue
        record["_path"] = os.path.join(directory, name)
        record["_alive"] = alive(int(record.get("pid", 0)),
                                 record.get("start_ticks"))
        record["_ageSeconds"] = round(time.time() - record.get("started_at", 0), 1)
        out.append(record)
    return out


def update_state(path: str, state: str, **fields: Any) -> None:
    record = load(path)
    if record is None:
        return
    record["state"] = state
    record.update(fields)
    temporary = path + ".partial"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
        os.replace(temporary, path)
    except OSError:
        pass


def remove_if_matches(path: str, run_id: str) -> bool:
    """Delete a record only when it still describes the run we stopped.

    A record rewritten by a newer attempt between our read and our delete would
    otherwise be removed by mistake, leaving a live runtime unfindable.
    """
    record = load(path)
    if record is None:
        return False
    if record.get("run_id") != run_id:
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def request_stop(record: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
    """Ask one runtime to shut down, then confirm it actually did."""
    pid = int(record.get("pid", 0))
    ticks = record.get("start_ticks")
    if not alive(pid, ticks):
        return {"outcome": "stale", "detail": "the process is already gone"}

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"outcome": "stale", "detail": "process vanished before signal"}
    except PermissionError as error:
        return {"outcome": "refused", "detail": str(error)}

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive(pid, ticks):
            return {"outcome": "stopped",
                    "seconds": round(timeout - (deadline - time.monotonic()), 1)}
        time.sleep(0.2)

    # Escalate only after a real grace period, and say that it was needed.
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not alive(pid, ticks):
            return {"outcome": "killed",
                    "detail": f"did not exit within {timeout:.0f}s of SIGTERM"}
        time.sleep(0.2)
    return {"outcome": "unresponsive",
            "detail": "still running after SIGKILL"}
