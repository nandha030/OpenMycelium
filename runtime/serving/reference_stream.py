"""Streaming CPU reference: the whole model, one layer resident at a time.

Step 4 needs logits produced by something that shares as little as possible with
the thing under test. A second GPU split would share the split logic; a
whole-model CPU run needs 22.8 GiB and this host has 15 GiB of RAM.

So this walks all 40 layers in order on the CPU, loading each layer's weights,
running it, and freeing it before the next. Peak memory is one layer plus the
embedding or head -- roughly 2 GiB -- and no partitioning, no transport, and no
second process are involved. If the split pipeline agrees with this within BF16
tolerance, the agreement is meaningful.

It is slow by construction. That is acceptable for a reference.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import sys
import time
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from model_inspect import GIB, MIB, inspect_model  # noqa: E402
from stage_loader import StageLoader  # noqa: E402
from stage_model import BoundaryMeta  # noqa: E402


def _rss_mib() -> float:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def run_reference(model_path: str, token: int, position: int,
                  torch: Any, progress: bool = False,
                  save_logits: str = "") -> Dict[str, Any]:
    from transformers import MistralConfig  # noqa: PLC0415
    from transformers.models.mistral.modeling_mistral import (  # noqa: PLC0415
        MistralDecoderLayer, MistralRMSNorm, MistralRotaryEmbedding,
    )

    model = inspect_model(model_path)
    with open(os.path.join(model_path, "config.json"), "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    config = MistralConfig(**raw)
    config._attn_implementation = "eager"
    dtype = getattr(torch, str(raw.get("torch_dtype", "bfloat16")))
    device = "cpu"
    started = time.perf_counter()

    def load_one(names: List[str]) -> Dict[str, Any]:
        loader = StageLoader(model_path, "reference", names, device, torch)
        loader.load()
        return dict(loader.tensors)

    # --- embedding, then freed -------------------------------------------
    embed_weight = load_one(["model.embed_tokens.weight"])["model.embed_tokens.weight"]
    ids = torch.tensor([[token]], dtype=torch.long, device=device)
    hidden = torch.nn.functional.embedding(ids, embed_weight)
    del embed_weight
    gc.collect()

    position_ids = torch.tensor([[position]], dtype=torch.long, device=device)
    rotary = MistralRotaryEmbedding(config=config).to(device)
    rotary_emb = rotary(hidden, position_ids)

    # --- one layer at a time ----------------------------------------------
    layer_digests: List[str] = []
    boundary_slice: List[float] = []
    for index in range(model.layer_count):
        prefix = f"model.layers.{index}."
        names = sorted(t.name for t in model.tensors if t.name.startswith(prefix))
        weights = load_one(names)
        with torch.device("meta"):
            layer = MistralDecoderLayer(config, layer_idx=index)
        for name, tensor in weights.items():
            path = name[len(prefix):].split(".")
            target: Any = layer
            for part in path[:-1]:
                target = getattr(target, part)
            setattr(target, path[-1], torch.nn.Parameter(tensor, requires_grad=False))
        with torch.inference_mode():
            out = layer(hidden, attention_mask=None, position_ids=position_ids,
                        past_key_values=None, use_cache=False,
                        position_embeddings=rotary_emb)
        hidden = out[0] if isinstance(out, tuple) else out
        if hidden.dtype != dtype:
            raise RuntimeError(f"layer {index} produced {hidden.dtype}, expected {dtype}")
        raw_bytes = hidden.contiguous().view(torch.uint8).numpy().tobytes()
        layer_digests.append(hashlib.sha256(raw_bytes).hexdigest()[:8])
        if index == 19:
            # Same cut point the split uses, so divergence can be located
            # rather than only observed at the end.
            boundary_slice = [round(float(v), 5)
                              for v in hidden.reshape(-1)[:16].float().tolist()]
        del layer, weights, out
        gc.collect()
        if progress and (index + 1) % 10 == 0:
            print(f"  layer {index + 1}/{model.layer_count}  "
                  f"peak RSS {_rss_mib():.0f} MiB", file=sys.stderr, flush=True)

    # --- final norm and head ----------------------------------------------
    norm_weight = load_one(["model.norm.weight"])["model.norm.weight"]
    with torch.device("meta"):
        norm = MistralRMSNorm(config.hidden_size, eps=config.rms_norm_eps)
    norm.weight = torch.nn.Parameter(norm_weight, requires_grad=False)
    with torch.inference_mode():
        hidden = norm(hidden)
    del norm, norm_weight
    gc.collect()

    head_weight = load_one(["lm_head.weight"])["lm_head.weight"]
    with torch.inference_mode():
        logits = torch.nn.functional.linear(hidden, head_weight)
    del head_weight
    gc.collect()
    if save_logits:
        host = logits.detach().to("cpu").contiguous()
        with open(save_logits, "wb") as handle:
            handle.write(bytes(host.view(torch.uint8).reshape(-1).tolist()))

    flat = logits.reshape(-1)
    values, indices = torch.topk(flat, k=5)
    raw_bytes = flat.contiguous().view(torch.uint8).numpy().tobytes()
    return {
        "source": "streaming-cpu-reference",
        "device": device,
        "token": token,
        "position": position,
        "layers": model.layer_count,
        "shape": list(logits.shape),
        "dtype": str(logits.dtype),
        "allFinite": bool(torch.isfinite(logits).all().item()),
        "topTokenIds": [int(i) for i in indices.tolist()],
        "topValues": [round(float(v), 4) for v in values.float().tolist()],
        "first16Fp32": [round(float(v), 4) for v in flat[:16].float().tolist()],
        "rawSha256": hashlib.sha256(raw_bytes).hexdigest()[:16],
        "topBits": [{"tokenId": int(i),
                     "value": float(flat[int(i)].float().item()),
                     "bits": f"0x{int(flat.view(torch.uint16)[int(i)].item()):04x}"}
                    for i in indices.tolist()],
        "boundaryLayerDigest": layer_digests[19] if len(layer_digests) > 19 else None,
        "boundaryFirst16": boundary_slice,
        "peakRssMiB": round(_rss_mib(), 1),
        "seconds": round(time.perf_counter() - started, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Streaming CPU reference logits")
    parser.add_argument("--model", required=True)
    parser.add_argument("--token", type=int, default=1234)
    parser.add_argument("--position", type=int, default=0)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--save-logits", default="")
    args = parser.parse_args()

    import torch  # noqa: PLC0415
    torch.manual_seed(0)
    torch.set_num_threads(max(1, (os.cpu_count() or 4) - 1))
    print(json.dumps(run_reference(args.model, args.token, args.position,
                                   torch, args.progress, args.save_logits),
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
