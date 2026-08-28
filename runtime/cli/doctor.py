"""`openmycelium doctor` -- check every precondition before a long load.

Loading 22.8 GiB takes minutes. Discovering afterwards that the ROCm runtime
cannot see its GPU wastes all of it, so each requirement is checked up front and
reported as pass or fail with the reason.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from typing import Any, Dict, List, Tuple

GIB = 1 << 30
def _checks() -> List[Tuple[str, str]]:
    """Which interpreters to check, from the configuration precedence."""
    import config as _config  # noqa: PLC0415
    resolved = _config.load()
    return [("cuda", resolved.cuda_python), ("rocm", resolved.rocm_python)]


def gpu_probe(python: str) -> Dict[str, Any]:
    script = (
        "import json,torch;"
        "ok=torch.cuda.is_available();"
        "free,total=(torch.cuda.mem_get_info(0) if ok else (0,0));"
        "print(json.dumps({'ok':ok,'name':torch.cuda.get_device_name(0) if ok else '',"
        "'free':free,'total':total,'torch':torch.__version__,"
        "'runtime':'rocm' if getattr(torch.version,'hip',None) else 'cuda'}))")
    try:
        out = subprocess.run([python, "-c", script], capture_output=True,
                             text=True, timeout=300)
        lines = [l for l in out.stdout.splitlines() if l.startswith("{")]
        if not lines:
            return {"ok": False, "detail": (out.stderr or "no output").strip()[-200:]}
        return json.loads(lines[-1])
    except Exception as error:                                # noqa: BLE001
        return {"ok": False, "detail": str(error)[:200]}


def main() -> int:
    failures = 0
    print()
    print("  openmycelium doctor")
    print()

    for label, python in _checks():
        if not os.path.exists(python):
            print(f"  [FAIL] {label:<6} interpreter missing: {python}")
            failures += 1
            continue
        info = gpu_probe(python)
        if not info.get("ok"):
            print(f"  [FAIL] {label:<6} no GPU: {info.get('detail', 'unavailable')}")
            failures += 1
            continue
        expected = label
        actual = info.get("runtime")
        mark = "PASS" if actual == expected else "FAIL"
        if mark == "FAIL":
            failures += 1
        print(f"  [{mark}] {label:<6} {info['name']:<28} "
              f"{info['total'] / GIB:5.2f} GiB, {info['free'] / GIB:5.2f} free  "
              f"(torch {info['torch']}, {actual} build)")

    # If the ROCm side is not working, say which of the three layers is the
    # reason. "no GPU" above is true but useless: the packages can be perfect
    # and the card still unreachable because the HSA runtime is the wrong one.
    try:
        import config as _config                            # noqa: PLC0415
        import rocm_prereq                                  # noqa: PLC0415
        resolved = _config.load()
        report = rocm_prereq.detect(getattr(resolved, "rocm_python", ""),
                                    state_dir=getattr(resolved, "state_dir", ""))
        if report["state"] != "qualified":
            print()
            print("  ROCm prerequisites")
            for line in rocm_prereq.summary(report):
                print(line)
            for line in rocm_prereq.remediation(report):
                print(line)
            print()
    except Exception:                                       # noqa: BLE001
        pass

    dxg = "/dev/dxg"
    print(f"  [{'PASS' if os.path.exists(dxg) else 'FAIL'}] wsl    {dxg} "
          f"{'present' if os.path.exists(dxg) else 'missing -- no GPU access'}")
    if not os.path.exists(dxg):
        failures += 1

    # From the resolver, not a constant. The a6 run reported "/opt/models does
    # not exist yet" on a machine whose store was /var/lib/openmycelium/models,
    # which is a portability leak wearing a warning's clothing.
    try:
        import config as _cfg                                # noqa: PLC0415
        _resolved = _cfg.load()
        store = _resolved.model_store
        ledger_default = _resolved.ledger
    except Exception:                                        # noqa: BLE001
        store = "/var/lib/openmycelium/models"
        ledger_default = "/var/lib/openmycelium/xvendor_qualification.json"
    if os.path.isdir(store):
        free = shutil.disk_usage(store).free
        models = [n for n in sorted(os.listdir(store))
                  if os.path.isfile(os.path.join(store, n, "config.json"))]
        print(f"  [PASS] models {len(models)} imported, "
              f"{free / GIB:.1f} GiB free on the Linux filesystem")
        for name in models:
            print(f"         - {name}")
    else:
        print(f"  [WARN] models {store} does not exist yet")

    ledger = os.environ.get("OM_XVENDOR_LEDGER", ledger_default)
    print(f"  [{'PASS' if os.path.exists(ledger) else 'WARN'}] ledger "
          f"{ledger} {'present' if os.path.exists(ledger) else 'absent'}")

    print()
    print(f"  {'ready' if failures == 0 else str(failures) + ' check(s) failed'}")
    print()
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
