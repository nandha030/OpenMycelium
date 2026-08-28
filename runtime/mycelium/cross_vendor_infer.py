"""Cross-vendor disaggregated inference: prefill on one GPU, decode on another.

This is the smallest honest demonstration of two different-vendor GPUs serving
one inference request. It is not a shortcut around the hardware: NVIDIA and AMD
still cannot address each other's memory, so the request is split at the only
place where a clean handoff exists.

    prefill stage   run the prompt, produce the KV cache          (device A)
    transfer        cache -> pinned host -> MCCL broker -> host (network)
    decode stage    load the cache, generate tokens               (device B)

The KV cache is the natural cut point: it is produced once, consumed many
times, and has a well-defined layout that survives leaving a vendor's memory.
Activations would have to cross on every layer; the cache crosses once.

The model is a small deterministic decoder built from seeded weights, so both
stages construct byte-identical parameters without shipping a checkpoint, and a
single-device reference run gives exact ground truth for the split run.

Devices are named by vendor (`cuda`, `rocm`, `cpu`). Any stage whose vendor has
no working runtime falls back to CPU and says so, so the same program runs on a
machine with one GPU today and two vendors' GPUs later, unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import zlib
from dataclasses import dataclass
from typing import Any

CANONICAL_LAYOUT = "kv-layer-batch-head-sequence-dim"


@dataclass(frozen=True)
class ModelConfig:
    vocab: int = 256
    layers: int = 4
    heads: int = 4
    head_dim: int = 16
    seed: int = 20260824

    @property
    def width(self) -> int:
        return self.heads * self.head_dim

    def to_dict(self) -> dict[str, Any]:
        return {"vocab": self.vocab, "layers": self.layers, "heads": self.heads, "head_dim": self.head_dim, "seed": self.seed}


def build_weights(config: ModelConfig, device: str, torch: Any) -> dict[str, Any]:
    """Deterministic parameters, identical on every device and every process.

    Seeding per tensor rather than per model means a stage can build only the
    parts it needs and still agree with the other stage bit for bit.
    """

    def tensor(name: str, *shape: int) -> Any:
        generator = torch.Generator(device="cpu")
        # zlib.crc32, not hash(): Python randomizes string hashing per process,
        # so hash() would give each stage different weights.
        generator.manual_seed(config.seed + zlib.crc32(name.encode("utf-8")))
        raw = torch.randn(*shape, generator=generator, dtype=torch.float32)
        return (raw / (shape[-1] ** 0.5)).to(device)

    weights: dict[str, Any] = {
        "embed": tensor("embed", config.vocab, config.width),
        "out": tensor("out", config.width, config.vocab),
    }
    for layer in range(config.layers):
        for part in ("q", "k", "v", "o"):
            weights[f"{part}{layer}"] = tensor(f"{part}{layer}", config.width, config.width)
    return weights


def _rms_norm(tensor: Any, torch: Any, epsilon: float = 1e-6) -> Any:
    """Normalize the residual stream the way a real decoder does.

    A saturating activation here drives the toy model to a fixed point, which
    would make the cross-vendor comparison pass on a constant and prove nothing.
    """
    scale = torch.rsqrt(tensor.pow(2).mean(dim=-1, keepdim=True) + epsilon)
    return tensor * scale


def _heads(tensor: Any, config: ModelConfig) -> Any:
    batch, sequence, _ = tensor.shape
    return tensor.view(batch, sequence, config.heads, config.head_dim).transpose(1, 2)


def prefill(tokens: list[int], config: ModelConfig, weights: dict[str, Any], device: str, torch: Any) -> tuple[Any, Any]:
    """Run the prompt and return the KV cache plus the final hidden state."""
    ids = torch.tensor([tokens], dtype=torch.long, device=device)
    hidden = weights["embed"][ids]
    batch, sequence = ids.shape
    cache = torch.zeros(
        2, config.layers, batch, config.heads, sequence, config.head_dim,
        dtype=torch.float32, device=device,
    )
    mask = torch.full((sequence, sequence), float("-inf"), device=device).triu(1)
    for layer in range(config.layers):
        query = _heads(hidden @ weights[f"q{layer}"], config)
        key = _heads(hidden @ weights[f"k{layer}"], config)
        value = _heads(hidden @ weights[f"v{layer}"], config)
        cache[0, layer] = key
        cache[1, layer] = value
        scores = query @ key.transpose(-1, -2) / (config.head_dim ** 0.5) + mask
        context = (torch.softmax(scores, dim=-1) @ value).transpose(1, 2).reshape(batch, sequence, config.width)
        hidden = _rms_norm(hidden + context @ weights[f"o{layer}"], torch)
    return cache, hidden[:, -1:, :]


def decode(
    cache: Any,
    last_hidden: Any,
    steps: int,
    config: ModelConfig,
    weights: dict[str, Any],
    device: str,
    torch: Any,
) -> list[int]:
    """Generate greedily, appending each step's keys and values to the cache."""
    generated: list[int] = []
    hidden = last_hidden.to(device)
    cache = cache.to(device)
    for _ in range(steps):
        logits = hidden[:, -1, :] @ weights["out"]
        token = int(torch.argmax(logits, dim=-1).item())
        generated.append(token)
        hidden = weights["embed"][torch.tensor([[token]], dtype=torch.long, device=device)]
        appended = []
        for layer in range(config.layers):
            query = _heads(hidden @ weights[f"q{layer}"], config)
            key = _heads(hidden @ weights[f"k{layer}"], config)
            value = _heads(hidden @ weights[f"v{layer}"], config)
            keys = torch.cat([cache[0, layer], key], dim=2)
            values = torch.cat([cache[1, layer], value], dim=2)
            appended.append((keys, values))
            scores = query @ keys.transpose(-1, -2) / (config.head_dim ** 0.5)
            context = (torch.softmax(scores, dim=-1) @ values).transpose(1, 2).reshape(1, 1, config.width)
            hidden = _rms_norm(hidden + context @ weights[f"o{layer}"], torch)
        cache = torch.stack(
            [
                torch.stack([item[0] for item in appended]),
                torch.stack([item[1] for item in appended]),
            ]
        )
    return generated


