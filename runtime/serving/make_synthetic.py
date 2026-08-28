"""Write a tiny checkpoint with Mistral-Nemo's exact tensor topology.

Same architecture, same tensor names, same sharding structure, ~1/500th the
size. Exercising the loader here first means a failure on the real checkpoint is
about scale, not about shape.

    python make_synthetic.py /tmp/tiny-nemo --layers 8 --hidden 256
"""

from __future__ import annotations

import argparse
import json
import os
import struct
from typing import Dict, List, Tuple

DTYPE = "BF16"
ELEMENT = 2


def _tensor_plan(layers: int, hidden: int, inter: int, vocab: int,
                 heads: int, kv_heads: int, head_dim: int) -> List[Tuple[str, Tuple[int, ...]]]:
    plan: List[Tuple[str, Tuple[int, ...]]] = [
        ("model.embed_tokens.weight", (vocab, hidden)),
    ]
    for layer in range(layers):
        p = f"model.layers.{layer}"
        plan += [
            (f"{p}.self_attn.q_proj.weight", (heads * head_dim, hidden)),
            (f"{p}.self_attn.k_proj.weight", (kv_heads * head_dim, hidden)),
            (f"{p}.self_attn.v_proj.weight", (kv_heads * head_dim, hidden)),
            (f"{p}.self_attn.o_proj.weight", (hidden, heads * head_dim)),
            (f"{p}.mlp.gate_proj.weight", (inter, hidden)),
            (f"{p}.mlp.up_proj.weight", (inter, hidden)),
            (f"{p}.mlp.down_proj.weight", (hidden, inter)),
            (f"{p}.input_layernorm.weight", (hidden,)),
            (f"{p}.post_attention_layernorm.weight", (hidden,)),
        ]
    plan += [
        ("model.norm.weight", (hidden,)),
        ("lm_head.weight", (vocab, hidden)),
    ]
    return plan


#: bf16 is the top half of an fp32, stored little-endian, so the second byte of
#: each pair carries the sign and exponent. Leaving it random makes roughly one
#: element in 256 a NaN (exponent all ones), which then fails any downstream
#: sanity check for reasons that have nothing to do with the code under test.
#: Constraining it to these values yields finite magnitudes around 0.05-1.0.
_BF16_HIGH = (0x3D, 0x3E, 0x3F, 0xBD, 0xBE, 0xBF)


def _bf16_pattern(size: int, seed: int) -> bytes:
    """Deterministic, non-zero, finite bf16 bytes."""
    out = bytearray(size)
    for i in range(0, size - 1, 2):
        out[i] = (seed + i) & 0xFF                       # mantissa bits
        out[i + 1] = _BF16_HIGH[((seed + i) // 2) % len(_BF16_HIGH)]
    if size % 2:                                         # odd tail, if ever
        out[-1] = seed & 0xFF
    return bytes(out)


def _nbytes(shape: Tuple[int, ...]) -> int:
    count = 1
    for dim in shape:
        count *= dim
    return count * ELEMENT


def write_synthetic(out_dir: str, layers: int = 8, hidden: int = 256,
                    inter: int = 704, vocab: int = 2048, heads: int = 8,
                    kv_heads: int = 2, head_dim: int = 32,
                    shards: int = 3) -> Dict[str, object]:
    os.makedirs(out_dir, exist_ok=True)
    plan = _tensor_plan(layers, hidden, inter, vocab, heads, kv_heads, head_dim)

    # Split across shards by cumulative bytes, mirroring how real checkpoints
    # land: a layer's tensors can straddle a shard boundary.
    total = sum(_nbytes(shape) for _, shape in plan)
    per_shard = total // shards + 1
    assignment: List[List[Tuple[str, Tuple[int, ...]]]] = [[] for _ in range(shards)]
    running, index = 0, 0
    for name, shape in plan:
        if running >= per_shard and index < shards - 1:
            index += 1
            running = 0
        assignment[index].append((name, shape))
        running += _nbytes(shape)

    weight_map: Dict[str, str] = {}
    for index, group in enumerate(assignment):
        shard_name = f"model-{index + 1:05d}-of-{shards:05d}.safetensors"
        header: Dict[str, object] = {}
        offset = 0
        for name, shape in group:
            size = _nbytes(shape)
            header[name] = {"dtype": DTYPE, "shape": list(shape),
                            "data_offsets": [offset, offset + size]}
            offset += size
            weight_map[name] = shard_name
        blob = json.dumps(header).encode("utf-8")
        pad = (8 - (len(blob) % 8)) % 8            # safetensors pads to 8 bytes
        blob += b" " * pad
        with open(os.path.join(out_dir, shard_name), "wb") as handle:
            handle.write(struct.pack("<Q", len(blob)))
            handle.write(blob)
            for position, (name, shape) in enumerate(group):
                handle.write(_bf16_pattern(_nbytes(shape), (position * 37 + 11) & 0xFF))

    with open(os.path.join(out_dir, "model.safetensors.index.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"metadata": {"total_size": total}, "weight_map": weight_map},
                  handle, indent=1, sort_keys=True)

    config = {
        "architectures": ["MistralForCausalLM"], "model_type": "mistral",
        "torch_dtype": "bfloat16", "hidden_size": hidden,
        "intermediate_size": inter, "num_hidden_layers": layers,
        "num_attention_heads": heads, "num_key_value_heads": kv_heads,
        "head_dim": head_dim, "vocab_size": vocab,
        "max_position_embeddings": 4096, "rms_norm_eps": 1e-5,
        "tie_word_embeddings": False,
    }
    with open(os.path.join(out_dir, "config.json"), "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, sort_keys=True)

    return {"path": out_dir, "tensors": len(plan), "shards": shards,
            "totalBytes": total, "layers": layers}


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a tiny Mistral-shaped checkpoint")
    parser.add_argument("out_dir")
    parser.add_argument("--layers", type=int, default=8)
    parser.add_argument("--hidden", type=int, default=256)
    parser.add_argument("--shards", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(write_synthetic(args.out_dir, layers=args.layers,
                                     hidden=args.hidden, shards=args.shards),
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
