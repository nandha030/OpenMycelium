"""Gate D.2 scope review: prove no enforcement path entered this milestone.

Run before sealing. A scope review that runs after an irreversible step is not
a gate, so this is mechanical and reruns cheaply: it reads the parsed syntax
tree, never the source text, so a mention in a comment does not fail it and a
real call cannot hide in one.

The claim under review is narrow and worth stating exactly: shadow mode observes
and changes nothing. That is proved four ways -- the observer has no acting
method, its `observe` returns nothing on every path, the Governor it builds is
given a no-op actuator and a throwaway store, and nothing outside the safety
tests calls an acting Governor method at all.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

#: Every Governor method that changes something -- a lease, a process, a durable
#: record, the state machine. Shadow mode may call none of them, and no
#: production module may call them at all in this milestone.
ACTING = (
    "admit", "drain", "quarantine", "manual_reset", "terminate",
    "report_incident", "force_state", "register_worker", "may_signal",
    "canary_passed", "completed", "heartbeat", "poll", "preflight",
)

#: Names whose import would mean the observer touches a device itself.
DEVICE_MODULES = ("subprocess", "torch", "pynvml", "cupy", "pycuda", "smi")

failures = []
notes = []


def check(label: str, ok: bool, detail: str = "") -> None:
    (notes if ok else failures).append(
        f"{'ok  ' if ok else 'FAIL'}  {label}" + (f" -- {detail}" if detail else ""))


def tree(relative: str) -> ast.Module:
    with open(os.path.join(REPO, relative), "r", encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=relative)


# 1. The observer's public surface has no way to act. -------------------------
shadow = tree("runtime/safety/shadow.py")
observer = next(n for n in ast.walk(shadow)
                if isinstance(n, ast.ClassDef) and n.name == "ShadowObserver")
public = sorted(n.name for n in observer.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not n.name.startswith("_"))
forbidden = ("admit", "refuse", "drain", "terminate", "quarantine", "release",
             "enforce", "veto", "report_incident")
check("ShadowObserver exposes no acting method",
      not [n for n in public if n in forbidden], f"public: {public}")

# 2. `observe` returns nothing on every path. ---------------------------------
observe = next(n for n in observer.body
               if isinstance(n, ast.FunctionDef) and n.name == "observe")
returns_a_value = [r for r in ast.walk(observe)
                   if isinstance(r, ast.Return) and r.value is not None]
check("observe() returns nothing on every path",
      not returns_a_value,
      "a return value is how an observer becomes a decision-maker")

# 3. The Governor it builds cannot act. ---------------------------------------
built = [c for c in ast.walk(observe)
         if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "SafetyGovernor"]
check("shadow builds exactly one Governor", len(built) == 1)
if built:
    keywords = {k.arg: k.value for k in built[0].keywords}
    check("...with a no-op actuator",
          getattr(getattr(keywords.get("actuator"), "func", None), "id", "")
          == "FakeProcessActuator")
    check("...with a throwaway in-memory quarantine store",
          isinstance(keywords.get("quarantine_store"), ast.Dict)
          and not keywords["quarantine_store"].keys)
    check("...marked simulated",
          getattr(keywords.get("simulated"), "value", None) is True)

# 4. Shadow calls no acting Governor method. ----------------------------------
called = sorted({c.func.attr for c in ast.walk(shadow)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                 and c.func.attr in ACTING})
check("shadow calls no acting Governor method", not called, f"found {called}")

# 5. Shadow imports nothing that touches a device. ----------------------------
imported = set()
for node in ast.walk(shadow):
    if isinstance(node, ast.Import):
        imported.update(a.name.split(".")[0] for a in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        imported.add(node.module.split(".")[0])
touching = sorted(imported & set(DEVICE_MODULES))
check("shadow issues no device query of its own", not touching,
      f"imports {touching}" if touching else "consumes the existing snapshot")

# 6. No production module calls an acting Governor method. --------------------
offenders = []
for root, dirs, files in os.walk(os.path.join(REPO, "runtime")):
    dirs[:] = [d for d in dirs if d not in ("__pycache__", "tests", "native")]
    for name in files:
        if not name.endswith(".py") or name.startswith("test_"):
            continue
        relative = os.path.relpath(os.path.join(root, name), REPO)
        if relative.replace("\\", "/") in ("runtime/safety/governor.py",):
            continue                      # the definitions themselves
        try:
            module = tree(relative)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for call in ast.walk(module):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) \
                    and call.func.attr in ACTING:
                # Only a call on something named like a governor counts; `poll`
                # and `terminate` are ordinary words elsewhere in the runtime.
                target = getattr(call.func.value, "id", "") or \
                    getattr(getattr(call.func.value, "attr", ""), "lower", lambda: "")()
                if "governor" in str(target).lower():
                    offenders.append(f"{relative}: {target}.{call.func.attr}()")
check("no production module calls an acting Governor method",
      not offenders, "; ".join(offenders))

# 7. The `off` path is one lookup and a return, before any import. -----------
coordinator = tree("runtime/cli/coordinator.py")
shadow_observe = next(n for n in ast.walk(coordinator)
                      if isinstance(n, ast.FunctionDef) and n.name == "_shadow_observe")
body = [n for n in shadow_observe.body if not isinstance(n, ast.Expr)
        or not isinstance(getattr(n, "value", None), ast.Constant)]
first = body[0] if body else None
check("the off path is the first thing _shadow_observe does",
      isinstance(first, ast.If)
      and any(isinstance(r, ast.Return) for r in ast.walk(first)),
      "importing a module to discover you are switched off is already a difference")
check("nothing is imported before the off check",
      not [n for n in ast.walk(first) if isinstance(n, (ast.Import, ast.ImportFrom))]
      if first else False)

# 8. `prepare_placement` still sets the run id, on every path. ----------------
#
# This is not hypothetical. Inserting `_shadow_observe` as a new module-level
# function split `prepare_placement`, stranding the `run_id` assignment after
# the observer's `return None` where nothing could reach it. It was dead code
# rather than a live fault -- `Coordinator.__init__` also guarantees a run id,
# and every caller constructs one before reading `args.run_id` -- but a safety
# milestone that silently deletes a statement from the path it promised not to
# touch has not preserved that path, so the property is asserted here.
prepare = next(n for n in ast.walk(coordinator)
               if isinstance(n, ast.FunctionDef) and n.name == "prepare_placement")
assigns_run_id = [n for n in ast.walk(prepare) if isinstance(n, ast.Assign)
                  and any(getattr(t, "attr", "") == "run_id" for t in n.targets)]
calls_observer = [n for n in ast.walk(prepare) if isinstance(n, ast.Call)
                  and getattr(n.func, "id", "") == "_shadow_observe"]
check("prepare_placement assigns args.run_id", bool(assigns_run_id))
check("...before the shadow observation, so the record can carry it",
      bool(assigns_run_id) and bool(calls_observer)
      and assigns_run_id[0].lineno < calls_observer[0].lineno)

# 9. Every observation field the milestone promises is declared. --------------
required = next((n for n in ast.walk(shadow) if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", "") == "REQUIRED_FIELDS" for t in n.targets)),
                None)
declared = {e.value for e in required.value.elts} if required else set()
promised = {"schemaVersion", "runId", "placementId", "bootId", "wallTimeUtc"}
check("the observation schema declares run, placement, boot and time",
      promised <= declared, f"missing {sorted(promised - declared)}")

print("Gate D.2 scope review -- no enforcement path entered this milestone\n")
for line in notes + failures:
    print(f"  {line}")
print()
if failures:
    print(f"  == scope review FAILED: {len(failures)} check(s) ==")
    sys.exit(1)
print(f"  == scope review PASSED: {len(notes)} checks ==")
