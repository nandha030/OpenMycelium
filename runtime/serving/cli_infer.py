"""`openmycelium-infer`: inspect and plan a cross-vendor partitioned model.

Only implemented commands are exposed. `run`, `chat`, and `serve` are
deliberately absent until the partitioned runner exists: a command that appeared
to work while silently using one GPU would be worse than no command at all.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "mccl", "src"))

from model_inspect import (  # noqa: E402
    GIB,
    MIB,
    InspectionError,
    assess_fit,
    inspect_model,
    parse_size,
)
from partition import PartitionError, assign_tensors, plan_pipeline  # noqa: E402

try:
    from mccl.xvendor import QualificationLedger, TransportError
except ImportError:                                    # package not installed
    QualificationLedger = None                         # type: ignore
    TransportError = RuntimeError                      # type: ignore

LEDGER = os.environ.get("OM_XVENDOR_LEDGER", "/opt/xvendor_qualification.json")


def _bar(label: str, value: int, budget: int, width: int = 28) -> str:
    filled = 0 if budget <= 0 else min(width, int(width * value / budget))
    return (f"  {label:<14}[{'#' * filled}{'.' * (width - filled)}] "
            f"{value / GIB:6.2f} / {budget / GIB:.1f} GiB")


def cmd_inspect(args) -> int:
    try:
        model = inspect_model(args.model)
    except InspectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    summary = model.to_dict()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"Model            {os.path.basename(model.path)}")
    print(f"Architecture     {summary['architecture']}  "
          f"({summary['modelType']}, {summary['dtype']})")
    print(f"Layers           {summary['layers']}   hidden {summary['hiddenSize']}   "
          f"heads {summary['attentionHeads']} "
          f"(kv {summary['kvHeads']}, head_dim {summary['headDim']})")
    print(f"Vocabulary       {summary['vocabSize']}   "
          f"max positions {summary['maxPositionEmbeddings']}")
    print(f"Tensors          {summary['tensors']} across the checkpoint")
    print()
    print(f"Weights          {summary['totalGiB']} GiB total")
    print(f"  embedding etc. {summary['prologueBytes'] / GIB:.3f} GiB")
    print(f"  per layer      {summary['perLayerMiB']} MiB"
          f"{'  (uniform)' if summary['uniformLayers'] else '  (VARIES between layers)'}")
    print(f"  norm + head    {summary['epilogueBytes'] / GIB:.3f} GiB")
    print()
    kv_tok = summary["kvBytesPerToken"]
    print(f"KV cache         {kv_tok} B/token across all layers ({kv_tok / 1024:.0f} KiB)")
    for ctx in (2048, 8192, 32768):
        print(f"  at {ctx:>6} ctx  {model.kv_bytes(ctx) / GIB:6.3f} GiB")

    cuda_b, rocm_b = parse_size(args.cuda_budget), parse_size(args.rocm_budget)
    verdict = assess_fit(model, cuda_b, rocm_b, args.context_length)
    print()
    print(f"Fit at {args.context_length} tokens, budgets "
          f"{cuda_b / GIB:.0f} GiB CUDA + {rocm_b / GIB:.0f} GiB ROCm:")
    print(f"  across both    {'YES' if verdict.fits_combined else 'NO'}")
    print(f"  single card    "
          f"{'yes' if verdict.fits_single else 'no  <- requires both cards'}")
    for reason in verdict.reasons:
        print(f"    - {reason}")
    return 0 if verdict.fits_combined else 1


def cmd_plan(args) -> int:
    try:
        model = inspect_model(args.model)
    except InspectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    cuda_b, rocm_b = parse_size(args.cuda_budget), parse_size(args.rocm_budget)
    try:
        plan = plan_pipeline(model, cuda_b, rocm_b, args.context_length,
                             batch=args.batch, transport=args.transport,
                             cuda_layer_speed=args.cuda_layer_speed,
                             rocm_layer_speed=args.rocm_layer_speed)
    except PartitionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    qualified = None
    if QualificationLedger is not None:
        ledger = QualificationLedger.load(args.ledger)
        try:
            ledger.assert_permitted("cuda", "rocm")
            ledger.assert_permitted("rocm", "cuda")
            qualified = True
        except TransportError:
            qualified = False

    assignment = assign_tensors(model, plan)
    if args.json:
        payload = plan.to_dict()
        payload["transportQualifiedBothDirections"] = qualified
        payload["tensorAssignment"] = {k: len(v) for k, v in assignment.items()}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    first, second = plan.stages
    transport_note = ""
    if qualified is True:
        transport_note = "  (qualified both directions)"
    elif qualified is False:
        transport_note = "  (NOT QUALIFIED -- run the qualification first)"

    print(f"Qualified budget     {cuda_b / GIB:.0f} GiB CUDA + {rocm_b / GIB:.0f} GiB ROCm")
    print(f"Execution            pipeline-parallel, one boundary after layer {plan.boundary}")
    print(f"Transport            {plan.transport}{transport_note}")
    print()
    for stage in (first, second):
        parts = []
        if stage.holds_prologue:
            parts.append("embedding")
        parts.append(f"layers {stage.layer_span}")
        if stage.holds_epilogue:
            parts.append("final norm + LM head")
        print(f"{stage.vendor.upper():<5} allocation    {' + '.join(parts)}")
        print(_bar("weights", stage.weight_bytes, stage.budget_bytes))
        print(_bar("+ KV cache", stage.total_bytes, stage.budget_bytes))
        print(f"                 headroom {stage.headroom_bytes / GIB:.2f} GiB")
    print()
    print("KV cache             partitioned by owned layers")
    print(f"Boundary activation  {plan.activation_bytes_per_token} B/token, "
          f"{plan.prefill_activation_bytes / MIB:.1f} MiB at a "
          f"{plan.context_length}-token prefill")
    print("Model fits           yes")
    print()
    for reason in plan.reasons:
        print(f"  - {reason}")
    print()
    print("Tensor ownership (the loader must stream only these):")
    for vendor, names in assignment.items():
        print(f"  {vendor:<5} {len(names)} tensors")
    return 0


def cmd_load_check(args) -> int:
    from stage_loader import (  # noqa: PLC0415
        LoaderError, StageLoader, validate_ownership,
    )

    try:
        model = inspect_model(args.model)
    except InspectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.plan and os.path.isfile(args.plan):
        with open(args.plan, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        boundary = int(saved["boundaryAfterLayer"])
        plan = plan_pipeline(model, parse_size(args.cuda_budget),
                             parse_size(args.rocm_budget), args.context_length)
        if plan.boundary != boundary:
            print(f"note: recomputed boundary {plan.boundary} differs from the "
                  f"saved plan's {boundary}; using the saved plan", file=sys.stderr)
    else:
        try:
            plan = plan_pipeline(model, parse_size(args.cuda_budget),
                                 parse_size(args.rocm_budget), args.context_length)
        except PartitionError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

    assignment = assign_tensors(model, plan)
    ownership = validate_ownership((t.name for t in model.tensors), assignment)
    if not ownership.valid:
        print("error: tensor ownership is not exclusive", file=sys.stderr)
        print(json.dumps(ownership.to_dict(), indent=2), file=sys.stderr)
        return 3

    if args.stage not in assignment:
        print(f"error: no stage named {args.stage}", file=sys.stderr)
        return 2
    names = assignment[args.stage]

    torch = None
    device = "cpu"
    if not args.dry_run:
        try:
            import torch as _torch                      # noqa: PLC0415
            torch = _torch
        except ImportError:
            print("error: PyTorch is required unless --dry-run is given",
                  file=sys.stderr)
            return 2
        if not torch.cuda.is_available():
            print(f"error: no device available for stage {args.stage}", file=sys.stderr)
            return 2
        is_rocm = bool(getattr(torch.version, "hip", None))
        if (args.stage == "rocm") != is_rocm:
            build = "ROCm" if is_rocm else "CUDA"
            print(f"error: stage {args.stage} requested but this is a {build} "
                  f"PyTorch build; run each stage in its own environment",
                  file=sys.stderr)
            return 2
        device = "cuda:0"

    loader = StageLoader(args.model, args.stage, names, device, torch,
                         dry_run=args.dry_run)
    try:
        metrics = loader.load(limit=args.limit)
    except LoaderError as error:
        print(f"error: {error}", file=sys.stderr)
        return 4
    loader.verify_device_residency()

    stage_plan = next(s for s in plan.stages if s.vendor == args.stage)
    budget = stage_plan.budget_bytes
    within = metrics.gpu_peak_bytes <= budget

    if args.json:
        payload = metrics.to_dict()
        payload["ownership"] = ownership.to_dict()
        payload["withinQualifiedBudget"] = within
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        m = metrics
        print(f"Stage                     {m.stage}  ({'dry run' if args.dry_run else m.device})")
        print(f"GPU identity              {m.device_name or 'n/a (dry run)'}")
        print(f"Assigned tensors          {m.assigned_tensors}")
        print(f"Loaded tensors            {m.loaded_tensors}"
              f"{f' (limit {args.limit})' if args.limit else ''}")
        print(f"Assigned weight bytes     {m.assigned_bytes / GIB:.3f} GiB")
        print(f"Actual GPU allocation     {m.gpu_allocated_bytes / GIB:.3f} GiB "
              f"(peak {m.gpu_peak_bytes / GIB:.3f} GiB)")
        print(f"Peak CPU staging memory   {m.cpu_staging_peak_bytes / MIB:.1f} MiB")
        print(f"Shards opened             {m.shards_opened}")
        print(f"Missing tensors           {len(ownership.missing)}")
        print(f"Unexpected tensors        {len(ownership.unexpected)}")
        print(f"Duplicated tensors        {len(ownership.duplicated)}")
        print(f"Every tensor one owner    {'yes' if ownership.valid else 'NO'}")
        print(f"Device allocation checked {'yes' if m.device_verified else 'no'}"
              f"  -- {m.verification_note}")
        print(f"Within qualified budget   {'yes' if within else 'NO'}  "
              f"({m.gpu_peak_bytes / GIB:.2f} / {budget / GIB:.1f} GiB)")
        print(f"Elapsed                   {m.seconds:.1f} s")
        if m.errors:
            for err in m.errors[:5]:
                print(f"  error: {err}")

    released = loader.unload()
    if not args.json and not args.dry_run:
        print(f"Released on unload        {released / GIB:.3f} GiB")
    return 0 if (ownership.valid and within and not metrics.errors) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="openmycelium-infer",
        description="Cross-vendor partitioned inference: inspection and planning")
    parser.add_argument("--ledger", default=LEDGER)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--model", required=True)
    common.add_argument("--cuda-budget", default="14GiB")
    common.add_argument("--rocm-budget", default="14GiB")
    common.add_argument("--context-length", type=int, default=2048)
    common.add_argument("--json", action="store_true")

    sub.add_parser("inspect", parents=[common],
                   help="report architecture, sizes, KV cache and fit")
    planner = sub.add_parser("plan", parents=[common],
                             help="choose the contiguous layer boundary")
    planner.add_argument("--batch", type=int, default=1)
    planner.add_argument("--transport", default="host-staged-xvendor")
    planner.add_argument("--cuda-layer-speed", type=float, default=1.0,
                         help="relative layers/second; measured values shift work")
    planner.add_argument("--rocm-layer-speed", type=float, default=1.0)

    checker = sub.add_parser("load-check", parents=[common],
                             help="stream one stage onto its device and measure it")
    checker.add_argument("--stage", required=True, choices=("cuda", "rocm"))
    checker.add_argument("--plan", default="")
    checker.add_argument("--dry-run", action="store_true",
                         help="walk the metadata without uploading anything")
    checker.add_argument("--limit", type=int, default=None,
                         help="load only the first N tensors")

    args = parser.parse_args(argv)
    return {"inspect": cmd_inspect, "plan": cmd_plan,
            "load-check": cmd_load_check}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
