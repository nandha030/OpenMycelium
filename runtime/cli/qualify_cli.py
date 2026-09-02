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


def cmd_require(args) -> int:
    """Exit non-zero unless this situation holds at least the asked-for scope.

    The machine-enforced half of the scope distinction, in a form a script can
    call. Release sealing, an enforcement canary and paging work run this and
    stop on a non-zero exit, so "a single run does not authorise a release" is a
    check rather than a paragraph somebody has to remember.
    """
    from adapters.qualification import require_scope  # noqa: PLC0415
    situation = _situation(args)
    records = load_records(args.ledger)
    try:
        granted = require_scope(situation, args.scope, records=records)
    except AdapterError as refusal:
        print(f"  refused: {refusal}")
        return refusal.exit_code
    print(f"  {args.scope} satisfied by record "
          f"{str(granted.get('recordId'))[:16]}")
    return 0


def _verdict_from_run(document: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Compute a verdict from a run document, or None if it is not one.

    `openmycelium run --json` describes a run; it states no verdict, because a
    run is not a gate. The console's own remediation told operators to record
    from exactly that output, so the documented path refused itself at the last
    step -- and the ledger shows why nobody noticed: every existing record was
    written from a gate script's summary, never from this route.

    Rather than demand a second tool, the verdict is derived here from checks
    that are true of any correct run and need no per-model constants:

      - every worker exited 0
      - a result exists and tokens were produced
      - the stages partition the model: no tensor owned twice, and every tensor
        owned once

    That last one is the substantive check. Disjoint layer ranges with a
    duplicated embedding would look fine and be two copies on two devices.
    """
    placement = document.get("placement")
    codes = document.get("exitCodes")
    if not isinstance(placement, dict) or not isinstance(codes, dict):
        return None                      # not a run document; leave it alone

    result = document.get("result") or {}
    stages = placement.get("stages") or []
    owned = [set(stage.get("tensors") or ()) for stage in stages]
    union = set().union(*owned) if owned else set()
    duplicated = sum(len(s) for s in owned) - len(union)
    declared = (placement.get("model") or {}).get("tensorCount")

    checks = [
        {"check": "every worker exited 0",
         "passed": bool(codes) and all(int(c) == 0 for c in codes.values()),
         "actual": codes},
        {"check": "a result was produced",
         "passed": bool(result.get("generatedTokens")),
         "actual": len(result.get("generatedTokens") or [])},
        {"check": "no tensor is owned twice",
         "passed": duplicated == 0, "actual": duplicated},
        {"check": "every model tensor is owned exactly once",
         "passed": declared is not None and len(union) == declared,
         "actual": f"{len(union)} owned, model declares {declared}"},
    ]
    failed = [c for c in checks if not c["passed"]]
    return {
        "failures": len(failed),
        "checksRun": len(checks),
        "checks": checks,
        "gate": "single-run qualification",
        # Named honestly. A record that overstated its scope would claim more
        # was proven than was.
        "scope": ("one run of this exact situation, with its ownership "
                  "partition and worker exits verified. NOT the full gate "
                  "battery: no performance campaign, no refusal paths, no "
                  "lifecycle, no installed-wheel suite."),
        "run": {"placementId": placement.get("placementId"),
                "manifestDigest": placement.get("manifestDigest"),
                "stopReason": result.get("stopReason"),
                "promptTokens": result.get("promptTokens")},
    }


def _evidence(path: Optional[str],
              allow_prose: bool = False) -> Dict[str, Any]:
    """The gate result. Required, and required to say it passed.

    Reading it here rather than trusting a flag means the record cannot claim a
    gate that never ran: the file has to exist and has to report zero failures.

    A gate summary states `failures` itself. A run document does not, so its
    verdict is computed from the run -- see `_verdict_from_run`. Either way the
    number is derived from evidence on disk and never taken on trust.
    """
    if not path:
        raise SystemExit(
            "  --evidence is required: a record with no evidence is a claim, "
            "not a qualification")
    try:
        # utf-8-sig, so a byte-order mark is consumed rather than counted as
        # content. PowerShell's `>` writes one, and reporting it as "text before
        # the JSON" blamed the runtime for the shell's redirection.
        with open(path, "r", encoding="utf-8-sig") as handle:
            text = handle.read()
    except OSError as error:
        raise SystemExit(f"  cannot read the evidence file {path}: {error}")
    try:
        # Leading whitespace is fine -- json.loads accepts it, and the BOM was
        # consumed by utf-8-sig above. Anything else before the document is
        # refused, because scanning forward to the first `{` would accept a file
        # with arbitrary text in front of it and silently write a qualification
        # record from whatever followed. An evidence reader that hunts for
        # something parseable is not verifying evidence.
        document = json.loads(text)
    except ValueError as error:
        start = text.find("{")
        legacy = start > 0 and not text[:start].strip()
        if legacy:
            # Only whitespace preceded it; json.loads would have handled that,
            # so reaching here means the document itself is malformed.
            raise SystemExit(f"  cannot read the evidence file {path}: {error}")
        if start > 0 and allow_prose:
            document = json.loads(text[start:])
            print(f"  LEGACY IMPORT: {path} has {start} character(s) of text "
                  "before the JSON, which is what an older runtime wrote when "
                  "it streamed tokens to stdout under --json.")
            print("  Accepted only because --legacy-prose-evidence was given. "
                  "The record will be marked as a legacy import.")
            document.setdefault("evidenceLegacyImport", True)
            document.setdefault("evidenceProseBytesSkipped", start)
        elif start > 0:
            raise SystemExit(
                f"  {path} has {start} character(s) of text before the JSON "
                f"document. Refusing to scan past it: a reader that hunts for "
                f"the first '{{' would write a qualification record from "
                f"whatever happened to follow.\n"
                f"  This is what an older runtime wrote when it streamed tokens "
                f"to stdout under --json. Re-run the gate on a current build, "
                f"or pass --legacy-prose-evidence to import it deliberately.")
        else:
            raise SystemExit(f"  cannot read the evidence file {path}: {error}")

    if document.get("failures") is None:
        derived = _verdict_from_run(document)
        if derived is None:
            raise SystemExit(
                f"  {path} does not report a `failures` count and is not a run "
                "document; refusing to record a qualification from evidence "
                "that does not say whether it passed")
        derived["derivedFrom"] = "run document"
        # Carried across, because deriving a verdict replaces the document and
        # would otherwise drop the very marks that say this was a legacy
        # import -- leaving the ledger with no trace of it, which is the one
        # thing a legacy import must never do.
        for mark in ("evidenceLegacyImport", "evidenceProseBytesSkipped"):
            if mark in document:
                derived[mark] = document[mark]
        document = derived

    failures = document.get("failures")
    if int(failures) != 0:
        detail = "; ".join(c["check"] for c in document.get("checks", ())
                           if not c.get("passed"))
        raise SystemExit(
            f"  {path} reports {failures} failure(s); refusing to qualify a "
            f"situation whose gate did not pass{': ' + detail if detail else ''}")
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

    evidence = _evidence(args.evidence,
                         allow_prose=getattr(args, "legacy_prose_evidence", False))
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
    record.add_argument("--legacy-prose-evidence", action="store_true",
                        dest="legacy_prose_evidence",
                        help="import evidence written by a runtime that "
                             "streamed tokens ahead of the JSON; marked "
                             "in the record as a legacy import")
    record.add_argument("--evidence", default="",
                        help="JSON from the passing gate; must report failures 0")
    record.set_defaults(handler=cmd_record)

    require = subparsers.add_parser(
        "require", help="exit non-zero unless this situation holds a scope")
    common(require)
    require.add_argument("--scope", default="FULL_GATE_QUALIFIED",
                         choices=("EXECUTION_QUALIFIED", "FULL_GATE_QUALIFIED"),
                         help="the minimum scope this situation must hold")
    require.set_defaults(handler=cmd_require)

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
