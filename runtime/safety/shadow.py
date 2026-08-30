"""Shadow mode: the Governor watches a real run and changes nothing.

Gate D.2. The point is to find out what the Governor *would* have done on real
workloads before it is allowed to do anything, and the only way that evidence is
worth having is if shadow mode provably cannot act.

So the observer holds no reference to anything that could act. It cannot refuse
admission, terminate a process, release a lease or write a production quarantine
record, because it is not given the ability to: the Governor it drives is
constructed with a no-op actuator and an in-memory quarantine store that is
discarded, and the observer exposes no method that returns a decision to its
caller. `observe()` returns None.

`off` is the default and is exactly the previous code path. The integration is
one conditional that reaches nothing when the flag is unset.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from contract import (CONTRACT_VERSION, Confidence, Policy,  # noqa: E402
                      State, permitted)
from governor import FakeProcessActuator, SafetyGovernor  # noqa: E402

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODES = (MODE_OFF, MODE_SHADOW)

#: The observation record's own schema. Separate from the contract version and
#: the policy version, because the three change for different reasons: the shape
#: of the record, the meaning of a transition, and the value of a threshold.
#: A reader that cannot tell which changed cannot safely parse an old file.
SHADOW_SCHEMA_VERSION = 1

#: Every observation carries these. An observation that cannot be tied to a run,
#: a placement, a boot and a moment is an anecdote: it cannot be correlated with
#: the audit trail, compared against what actually happened, or ordered against
#: its neighbours -- which is the entire use of shadow evidence.
REQUIRED_FIELDS = (
    "schemaVersion", "runId", "placementId", "manifestDigest", "bootId",
    "wallTimeUtc", "monotonicNs", "safetyMode", "safetyContractVersion",
    "safetyPolicyVersion", "safetySignals", "wouldTransition", "wouldAction",
)

#: Development flag. Deliberately not a CLI option, a config file key or a
#: console control: shadow mode is for gathering evidence, and a surface that
#: invites operators to turn it on invites them to expect it to do something.
MODE_ENV = "OM_SAFETY_MODE"

#: Minimum seconds between telemetry reads. The observer runs beside a workload
#: that is already saturating two GPUs; an unbounded poll loop would be a
#: resource-pressure source of exactly the kind the Governor exists to detect,
#: and it would be detecting itself.
MIN_POLL_INTERVAL_SECONDS = 5.0


def safety_mode(environ: Optional[Dict[str, str]] = None) -> str:
    """`off` unless explicitly asked otherwise. An unknown value is `off`.

    Failing to `off` on a bad value is deliberate. The alternative -- raising --
    would let a typo in an environment variable stop a production run, which is
    a worse outcome than not collecting observations.
    """
    environ = os.environ if environ is None else environ
    value = (environ.get(MODE_ENV) or "").strip().lower()
    return value if value in MODES else MODE_OFF


class SystemClock:
    """Wall clock for shadow mode. Monotonic, because deadlines use it."""

    def now(self) -> float:
        import time  # noqa: PLC0415
        return time.monotonic()


class FabricTelemetry:
    """Adapts a Fabric snapshot to the telemetry interface.

    Consumes the snapshot the coordinator has *already* taken rather than
    probing devices itself. That is the strongest form of the polling bound: the
    observer adds no device queries at all, so it cannot contend with the
    workload it is watching.

    Confidence is preserved as the Fabric layer reported it. AMD under WSL has
    no power or thermal source -- `power_source: "unavailable"`, values `None` --
    and that must arrive here as UNAVAILABLE_EXPECTED rather than zero.
    """

    def __init__(self, report: Dict[str, Any], taken_at: float):
        self._devices: Dict[str, Dict[str, Any]] = {}
        self.sampled_at: Dict[str, float] = {}
        for device in (report.get("devices") or []):
            identity = device.get("identity")
            if not identity:
                continue
            self._devices[identity] = self._reading(device)
            self.sampled_at[identity] = taken_at

    @staticmethod
    def _reading(device: Dict[str, Any]) -> Dict[str, Any]:
        free = device.get("free_bytes", device.get("freeBytes"))
        total = device.get("total_bytes", device.get("totalBytes"))
        power = device.get("power_watts")
        source = device.get("power_source")
        return {
            "totalBytes": total,
            "freeBytes": free,
            "vramConfidence": (Confidence.AVAILABLE if free is not None
                               else Confidence.UNAVAILABLE_UNEXPECTED),
            "powerWatts": power,
            # An absent source with a recorded reason is a capability gap; a
            # source that named itself and returned nothing is a fault.
            "powerConfidence": (
                Confidence.AVAILABLE if power is not None
                else Confidence.UNAVAILABLE_EXPECTED
                if source in (None, "", "unavailable")
                else Confidence.UNAVAILABLE_UNEXPECTED),
            "temperatureC": None,
            "temperatureConfidence": Confidence.UNAVAILABLE_EXPECTED,
            "runtime": device.get("runtime"),
        }

    def devices(self) -> Sequence[str]:
        return tuple(self._devices)

    def sample(self, device: str, now: float) -> Dict[str, Any]:
        return dict(self._devices[device])

    def canary(self, device: str) -> bool:
        # Shadow mode never runs a canary: it would be real work on a real
        # device, which is an action.
        return True


class ShadowObserver:
    """Watches, records what the Governor would do, and does none of it."""

    def __init__(self, mode: str = MODE_OFF, policy: Optional[Policy] = None,
                 boot_id: str = "unknown", clock: Optional[Any] = None,
                 emit: Optional[Any] = None, run_id: str = ""):
        self.mode = mode if mode in MODES else MODE_OFF
        self.run_id = run_id
        self.policy = policy or Policy()
        self.boot_id = boot_id
        self.clock = clock or SystemClock()
        self._emit = emit
        self.observations: List[Dict[str, Any]] = []
        self._last_poll: Optional[float] = None
        self._governor: Optional[SafetyGovernor] = None

    @property
    def active(self) -> bool:
        return self.mode == MODE_SHADOW

    def _due(self) -> bool:
        now = self.clock.now()
        if self._last_poll is not None and \
                now - self._last_poll < MIN_POLL_INTERVAL_SECONDS:
            return False
        self._last_poll = now
        return True

    def observe(self, phase: str, fabric_report: Dict[str, Any],
                manifest: Optional[Dict[str, Any]] = None,
                requested_bytes: int = 0, force: bool = False) -> None:
        """Record what the Governor would do. Returns nothing, by design.

        There is no return value because a return value is how an observer
        becomes a decision-maker: the first caller to branch on it turns shadow
        mode into enforcement without anyone deciding to.
        """
        if not self.active:
            return
        if not force and not self._due():
            return

        telemetry = FabricTelemetry(fabric_report, self.clock.now())
        # A fresh Governor per observation, with a no-op actuator and a
        # throwaway in-memory quarantine store. Nothing it does outlives this
        # call, so it cannot write a production record even by mistake.
        governor = SafetyGovernor(
            clock=self.clock, telemetry=telemetry, policy=self.policy,
            boot_id=self.boot_id, quarantine_store={},
            actuator=FakeProcessActuator(), simulated=True,
            run_id=self.run_id or "shadow",
            placement_id=(manifest or {}).get("placementId", "shadow"),
            manifest_digest=(manifest or {}).get("manifestDigest", ""),
            model_fingerprint=((manifest or {}).get("model") or {})
                              .get("fingerprint", ""))
        self._governor = governor

        would_transition, would_action, detail = self._evaluate(
            governor, telemetry, phase, requested_bytes)

        signals = {}
        for device in telemetry.devices():
            reading = telemetry.sample(device, self.clock.now())
            signals[device] = {
                "freeBytes": reading.get("freeBytes"),
                "totalBytes": reading.get("totalBytes"),
                "vramConfidence": str(reading.get("vramConfidence")),
                "powerWatts": reading.get("powerWatts"),
                "powerConfidence": str(reading.get("powerConfidence")),
                "temperatureC": reading.get("temperatureC"),
                "temperatureConfidence": str(reading.get("temperatureConfidence")),
            }

        import time  # noqa: PLC0415

        manifest = manifest or {}
        record = {
            "event": "safety_shadow_observation",
            "schemaVersion": SHADOW_SCHEMA_VERSION,
            # Correlation. Without these the observation cannot be matched to
            # the run it describes, and shadow evidence exists to be compared
            # against what actually happened.
            "runId": self.run_id,
            "placementId": manifest.get("placementId", ""),
            "manifestDigest": manifest.get("manifestDigest", ""),
            "modelFingerprint": (manifest.get("model") or {}).get(
                "fingerprint", ""),
            "bootId": self.boot_id,
            # Both clocks, for the reason audit.py already documents: wall time
            # orders events for a human and can step backwards; monotonic is
            # only comparable within one boot, which `bootId` identifies.
            "wallTimeUtc": round(time.time(), 3),
            "monotonicNs": time.clock_gettime_ns(time.CLOCK_MONOTONIC),
            "safetyMode": MODE_SHADOW,
            "safetyContractVersion": CONTRACT_VERSION,
            "safetyPolicyVersion": self.policy.version,
            "safetyPhase": phase,
            "safetySignals": signals,
            "wouldTransition": would_transition,
            "wouldAction": would_action,
            "wouldDetail": detail,
            "safetySimulated": True,
            "safetyEnforced": False,
        }
        missing = [name for name in REQUIRED_FIELDS if name not in record]
        if missing:
            raise ValueError(
                f"shadow observation is missing {missing}; an observation that "
                "cannot be correlated is an anecdote")
        self.observations.append(record)
        if self._emit is not None:
            self._emit(record)

    def _evaluate(self, governor, telemetry, phase: str,
                  requested_bytes: int):
        """What would have happened. Computed, never applied."""
        if phase == "preflight":
            for device in telemetry.devices():
                reading = telemetry.sample(device, self.clock.now())
                if reading.get("freeBytes") is None:
                    return ("UNKNOWN->FAILED", "refuse_preflight",
                            f"{device} has no VRAM reading")
            return ("UNKNOWN->READY", "none", "preflight would pass")

        if phase == "admission":
            for device in telemetry.devices():
                reading = telemetry.sample(device, self.clock.now())
                free = reading.get("freeBytes")
                total = reading.get("totalBytes") or 0
                if free is None:
                    return ("READY->READY", "refuse_admission",
                            f"{device} VRAM unavailable")
                reserve = self.policy.reserve_bytes(int(total))
                if int(free) - requested_bytes < reserve:
                    return ("READY->READY", "refuse_admission",
                            f"{device}: {free} free, {requested_bytes} "
                            f"requested, {reserve} reserved")
            return ("READY->ADMITTED", "none", "admission would be granted")

        # Running: the limits.
        for device in telemetry.devices():
            reading = telemetry.sample(device, self.clock.now())
            free, total = reading.get("freeBytes"), reading.get("totalBytes")
            if free is None or not total:
                return ("RUNNING->RUNNING", "degrade",
                        f"{device} VRAM unavailable; would stop admitting")
            used = (int(total) - int(free)) / int(total)
            if used >= self.policy.hard_limit_fraction:
                return ("RUNNING->DRAINING", "drain_immediate",
                        f"{device} at {used:.1%}, hard limit "
                        f"{self.policy.hard_limit_fraction:.0%}")
            if used >= self.policy.soft_limit_fraction:
                return ("RUNNING->DRAINING", "drain_graceful_if_sustained",
                        f"{device} at {used:.1%}, soft limit "
                        f"{self.policy.soft_limit_fraction:.0%}")
        return ("none", "none", "no limit approached")

    # ------------------------------------------------------------- summary
    def summary(self) -> Dict[str, Any]:
        actions = [o["wouldAction"] for o in self.observations]
        return {
            "safetyMode": self.mode,
            "observations": len(self.observations),
            "contractVersion": CONTRACT_VERSION,
            "policyVersion": self.policy.version,
            "wouldActions": sorted(set(actions)),
            "wouldHaveActed": sorted(set(a for a in actions if a != "none")),
            "enforced": False,
        }
