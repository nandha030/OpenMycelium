"""A tiny real checkpoint: safetensors, config, and the real tokenizer.

The driver's socket protocol, control messages, cache reports and two-pass
prefill sweep can all be wrong in ways that only a full run reveals -- and a
full run costs twenty minutes of weight loading before it can fail. This writes
an eight-layer model in the same on-disk format so the whole driver can be
exercised on the CPU in seconds.

The tokenizer files are copied from the real checkpoint, so tokenisation and the
chat template are the genuine ones. Only the weights are random.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import sys

import torch

CONFIG = {
    "architectures": ["MistralForCausalLM"],
    "model_type": "mistral",
    "vocab_size": 131072,
    "hidden_size": 128,
    "intermediate_size": 256,
    "num_hidden_layers": 8,
    "num_attention_heads": 8,
    "num_key_value_heads": 2,
    "head_dim": 16,
    "max_position_embeddings": 4096,
    "rms_norm_eps": 1e-5,
    "rope_theta": 1000000.0,
    "torch_dtype": "bfloat16",
    "tie_word_embeddings": False,
}

TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json",
                   "special_tokens_map.json", "generation_config.json")


def layer_tensors(config):
    hidden, heads, kv = config["hidden_size"], config["num_attention_heads"], \
        config["num_key_value_heads"]
    head_dim, inter = config["head_dim"], config["intermediate_size"]
    return {
        "self_attn.q_proj.weight": (heads * head_dim, hidden),
        "self_attn.k_proj.weight": (kv * head_dim, hidden),
        "self_attn.v_proj.weight": (kv * head_dim, hidden),
        "self_attn.o_proj.weight": (hidden, heads * head_dim),
        "mlp.gate_proj.weight": (inter, hidden),
        "mlp.up_proj.weight": (inter, hidden),
        "mlp.down_proj.weight": (hidden, inter),
        "input_layernorm.weight": (hidden,),
        "post_attention_layernorm.weight": (hidden,),
    }


def write_safetensors(path, tensors):
    header, offset, blobs = {}, 0, []
    for name in sorted(tensors):
        tensor = tensors[name].contiguous()
        blob = tensor.view(torch.uint8).numpy().tobytes()
        header[name] = {"dtype": "BF16", "shape": list(tensor.shape),
                        "data_offsets": [offset, offset + len(blob)]}
        offset += len(blob)
        blobs.append(blob)
    encoded = json.dumps(header).encode("utf-8")
    padding = (-len(encoded)) % 8
    encoded += b" " * padding
    with open(path, "wb") as handle:
        handle.write(struct.pack("<Q", len(encoded)))
        handle.write(encoded)
        for blob in blobs:
            handle.write(blob)


def main() -> int:
    source = sys.argv[1] if len(sys.argv) > 1 \
        else "/opt/models/Mistral-Nemo-Instruct-2407"
    target = sys.argv[2] if len(sys.argv) > 2 else "/opt/models/tiny-mistral"
    os.makedirs(target, exist_ok=True)
    torch.manual_seed(7)

    tensors = {}
    hidden = CONFIG["hidden_size"]

    def make(shape):
        if len(shape) == 1:
            return torch.ones(shape).to(torch.bfloat16)
        return (torch.randn(shape) * (shape[-1] ** -0.5)).to(torch.bfloat16)

    tensors["model.embed_tokens.weight"] = make((CONFIG["vocab_size"], hidden))
    for index in range(CONFIG["num_hidden_layers"]):
        for suffix, shape in layer_tensors(CONFIG).items():
            tensors[f"model.layers.{index}.{suffix}"] = make(shape)
    tensors["model.norm.weight"] = make((hidden,))
    tensors["lm_head.weight"] = make((CONFIG["vocab_size"], hidden))

    write_safetensors(os.path.join(target, "model.safetensors"), tensors)
    with open(os.path.join(target, "config.json"), "w", encoding="utf-8") as handle:
        json.dump(CONFIG, handle, indent=2)
    copied = []
    for name in TOKENIZER_FILES:
        origin = os.path.join(source, name)
        if os.path.isfile(origin):
            shutil.copy2(origin, os.path.join(target, name))
            copied.append(name)

    total = sum(t.numel() * t.element_size() for t in tensors.values())
    print(f"wrote {len(tensors)} tensors, {total / (1 << 20):.1f} MiB to {target}")
    print(f"copied tokenizer files: {copied}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
