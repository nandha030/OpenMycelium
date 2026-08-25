"""Command-line interface for installing and qualifying HetCCL nodes."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

from . import __version__
from .collective import CollectiveConfig, HetCCLCollective
from .discovery import discover_capabilities
from .kvcache import KVCacheBroker
from .planner import plan_collective
from .router import HetRouter, InferenceNode, InferenceRequest
from .server import Coordinator
from .serving import VLLMServingAdapter


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hetccl", description="OpenMycelium heterogeneous collective runtime")
    root.add_argument("--version", action="version", version=f"HetCCL {__version__}")
    commands = root.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="discover devices, adapters, and transports")
    doctor.add_argument("--json", action="store_true", dest="json_output")

    plan = commands.add_parser("plan", help="show the locally executable collective plan")
    plan.add_argument("--prefer-device-direct", action="store_true")

    serve = commands.add_parser("serve", help="run the portable collective coordinator")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=29500)
    serve.add_argument("--timeout", type=float, default=120)

    allreduce = commands.add_parser("allreduce", help="run a typed portable AllReduce")
    allreduce.add_argument("--rank", type=int, required=True)
    allreduce.add_argument("--world-size", type=int, required=True)
    allreduce.add_argument("--coordinator", default="127.0.0.1:29500")
    allreduce.add_argument("--group", default="cli")
    allreduce.add_argument("--dtype", choices=("f32", "f64", "i32", "i64"), default="f64")
    allreduce.add_argument("--reduction", choices=("sum", "avg", "min", "max"), default="sum")
    allreduce.add_argument("values", nargs="+", type=float)

    kv_serve = commands.add_parser("kv-serve", help="run the bounded KV-cache transfer broker")
    kv_serve.add_argument("--host", default="0.0.0.0")
    kv_serve.add_argument("--port", type=int, default=29600)
    kv_serve.add_argument("--max-mib", type=int, default=1024)
    kv_serve.add_argument("--ttl", type=float, default=300)

    vllm_check = commands.add_parser("vllm-check", help="inspect a vLLM/OpenAI-compatible endpoint")
    vllm_check.add_argument("--url", required=True)
    vllm_check.add_argument("--model", default="")
    vllm_check.add_argument("--api-key-env", default="OPENAI_API_KEY")
    vllm_check.add_argument("--runtime", choices=("cuda", "rocm", "metal", "oneapi", "cpu"), default="cuda")

    inference_plan = commands.add_parser("inference-plan", help="plan whole, split, or speculative inference")
    inference_plan.add_argument("--nodes", required=True, help="JSON file containing an array of measured node profiles")
    inference_plan.add_argument("--request", required=True, help="JSON file containing the inference request")
    return root


def main(arguments: Sequence[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    if args.command == "doctor":
        report = discover_capabilities()
        if args.json_output:
            print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        else:
            print(f"HetCCL {__version__} on {report.hostname} ({report.operating_system}/{report.machine})")
            if report.devices:
                for device in report.devices:
                    memory = f" {device.memory_mib} MiB" if device.memory_mib else ""
                    print(f"  [{device.runtime}] {device.index}: {device.name}{memory}")
            else:
                print("  [host] no accelerator detected; TCP CPU path is ready")
            for name, status in report.adapters.items():
                print(f"  adapter {name}: {status}")
            for name, status in report.transports.items():
                print(f"  transport {name}: {status}")
        return 0
    if args.command == "plan":
        report = discover_capabilities()
        print(json.dumps(plan_collective([report], args.prefer_device_direct).to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "serve":
        coordinator = Coordinator(args.host, args.port, args.timeout)
        print(f"HetCCL coordinator listening on {args.host}:{args.port}", flush=True)
        try:
            coordinator.serve_forever()
        except KeyboardInterrupt:
            return 0
        finally:
            coordinator.close()
    if args.command == "allreduce":
        host, separator, raw_port = args.coordinator.rpartition(":")
        if not separator or not host:
            raise SystemExit("--coordinator must use host:port")
        collective = HetCCLCollective(CollectiveConfig(args.rank, args.world_size, host, int(raw_port), args.group))
        values = [int(value) for value in args.values] if args.dtype.startswith("i") else args.values
        print(json.dumps({"rank": args.rank, "result": collective.all_reduce(values, args.dtype, args.reduction)}))
        return 0
    if args.command == "kv-serve":
        if args.max_mib <= 0 or args.ttl <= 0:
            raise SystemExit("--max-mib and --ttl must be positive")
        broker = KVCacheBroker(
            args.host,
            args.port,
            args.max_mib * 1024 * 1024,
            args.ttl,
            os.environ.get("HETCCL_KV_AUTH_TOKEN", ""),
        )
        print(f"HetCCL KV-cache broker listening on {args.host}:{args.port}", flush=True)
        try:
            broker.serve_forever()
        except KeyboardInterrupt:
            return 0
        finally:
            broker.close()
    if args.command == "vllm-check":
        adapter = VLLMServingAdapter(
            args.url,
            args.model,
            os.environ.get(args.api_key_env, ""),
            runtime=args.runtime,
        )
        print(json.dumps(adapter.capabilities().to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "inference-plan":
        raw_nodes = _load_json(args.nodes)
        raw_request = _load_json(args.request)
        if not isinstance(raw_nodes, list) or not isinstance(raw_request, dict):
            raise SystemExit("--nodes must contain an array and --request must contain an object")
        nodes = []
        for item in raw_nodes:
            if not isinstance(item, dict):
                raise SystemExit("every node profile must be an object")
            values = dict(item)
            values["capabilities"] = tuple(values.get("capabilities", ()))
            nodes.append(InferenceNode(**values))
        try:
            route = HetRouter().plan(InferenceRequest(**raw_request), nodes)
        except ValueError as error:
            raise SystemExit(str(error))
        print(json.dumps(route.to_dict(), indent=2, sort_keys=True))
        return 0
    return 2


def _load_json(path: str) -> object:
    try:
        with open(path, "r", encoding="utf-8") as source:
            return json.load(source)
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"unable to load {path}: {error}") from error


if __name__ == "__main__":
    sys.exit(main())
