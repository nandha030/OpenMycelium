"""Render the two stage reports into one readable result.

The stages write JSON because they are two processes on two vendors; nobody
should have to read that to find out whether prefill worked.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict

#: BF16 spacing near a top logit of magnitude ~5.4: 2**(2 - 7).
BF16_ULP_AT_TOP = 0.03125


def last_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.loads(handle.read().strip().splitlines()[-1])
    except Exception as error:                                # noqa: BLE001
        return {"error": f"{path}: {error}"}


def _crossing_table(rows, key="sequenceLength") -> None:
    """The interval the user asked for, end to end, on one shared clock."""
    print("  boundary crossing: producer kernels done -> D2H -> last payload byte")
    print("                     -> H2D done -> consumer ready")
    print("   seq      bytes |    D2H  framing     wire      H2D |  total  "
          "wire MB/s  end-to-end MB/s  ordering")
    for row in rows:
        crossing = row.get("crossing") or {}
        if not crossing:
            continue
        print(f"  {row[key]:>4} {crossing['bytes']:>10} | "
              f"{crossing['deviceToHostMs']:>6.2f} {crossing['framingMs']:>8.2f} "
              f"{crossing['wireMs']:>8.2f} {crossing['hostToDeviceMs']:>8.2f} | "
              f"{crossing['producerDoneToConsumerReadyMs']:>6.2f} "
              f"{str(crossing['wireMBps']):>10} "
              f"{str(crossing['endToEndMBps']):>16}  "
              f"{crossing['clockOrderingHolds']}")


def show_prefill(cuda: Dict[str, Any], rocm: Dict[str, Any]) -> bool:
    rows = cuda.get("prefill", [])
    if not rows:
        print("  no prefill rows recorded")
        return False
    print(f"  boundary after layer {cuda.get('boundary')}: CUDA "
          f"{cuda.get('layers', [])[:1]}..{cuda.get('layers', [])[-1:]}  "
          f"ROCm {rocm.get('layers', [])[:1]}..{rocm.get('layers', [])[-1:]}")
    print(f"  weights resident: CUDA {cuda.get('weightsMiB')} MiB, "
          f"ROCm {rocm.get('weightsMiB')} MiB")
    print()
    print("   seq  boundary shape        bytes  mask shape            positions  "
          "byte-exact  finite  token")
    ok = True
    for row in rows:
        mask = row["maskShape"] or "none (single query)"
        logits = row["logits"]
        ok = ok and bool(row["byteExact"]) and bool(logits["allFinite"])
        print(f"  {row['sequenceLength']:>4}  {str(row['boundaryShape']):<20} "
              f"{row['boundaryBytes']:>9}  {str(mask):<21} "
              f"{str(row['positionSpan']):<10} {str(row['byteExact']):<11} "
              f"{str(logits['allFinite']):<7} {logits['greedyToken']}")
    print()
    print("   seq   TTFT ms | CUDA compute  ROCm compute | workspace CUDA/ROCm MiB")
    for row in rows:
        decomposition, memory = row["decomposition"], row["memory"]
        print(f"  {row['sequenceLength']:>4}  {row['ttftMs']:>8.1f} | "
              f"{decomposition['cudaComputeMs']:>12.2f} "
              f"{decomposition['rocmComputeMs']:>13.2f} | "
              f"{memory['cudaWorkspaceMiB']:>10.1f} / "
              f"{memory['rocmWorkspaceMiB']:.1f}")
    print()
    _crossing_table(rows)
    shares = [(row["sequenceLength"],
               100.0 * (row.get("crossing") or {}).get(
                   "producerDoneToConsumerReadyMs", 0.0) / max(row["ttftMs"], 1e-9))
              for row in rows if row.get("crossing")]
    if shares:
        print("  crossing as a share of TTFT: "
              + ", ".join(f"seq {n}: {p:.1f}%" for n, p in shares))
    print()
    first = rows[0]["tokenizer"]
    print(f"  tokenizer: chat template {first['usedChatTemplate']}, "
          f"templated prompt is {first['baseTemplatedLength']} tokens")
    print(f"  template begins: {first['templatePreview'][:80]!r}")
    print(f"  all {rocm.get('transfers')} transfers byte-exact: "
          f"{rocm.get('allTransfersByteExact')}")
    ordering = all((row.get("crossing") or {}).get("clockOrderingHolds", False)
                   for row in rows)
    if not ordering:
        print("  WARNING: cross-process clock ordering failed; timings are void")
    return ok and bool(rocm.get("allTransfersByteExact")) and ordering


def _band(metrics, key):
    values = [m.get(key) for m in metrics if m.get(key) is not None]
    if not values:
        return 0.0, 0.0, 0.0
    return min(values), max(values), sum(values) / len(values)


def _envelope(steps) -> bool:
    """The bounds the run actually stayed inside, not just the token count.

    Greedy agreement alone would hide a distribution drifting underneath a
    stable argmax, so the agreement figure is reported alongside the bounds
    rather than in place of them.
    """
    if not steps:
        return False
    metrics = [row.get("metrics") or {} for row in steps]
    overlaps = [row.get("topKOverlap") or {} for row in steps]

    print(f"  numerical envelope over all {len(steps)} steps")
    print(f"    {'':<32} {'min':>12} {'max':>12} {'mean':>12}")
    for label, key in (("max absolute logit error", "maxAbsError"),
                       ("mean absolute logit error", "meanAbsError"),
                       ("normalized RMSE", "normalizedRmse"),
                       ("cosine similarity", "cosineSimilarity"),
                       ("KL divergence (fp32 softmax)", "klDivergenceFp32"),
                       ("Spearman on candidates", "spearmanOnCandidates")):
        low, high, mean = _band(metrics, key)
        print(f"    {label:<32} {low:>12.8f} {high:>12.8f} {mean:>12.8f}")

    for key, name in (("top1", "top-1"), ("top5", "top-5"), ("top20", "top-20")):
        values = [o.get(key, 0.0) for o in overlaps]
        exact = sum(1 for v in values if v >= 1.0)
        print(f"    {name + ' overlap':<32} min {min(values):.3f}      "
              f"{exact}/{len(values)} steps at 1.000")
    agreed = sum(1 for row in steps if row.get("tokensAgree"))
    print(f"    {'greedy-token agreement':<32} {agreed}/{len(steps)}")

    low, high, _ = _band(metrics, "maxAbsError")
    print(f"    max error in BF16 ULP at the top logit: {low / BF16_ULP_AT_TOP:.1f}"
          f" .. {high / BF16_ULP_AT_TOP:.1f} ULP (spacing {BF16_ULP_AT_TOP})")

    crossings = [row["crossing"] for row in steps if row.get("crossing")]
    ordering = all(c["clockOrderingHolds"] for c in crossings) if crossings else False
    if crossings:
        totals = [c["producerDoneToConsumerReadyMs"] for c in crossings]
        wires = [c["wireMs"] for c in crossings]
        print(f"    boundary crossing per decode step: {min(totals):.2f}"
              f"..{max(totals):.2f} ms (wire {min(wires):.2f}..{max(wires):.2f} ms)"
              f" for {crossings[0]['bytes']} bytes")
        print(f"    cross-process clock ordering holds every step: {ordering}")
    return agreed == len(steps) and ordering


def show_oracle(cuda: Dict[str, Any], rocm: Dict[str, Any]) -> bool:
    oracle = cuda.get("oracle")
    if not oracle:
        print("  no oracle result recorded")
        return False
    steps = oracle["steps"]
    print(f"  prompt {oracle['promptLength']} tokens, {len(steps)} decode steps")
    print(f"  CUDA cache after prefill: layers "
          f"{_span(oracle['senderCacheAfterPrefill']['populatedLayers'])} "
          f"length {oracle['senderCacheAfterPrefill']['uniformLength']}")
    final = rocm.get("finalCache", {})
    print(f"  ROCm cache at end:        layers "
          f"{_span(final.get('populatedLayers', []))} "
          f"length {final.get('uniformLength')}  "
          f"({round(final.get('bytes', 0) / (1 << 20), 1)} MiB)")
    print()
    print("  step  prefix  cached  recomp  agree  bits   maxAbs   meanAbs  "
          "nRMSE      cosine      KL        top1/5/20        cacheLen")
    ok = True
    for row in steps:
        metrics = row.get("metrics") or {}
        overlap = row.get("topKOverlap") or {}
        checks = row.get("cacheChecks") or {}
        agree = row.get("tokensAgree")
        good = bool(agree) and all(
            checks.get(key) for key in ("receiverHoldsExactlyItsLayers",
                                        "senderHoldsNoneOfThem", "lengthsMatch"))
        ok = ok and good
        tops = "/".join(f"{overlap.get(k, 0.0):.2f}"
                        for k in ("top1", "top5", "top20"))
        print(f"  {row['step']:>4}  {row['prefixLength']:>6}  "
              f"{row['cachedToken']:>6}  {row['recomputedToken']:>6}  "
              f"{str(agree):<5}  {str(row.get('bitIdentical'))[:5]:<5} "
              f"{metrics.get('maxAbsError', 0):>7.4f} "
              f"{metrics.get('meanAbsError', 0):>8.5f}  "
              f"{metrics.get('normalizedRmse', 0):>8.6f}  "
              f"{metrics.get('cosineSimilarity', 0):>10.8f}  "
              f"{metrics.get('klDivergenceFp32', 0):>8.6f}  {tops}  "
              f"{str(checks.get('receiverLength')):>8}")
    print()
    envelope_ok = _envelope(steps)
    if steps and all(row.get("bitIdentical") for row in steps):
        print("  every step is bit-identical; the cosine figure sits just under 1 "
              "because the statistic itself accumulates in FP32")
    print()
    print(f"  generated tokens: {oracle['generatedTokens']}")
    print(f"  decoded text: {oracle['generatedText']!r}")
    warm = [row for row in steps[1:]]
    if warm:
        cached = _mean(r["cachedDecodeMs"] for r in warm)
        recompute = _mean(r["recomputeMs"] for r in warm)
        print(f"  warm steps: cached decode {cached:.1f} ms vs full recompute "
              f"{recompute:.1f} ms ({recompute / max(cached, 1e-9):.2f}x at a "
              f"{steps[-1]['prefixLength']}-token prefix)")
    print(f"  all {rocm.get('transfers')} transfers byte-exact: "
          f"{rocm.get('allTransfersByteExact')}")
    return ok and envelope_ok and bool(rocm.get("allTransfersByteExact"))


def _span(values) -> str:
    if not values:
        return "[]"
    return f"{values[0]}-{values[-1]} ({len(values)})"


def _mean(values) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def main() -> int:
    cuda_path, rocm_path, mode, crc, rrc = sys.argv[1:6]
    cuda, rocm = last_json(cuda_path), last_json(rocm_path)
    for label, payload in (("cuda", cuda), ("rocm", rocm)):
        if "error" in payload:
            print(f"  {label}: {payload['error']}")
            return 1

    ok = show_prefill(cuda, rocm) if mode == "prefill" else show_oracle(cuda, rocm)
    ok = ok and crc == "0" and rrc == "0"
    print()
    if mode == "prefill":
        print("RESULT:", "PASS - prefill runs across both vendors at every length, "
              "byte-exact at the boundary, with finite logits"
              if ok else "FAIL - see the rows above")
    else:
        print("RESULT:", "PASS - cached decode matches full recomputation at every "
              "step, caches stay on their owning GPU"
              if ok else "FAIL - see the rows above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
