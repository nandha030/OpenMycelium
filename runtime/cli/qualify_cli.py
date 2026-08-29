"""Inspect and write adapter qualification records.

Qualification is default-deny: an adapter with no record covering this exact
situation cannot execute. This command is how a record comes to exist, and it
refuses to write one that is not backed by a passing gate.

    openmycelium qualify status --model Mistral-Nemo-Instruct-2407
    openmycelium qualify list
    openmycelium qualify record --placement /path/placement.json \
                                --evidence /var/log/om-gate/summary.json

`record` is not a way to declare something qualified. It requires
`OM_QUALIFICATION_MODE=qualify`, an actor, and an evidence file describing the
run that passed -- because a record with no evidence behind it is exactly the
claim this design exists to prevent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
for _part in ("serving", "scheduler", "fabric"):
    _path = os.path.join(os.path.dirname(_HERE), _part)
    if _path not in sys.path:
        sys.path.insert(0, _path)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from adapters import AdapterError  # noqa: E402
from adapters.qualification import (SITUATION_FIELDS, append_record,  # noqa: E402
                                    build_identity, find_record, load_records,
                                    override_from_environment,
                                    qualification_status, record_from_situation,
                                    records_path, situation_digest,
                                    situation_from_manifest)


def _load_manifest(path: str) -> Dict[str, Any]:
    from placement import load_placement  # noqa: PLC0415
    return load_placement(path)


#: The coordinator's own defaults, not invented ones. The boundary layer is part
#: of the qualified topology, and the boundary follows from the budgets and the
#: context length -- so planning here with different numbers would compute a
#: different situation from the one `openmycelium run` actually executes, and
#: the record would never match anything.
PLANNING_DEFAULTS = {"cuda_budget": "14GiB", "rocm_budget": "14GiB",
                     "context_length": 4096}


def _plan_for_model(model: str, args=None) -> Dict[str, Any]:
    """Compile a placement so `status` can answer without one on disk.

    Planning an unqualified situation is explicitly permitted -- you have to be
    able to see what would run before you can qualify it.
    """
    import config as _config  # noqa: PLC0415
    from fabric import discover  # noqa: PLC0415
    from model_inspect import parse_size  # noqa: PLC0415
    from models import resolve  # noqa: PLC0415
    from placement import create_placement  # noqa: PLC0415

    # Through the store, exactly as `run` resolves it. Passing the bare name
    # straight to the planner looked like a missing checkpoint, which is a
    # confusing way to say "that is a store name, not a path".
    path = resolve(model)
    if path is None:
        raise SystemExit(f"  no model found for {model!r}; "
                         "try:  openmycelium model list")
    model = path
    resolved = _config.load({})
    report = discover({"nvidia": resolved.cuda_python,
                       "amd": resolved.rocm_python}, use_cache=False)
    cuda = getattr(args, "cuda_budget", "") or PLANNING_DEFAULTS["cuda_budget"]
    rocm = getattr(args, "rocm_budget", "") or PLANNING_DEFAULTS["rocm_budget"]
    context = int(getattr(args, "context_length", 0)
                  or PLANNING_DEFAULTS["context_length"])
    return create_placement(model, report, parse_size(cuda), parse_size(rocm),
                            context)


def _situation(args) -> Dict[str, Any]:
    if args.placement:
        return situation_from_manifest(_load_manifest(args.placement))
    if args.model:
        return situation_from_manifest(_plan_for_model(args.model, args))
    raise SystemExit("  give --placement or --model")


def _print_situation(situation: Dict[str, Any]) -> None:
    for field in SITUATION_FIELDS:
        print(f"    {field:<22} {situation.get(field)}")
    print(f"    {'situationDigest':<22} {situation_digest(situation)}")


def cmd_status(args) -> int:
    situation = _situation(args)
    records = load_records(args.ledger)
    status = qualification_status(situation, records=records)
    record = find_record(situation, records=records)

    if args.json:
        print(json.dumps({"qualificationStatus": status,
                          "situationDigest": situation_digest(situation),
                          "situation": situation,
                          "recordId": (record or {}).get("recordId"),
                          "qualifiedAt": (record or {}).get("qualifiedAt"),
                          "ledger": records_path(args.ledger)},
                         indent=2, sort_keys=True))
        return 0

    print(f"  qualification     {status}")
    print(f"  ledger            {records_path(args.ledger)}")
    _print_situation(situation)
    if record is None:
        print("\n  Not qualified here. Nothing has measured this exact "
              "combination.\n  Run the hardware gate with "
              "OM_QUALIFICATION_MODE=qualify and OM_QUALIFICATION_ACTOR set.")
        return 1
    print(f"\n  qualified at      {record.get('qualifiedAt')}")
    print(f"  evidence          {json.dumps(record.get('evidence') or {})[:200]}")
    return 0


def cmd_list(args) -> int:
    records = load_records(args.ledger)
    if args.json:
        print(json.dumps({"ledger": records_path(args.ledger),
                          "records": records}, indent=2, sort_keys=True))
        return 0
    print(f"  ledger  {records_path(args.ledger)}")
    if not records:
        print("  no adapter qualification records")
        return 0
    for record in records:
        situation = record.get("situation") or {}
        print(f"    {record.get('recordId', '')[:16]}  "
              f"{situation.get('adapterId')}@{situation.get('adapterVersion')}  "
              f"{situation.get('openmyceliumVersion')}  "
              f"model {str(situation.get('modelFingerprint'))[:12]}")
    return 0


def _evidence(path: Optional[str]) -> Dict[str, Any]:
    """The gate result. Required, and required to say it passed.

    Reading it here rather than trusting a flag means the record cannot claim a
    gate that never ran: the file has to exist and has to report zero failures.
    """
    if not path:
        raise SystemExit(
            "  --evidence is required: a record with no evidence is a claim, "
            "not a qualification")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as error:
        raise SystemExit(f"  cannot read the evidence file {path}: {error}")
    failures = document.get("failures")
    if failures is None:
        raise SystemExit(
            f"  {path} does not report a `failures` count; refusing to record "
            "a qualification from evidence that does not say whether it passed")
    if int(failures) != 0:
        raise SystemExit(
            f"  {path} reports {failures} failure(s); refusing to qualify a "
            "situation whose gate did not pass")
    document.setdefault("evidencePath", os.path.abspath(path))
    return document


def cmd_record(args) -> int:
    override = override_from_environment()
    if not override or override.get("mode") != "qualify":
        print("  refusing to write a qualification record outside qualification "
              "mode.\n  Set OM_QUALIFICATION_MODE=qualify and "
              "OM_QUALIFICATION_ACTOR=<who> and re-run the gate.",
              file=sys.stderr)
        return 65

    evidence = _evidence(args.evidence)
    situation = _situation(args)
    evidence["qualifiedBy"] = override.get("actor")
    evidence["qualificationReason"] = override.get("reason", "")

    record = record_from_situation(situation, evidence=evidence)
    path = append_record(record, args.ledger)
    print(f"  recorded {record['recordId'][:16]} in {path}")
    _print_situation(situation)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="openmycelium qualify",
        description="Inspect and write adapter qualification records")
    subparsers = parser.add_subparsers(dest="action", required=True)

    def common(sub):
        sub.add_argument("--placement", default="",
                         help="an existing placement manifest")
        sub.add_argument("--model", default="",
                         help="a model to plan, when no manifest exists yet")
        sub.add_argument("--ledger", default="",
                         help="override the qualification ledger path")
        sub.add_argument("--cuda-budget", dest="cuda_budget", default="")
        sub.add_argument("--rocm-budget", dest="rocm_budget", default="")
        sub.add_argument("--context-length", dest="context_length", type=int,
                         default=0)
        sub.add_argument("--json", action="store_true")

    status = subparsers.add_parser("status", help="is this situation qualified")
    common(status)
    status.set_defaults(handler=cmd_status)

    listing = subparsers.add_parser("list", help="records in the ledger")
    listing.add_argument("--ledger", default="")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(handler=cmd_list)

    record = subparsers.add_parser(
        "record", help="write a record for a situation whose gate passed")
    common(record)
    record.add_argument("--evidence", default="",
                        help="JSON from the passing gate; must report failures 0")
    record.set_defaults(handler=cmd_record)

    args = parser.parse_args()
    try:
        return int(args.handler(args))
    except AdapterError as error:
        print(f"  {error}", file=sys.stderr)
        return getattr(error, "exit_code", 65)
    except (RuntimeError, OSError, ValueError) as error:
        print(f"  {error}", file=sys.stderr)
        return 65


if __name__ == "__main__":
    raise SystemExit(main())
