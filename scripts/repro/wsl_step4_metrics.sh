#!/usr/bin/env bash
# Step 4, recorded as metrics rather than one arbitrary threshold.
set -uo pipefail
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/cudaroot/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
S=/mnt/c/Users/User/Documents/Open_Mycelium
MODEL=${MODEL:-/opt/models/Mistral-Nemo-Instruct-2407}
export PYTHONPATH="$S/runtime/serving:$S/runtime/mccl/src"
export OM_XVENDOR_LEDGER=/opt/xvendor_qualification.json
TOKEN=${TOKEN:-1234}; PORT=${PORT:-31800}
READY=/opt/m_ready.$PORT
rm -f "$READY" /opt/m_ref.bin /opt/m_split.bin

echo "=== streaming CPU reference ==="
CUDA_VISIBLE_DEVICES="" timeout 600 /opt/hetenv/bin/python \
    "$S/runtime/serving/reference_stream.py" --model "$MODEL" --token $TOKEN \
    --save-logits /opt/m_ref.bin >/opt/m_ref.json 2>/dev/null
echo "  saved $(stat -c%s /opt/m_ref.bin 2>/dev/null || echo 0) bytes"

echo "=== split pipeline ==="
timeout 900 /opt/rocmenv/bin/python "$S/runtime/serving/forward_pass.py" \
    --model "$MODEL" --role rocm --token $TOKEN --port $PORT \
    --ready-file "$READY" --accept-timeout 300 --save-logits /opt/m_split.bin \
    >/opt/m_rocm.json 2>/dev/null &
R=$!
D=$((SECONDS + 900))
while [ ! -f "$READY" ]; do
  kill -0 $R 2>/dev/null || { echo "  receiver died"; exit 1; }
  [ $SECONDS -gt $D ] && { echo "  receiver never ready"; kill $R; exit 1; }
  sleep 2
done
timeout 900 /opt/hetenv/bin/python "$S/runtime/serving/forward_pass.py" \
    --model "$MODEL" --role cuda --token $TOKEN --port $PORT --peer 127.0.0.1 \
    --ready-file "$READY" --accept-timeout 300 >/opt/m_cuda.json 2>/dev/null
wait $R 2>/dev/null
rm -f "$READY"
echo "  saved $(stat -c%s /opt/m_split.bin 2>/dev/null || echo 0) bytes"

echo
/opt/hetenv/bin/python - <<'PY' 2>&1 | grep -vE 'NumPy|conversion_method'
import json, sys, torch
sys.path.insert(0, "/mnt/c/Users/User/Documents/Open_Mycelium/runtime/serving")
from logit_metrics import bf16_bits, bf16_ulp, compare_logits

def load_bin(p):
    with open(p, "rb") as h:
        return torch.frombuffer(bytearray(h.read()), dtype=torch.uint8).view(torch.bfloat16)
def last(p):
    return json.loads(open(p).read().strip().splitlines()[-1])

ref_t, split_t = load_bin("/opt/m_ref.bin"), load_bin("/opt/m_split.bin")
ref_j, rocm_j = last("/opt/m_ref.json"), last("/opt/m_rocm.json")
m = compare_logits(ref_t, split_t, torch)

print("Reference (streaming CPU) vs split (CUDA 0-19 -> ROCm 20-39)")
print(f"  elements compared        {ref_t.numel()}")
print(f"  max absolute error       {m['maxAbsError']}")
print(f"  mean absolute error      {m['meanAbsError']}")
print(f"  RMSE                     {m['rmse']}")
print(f"  normalized RMSE          {m['normalizedRmse']}")
print(f"  cosine similarity        {m['cosineSimilarity']}")
print(f"  KL divergence (fp32)     {m['klDivergenceFp32']}")
for k in ("top1", "top5", "top20"):
    o = m["topKOverlap"][k]
    print(f"  {k} overlap{'':<14} {o['shared']}/{o['of']}  ({o['fraction']*100:.0f}%)")
print(f"  Spearman on {m['candidateCount']} candidates  {m['spearmanOnCandidates']}")
print(f"  max error in BF16 ULP    {m['maxAbsErrorInUlpAtTop1']} ULP at the top logit")

print()
print("Top-2 bit patterns (settles whether the top logits are genuinely tied)")
for label, tensor in (("reference", ref_t), ("split", split_t)):
    top = torch.topk(tensor.float(), k=3).indices.tolist()
    for entry in bf16_bits(tensor, top, torch):
        print(f"  {label:<10} token {entry['tokenId']:>7}  "
              f"value {entry['value']:>9.5f}  bits {entry['bits']}")
    print()

for label, tensor in (("reference", ref_t), ("split", split_t)):
    flat = tensor.reshape(-1)
    top = torch.topk(flat.float(), k=2).indices.tolist()
    bits = flat.view(torch.uint16)
    same = int(bits[top[0]].item()) == int(bits[top[1]].item())
    order = "lower index first" if top[0] < top[1] else "HIGHER index first"
    print(f"  {label}: top-2 bit-identical = {same}; argmax chose {order} "
          f"(ids {top})")
    if same and top[0] > top[1]:
        print("     ^ bit-identical values but the higher index won: argmax is "
              "not selecting the first maximum here.")
ulp = bf16_ulp(float(torch.topk(ref_t.float(), k=1).values.item()))
print()
print(f"  BF16 spacing near the top logit: {ulp}")
PY