def resolve_device(vendor: str, torch: Any) -> tuple[str, str]:
    """Map a vendor to a usable torch device, reporting any fallback honestly.

    A ROCm build of PyTorch exposes AMD GPUs as `cuda` devices, so the device
    string alone cannot tell the vendors apart. `torch.version.hip` is what
    distinguishes a ROCm build from a CUDA one, and asking for `rocm` on a CUDA
    build must not silently hand back an NVIDIA GPU.
    """
    if vendor not in {"cuda", "rocm"}:
        return "cpu", "cpu"
    is_rocm_build = bool(getattr(torch.version, "hip", None))
    wanted_rocm = vendor == "rocm"
    if torch.cuda.is_available() and wanted_rocm == is_rocm_build:
        return "cuda:0", f"{vendor} on {torch.cuda.get_device_name(0)}"
    if torch.cuda.is_available():
        present = "ROCm" if is_rocm_build else "CUDA"
        return "cpu", f"{vendor} requested but this is a {present} build; standing in with CPU"
    return "cpu", f"{vendor} requested but no GPU runtime is present; standing in with CPU"


def cache_descriptor(cache: Any, config: ModelConfig, vendor: str, cache_id: str) -> Any:
    from mccl.kvcache import KVCacheDescriptor  # noqa: PLC0415

    return KVCacheDescriptor(
        cache_id=cache_id,
        model=f"toy-decoder-L{config.layers}H{config.heads}D{config.head_dim}",
        source_runtime=vendor if vendor in {"cuda", "rocm", "cpu"} else "cpu",
        dtype="f32",
        shape=tuple(int(value) for value in cache.shape),
        sequence=int(cache.shape[4]),
        layout=CANONICAL_LAYOUT,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-vendor disaggregated inference")
    parser.add_argument("--role", required=True, choices=("prefill", "decode", "reference"))
    parser.add_argument("--vendor", default="cpu", help="cuda, rocm, or cpu")
    parser.add_argument("--prompt", default="7,11,23,42,5,99,3,64")
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--cache-id", default="demo-request-1")
    parser.add_argument("--broker-host", default="127.0.0.1")
    parser.add_argument("--broker-port", type=int, default=29600)
    parser.add_argument("--layers", type=int, default=4)
    args = parser.parse_args()

    import torch  # noqa: PLC0415

    config = ModelConfig(layers=args.layers)
    device, note = resolve_device(args.vendor, torch)
    weights = build_weights(config, device, torch)
    tokens = [int(value) % config.vocab for value in args.prompt.split(",")]
    report: dict[str, Any] = {"role": args.role, "vendor": args.vendor, "device": device, "note": note}

    if args.role == "reference":
        cache, hidden = prefill(tokens, config, weights, device, torch)
        report["tokens"] = decode(cache, hidden, args.steps, config, weights, device, torch)
        report["cacheBytes"] = cache.numel() * 4
    elif args.role == "prefill":
        from mccl.kvcache import KVCacheClient  # noqa: PLC0415

        cache, hidden = prefill(tokens, config, weights, device, torch)
        # Leaving the device is the whole point: the cache is staged into host
        # memory, which is the only representation the other vendor can accept.
        host_cache = cache.detach().to("cpu").contiguous()
        payload = host_cache.numpy().tobytes()
        descriptor = cache_descriptor(host_cache, config, args.vendor, args.cache_id)
        client = KVCacheClient(args.broker_host, args.broker_port, auth_token=os.environ.get("MCCL_KV_AUTH_TOKEN", ""))
        report["checksum"] = client.put(descriptor, payload)
        report["cacheBytes"] = len(payload)
        report["lastToken"] = tokens[-1]
        # The hidden state is small request metadata, not bulk data; a real
        # router carries it on the control path beside the cache transfer.
        report["lastHidden"] = hidden.detach().to("cpu").reshape(-1).tolist()
    else:
        from mccl.kvcache import KVCacheClient  # noqa: PLC0415

        client = KVCacheClient(args.broker_host, args.broker_port, auth_token=os.environ.get("MCCL_KV_AUTH_TOKEN", ""))
        record = client.get(args.cache_id)
        hidden_values = json.loads(os.environ["DEMO_LAST_HIDDEN"])
        cache = torch.frombuffer(bytearray(record.payload), dtype=torch.float32).reshape(record.descriptor.shape)
        hidden = torch.tensor(hidden_values, dtype=torch.float32).reshape(1, 1, config.width)
        report["receivedBytes"] = len(record.payload)
        report["sourceRuntime"] = record.descriptor.source_runtime
        report["tokens"] = decode(cache, hidden, args.steps, config, weights, device, torch)

    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
