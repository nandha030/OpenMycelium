"""The KV-cache oracle on a tiny random model, both stages in one process.

The real run costs a quarter of an hour of weight loading before it can fail.
This exercises the same `MistralStage` code -- global layer indices, position
ids, mask growth, per-stage caches -- on a four-layer random model in seconds,
so an off-by-one in the driver is found before the GPUs are involved.

It is not evidence about the bridge. It is evidence about the split logic the
bridge carries, which is the part most likely to be wrong.

A passing oracle is only worth as much as its sensitivity, so two faults can be
injected deliberately:

    python kv_oracle_synthetic.py position-off-by-one
    python kv_oracle_synthetic.py local-layer-index

Both must make it fail. The second reproduces the specific mistake this design
guards against: a stage that renumbers its layers 0-1 instead of keeping their
global identity, so its cache slots collide with the other stage's.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "runtime", "serving"))

import torch  # noqa: E402

from greedy import greedy_token  # noqa: E402
from stage_model import BoundaryMeta, MistralStage, StageSpec  # noqa: E402

CONFIG = dict(vocab_size=256, hidden_size=64, intermediate_size=128,
              num_hidden_layers=4, num_attention_heads=4, num_key_value_heads=2,
              head_dim=16, torch_dtype="bfloat16", max_position_embeddings=512,
              rms_norm_eps=1e-5)


def build(path: str, spec: StageSpec, seed: int) -> MistralStage:
    """A stage with deterministic random weights.

    The seed is explicit so a renumbered stage can be given byte-identical
    weights: the module structure and parameter order do not depend on
    `layer_idx`, only the cache slot does, which is exactly the variable the
    fault injection needs to isolate.
    """
    stage = MistralStage(path, spec, torch)
    torch.manual_seed(seed)
    weights = {}
    for name, param in stage.named_meta_parameters():
        shape = tuple(param.shape)
        if name.endswith("norm.weight") or name.endswith("layernorm.weight"):
            # RMSNorm gains multiply; random ones near zero would erase the
            # signal the rest of the layer produces.
            tensor = torch.ones(shape)
        else:
            # 1/sqrt(fan_in), the usual scale. An earlier version used a flat
            # 0.02 and the model was numerically degenerate: attention moved the
            # residual stream by less than one BF16 step, so the test could not
            # see a wrong RoPE position at all.
            fan_in = shape[-1] if len(shape) > 1 else shape[0]
            tensor = torch.randn(shape) * (fan_in ** -0.5)
        weights[name] = tensor.to(torch.bfloat16)
    stage.install(weights)
    return stage


def run(first: MistralStage, second: MistralStage, ids, positions, past,
        cache_a=None, cache_b=None):
    """One end-to-end pass; only the hidden state moves between the stages."""
    hidden_size = CONFIG["hidden_size"]
    positions = list(positions)
    meta = BoundaryMeta(1, len(ids), hidden_size, "bf16", positions, positions,
                        use_cache=cache_a is not None, past_length=past)
    tensor_ids = torch.tensor([ids], dtype=torch.long)
    with torch.inference_mode():
        hidden = first.forward(tensor_ids, meta, past_key_values=cache_a)
        # The boundary: a contiguous copy, exactly what the wire would carry.
        crossed = hidden.detach().contiguous().clone()
        logits = second.forward(crossed, meta, past_key_values=cache_b,
                                last_position_only=True)
    return logits.reshape(-1), crossed


def main() -> int:
    fault = sys.argv[1] if len(sys.argv) > 1 else ""
    directory = tempfile.mkdtemp()
    path = os.path.join(directory, "config.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(CONFIG, handle)

    first = build(path, StageSpec("cuda", (0, 1), True, False, "cpu"), seed=11)
    if fault == "local-layer-index":
        second = build(path, StageSpec("rocm", (0, 1), False, True, "cpu"), seed=22)
        share_cache = True
        print("fault injected: the second stage renumbered its layers to 0-1 and "
              "now shares cache slots with the first")
    else:
        second = build(path, StageSpec("rocm", (2, 3), False, True, "cpu"), seed=22)
        share_cache = False
    if fault == "position-off-by-one":
        print("fault injected: cached decode is given position - 1")

    prompt = [3, 17, 42, 88, 5, 61, 200, 9]
    cache_a = first.new_cache()
    cache_b = cache_a if share_cache else second.new_cache()
    try:
        cached_logits, boundary = run(first, second, prompt, range(len(prompt)), 0,
                                      cache_a, cache_b)
    except RuntimeError as error:
        # Colliding cache slots surface as a shape mismatch, not a wrong number.
        # That is the fault being caught, so say so rather than leaving a
        # traceback to be read as a broken test.
        print(f"RESULT: FAIL - prefill raised: {str(error).splitlines()[-1][:110]}")
        return 1
    print(f"prefill boundary {list(boundary.shape)} {boundary.dtype}  "
          f"logits {list(cached_logits.shape)}")
    print(f"stage caches: first {first.cache_report(cache_a)['populatedLayers']} "
          f"second {second.cache_report(cache_b)['populatedLayers']}")

    prefix = list(prompt)
    token = greedy_token(cached_logits, torch)
    failures = []
    for step in range(1, 17):
        position = len(prefix)
        used = position - 1 if fault == "position-off-by-one" else position
        try:
            cached, _ = run(first, second, [token], [used], position,
                            cache_a, cache_b)
            # No cache anywhere, whole prefix recomputed: the only thing that
            # can differ is what caching did.
            fresh, _ = run(first, second, prefix + [token],
                           range(len(prefix) + 1), 0, None, None)
        except RuntimeError as error:
            # A colliding cache slot shows up as a shape mismatch rather than a
            # wrong number. That is still the fault being detected, so report it
            # as one instead of letting a traceback stand in for a result.
            failures.append(step)
            print(f"  step {step:>2}  raised: {str(error).splitlines()[-1][:90]}")
            break
        same_bits = bool((cached.view(torch.uint16) == fresh.view(torch.uint16))
                         .all().item())
        cached_token = greedy_token(cached, torch)
        fresh_token = greedy_token(fresh, torch)
        max_error = float((cached.float() - fresh.float()).abs().max().item())
        report_a = first.cache_report(cache_a)
        report_b = second.cache_report(cache_b)
        expected_layers = [0, 1] if share_cache else [2, 3]
        # On one CPU with one reduction order, caching should change nothing at
        # all, so this demands bit-identity rather than agreement on the chosen
        # token. Token agreement is far too weak here: with the position fault
        # injected the logits were already wrong by 0.9 while the same token
        # still won for five steps.
        ok = (same_bits
              and cached_token == fresh_token
              and report_a["uniformLength"] == position + 1
              and report_b["uniformLength"] == position + 1
              and report_a["populatedLayers"] == [0, 1]
              and report_b["populatedLayers"] == expected_layers)
        if not ok:
            failures.append(step)
        print(f"  step {step:>2}  cacheLen {report_a['uniformLength']:>3}/"
              f"{report_b['uniformLength']:>3}  token {cached_token:>4} vs "
              f"{fresh_token:>4}  maxAbsErr {max_error:.6f}  "
              f"bitIdentical {same_bits}  {'ok' if ok else 'FAILED'}")
        prefix.append(token)
        token = cached_token

    print()
    if failures:
        print(f"RESULT: FAIL - steps {failures} disagreed")
        return 1
    print("RESULT: PASS - cached decode matches full recomputation for 16 steps, "
          "caches stay on their owning stage and grow in lockstep")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
