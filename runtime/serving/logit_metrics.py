"""Comparison metrics for two logit vectors, and BF16 bit-level inspection.

Replaces a single relative-error threshold, which was arbitrary and misleading:
relative error is dominated by small-magnitude logits and says little about
whether two distributions agree.

Also settles tie questions by looking at bits. If two logits really are the same
BF16 value, `argmax` must return the lower index; a different selection means
they were not bit-identical, or the comparison happened at a different
precision. Printing the raw `uint16` patterns distinguishes those cases instead
of leaving it to inference.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence, Tuple


def bf16_bits(tensor: Any, indices: Sequence[int], torch: Any) -> List[Dict[str, Any]]:
    """Token id, value, and raw BF16 bit pattern for each index."""
    flat = tensor.reshape(-1)
    if flat.dtype != torch.bfloat16:
        raise ValueError(f"expected bfloat16, got {flat.dtype}")
    bits = flat.view(torch.uint16)
    out = []
    for index in indices:
        raw = int(bits[index].item())
        out.append({
            "tokenId": int(index),
            "value": float(flat[index].float().item()),
            "bits": f"0x{raw:04x}",
        })
    return out


def bf16_ulp(value: float) -> float:
    """Spacing between adjacent BF16 values near `value`.

    BF16 keeps 7 explicit mantissa bits, so the step is 2**(exponent - 7).
    """
    if value == 0.0 or not math.isfinite(value):
        return 0.0
    exponent = math.floor(math.log2(abs(value)))
    return 2.0 ** (exponent - 7)


def compare_logits(a: Any, b: Any, torch: Any, ks: Sequence[int] = (1, 5, 20)
                   ) -> Dict[str, Any]:
    """Several recorded metrics rather than one pass/fail threshold.

    Both inputs are promoted to FP32 once, here, for the statistics only. The
    tensors under test stay BF16.
    """
    fa = a.reshape(-1).float()
    fb = b.reshape(-1).float()
    if fa.shape != fb.shape:
        raise ValueError(f"shape mismatch: {tuple(fa.shape)} vs {tuple(fb.shape)}")

    diff = fa - fb
    abs_diff = diff.abs()
    max_abs = float(abs_diff.max().item())
    mean_abs = float(abs_diff.mean().item())
    rmse = float(torch.sqrt((diff * diff).mean()).item())
    spread = float((fa.max() - fa.min()).item())
    nrmse = rmse / spread if spread > 0 else float("nan")
    cosine = float(torch.nn.functional.cosine_similarity(
        fa.unsqueeze(0), fb.unsqueeze(0)).item())

    # KL(P_a || P_b) after an FP32 softmax; both are full distributions here.
    log_pa = torch.log_softmax(fa, dim=-1)
    log_pb = torch.log_softmax(fb, dim=-1)
    kl = float((log_pa.exp() * (log_pa - log_pb)).sum().item())

    overlap: Dict[str, Any] = {}
    for k in ks:
        ta = torch.topk(fa, k=k).indices.tolist()
        tb = torch.topk(fb, k=k).indices.tolist()
        shared = len(set(ta) & set(tb))
        overlap[f"top{k}"] = {"shared": shared, "of": k,
                              "fraction": round(shared / k, 4)}

    # Spearman rank correlation over the union of the two top-50 candidate sets:
    # ranking agreement among plausible tokens is what decoding depends on.
    cand = sorted(set(torch.topk(fa, k=50).indices.tolist())
                  | set(torch.topk(fb, k=50).indices.tolist()))
    idx = torch.tensor(cand, dtype=torch.long, device=fa.device)
    rank_a = torch.argsort(torch.argsort(fa[idx], descending=True)).float()
    rank_b = torch.argsort(torch.argsort(fb[idx], descending=True)).float()
    n = float(len(cand))
    d2 = float(((rank_a - rank_b) ** 2).sum().item())
    spearman = 1.0 - (6.0 * d2) / (n * (n * n - 1.0)) if n > 1 else float("nan")

    top_a = torch.topk(fa, k=max(ks)).indices.tolist()
    return {
        "maxAbsError": round(max_abs, 6),
        "meanAbsError": round(mean_abs, 6),
        "rmse": round(rmse, 6),
        "normalizedRmse": round(nrmse, 6),
        "cosineSimilarity": round(cosine, 8),
        "klDivergenceFp32": round(kl, 8),
        "topKOverlap": overlap,
        "spearmanOnCandidates": round(spearman, 6),
        "candidateCount": len(cand),
        "maxAbsErrorInUlpAtTop1": round(
            max_abs / bf16_ulp(float(fa[top_a[0]].item())), 2)
        if bf16_ulp(float(fa[top_a[0]].item())) else None,
    }
