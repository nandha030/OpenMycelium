"""A structurally complete Llama checkpoint that must be refused for one reason.

Deliberately synthetic, and deliberately *valid*. Downloading a real Llama would
be a weaker test: a real checkpoint can be rejected for a missing shard, an
unreadable tensor, or a shape the inspector cannot parse, and every one of those
looks like success from the outside. This fixture has real safetensors, a
contiguous layer range, the prologue and the epilogue, and a config that would
construct -- if it were Mistral. The only thing wrong with it is the
architecture, so `UNSUPPORTED_ARCHITECTURE` is the only correct answer, and
`INVALID_CHECKPOINT` proves the refusal came from the wrong place.

Tiny on purpose. It is never loaded onto a device: the whole point is that the
refusal happens before anything is allocated.

Usage:  make_llama_fixture.py [target]
"""

from __future__ import annotations

import json
import os
import struct
import sys

import torch

CONFIG = {
    "architectures": ["LlamaForCausalLM"],
    "model_type": "llama",
    "vocab_size": 2048,
    "hidden_size": 128,
    "intermediate_size": 256,
    "num_hidden_layers": 4,
    "num_attention_heads": 8,
    "num_key_value_heads": 2,
    "head_dim": 16,
    "max_position_embeddings": 4096,
    "rms_norm_eps": 1e-5,
    "rope_theta": 500000.0,
    "torch_dtype": "bfloat16",
    "tie_word_embeddings": False,
    "hidden_act": "silu",
    "attention_dropout": 0.0,
    "initializer_range": 0.02,
    "bos_token_id": 1,
    "eos_token_id": 2,
    "use_cache": True,
}


def layer_tensors(config):
    hidden = config["hidden_size"]
    heads, kv = config["num_attention_heads"], config["num_key_value_heads"]
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
    """The same writer as make_tiny_checkpoint.py, so the format is identical."""
    header, offset, blobs = {}, 0, []
    for name in sorted(tensors):
        tensor = tensors[name].contiguous()
        blob = tensor.view(torch.uint8).numpy().tobytes()
        header[name] = {"dtype": "BF16", "shape": list(tensor.shape),
                        "data_offsets": [offset, offset + len(blob)]}
        offset += len(blob)
        blobs.append(blob)
    encoded = json.dumps(header).encode("utf-8")
    encoded += b" " * ((-len(encoded)) % 8)
    with open(path, "wb") as handle:
        handle.write(struct.pack("<Q", len(encoded)))
        handle.write(encoded)
        for blob in blobs:
            handle.write(blob)


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "/opt/models/tiny-llama-fixture"
    os.makedirs(target, exist_ok=True)
    torch.manual_seed(11)

    hidden = CONFIG["hidden_size"]

    def make(shape):
        if len(shape) == 1:
            return torch.ones(shape).to(torch.bfloat16)
        return (torch.randn(shape) * (shape[-1] ** -0.5)).to(torch.bfloat16)

    tensors = {"model.embed_tokens.weight": make((CONFIG["vocab_size"], hidden))}
    for index in range(CONFIG["num_hidden_layers"]):
        for suffix, shape in layer_tensors(CONFIG).items():
            tensors[f"model.layers.{index}.{suffix}"] = make(shape)
    tensors["model.norm.weight"] = make((hidden,))
    tensors["lm_head.weight"] = make((CONFIG["vocab_size"], hidden))

    write_safetensors(os.path.join(target, "model.safetensors"), tensors)
    with open(os.path.join(target, "config.json"), "w", encoding="utf-8") as handle:
        json.dump(CONFIG, handle, indent=2)

    total = sum(t.numel() * t.element_size() for t in tensors.values())
    print(f"wrote {len(tensors)} tensors, {total / (1 << 20):.2f} MiB to {target}")
    print(f"architecture {CONFIG['architectures']}, "
          f"layers 0..{CONFIG['num_hidden_layers'] - 1} contiguous, "
          "prologue and epilogue present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
