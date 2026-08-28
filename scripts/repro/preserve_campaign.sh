#!/usr/bin/env bash
# Preserve the throughput campaign and wheelhouse evidence for 0.1.0a8.
set -uo pipefail
D=/mnt/c/Users/User/Documents/Open_Mycelium/release/0.1.0a8/throughput
mkdir -p "$D"
for f in throughput-report.json invariants-before.json invariants-after.json \
         boundary-before-cuda.json boundary-before-rocm.json \
         boundary-after-cuda.json boundary-after-rocm.json \
         wh-cuda-verify.txt wh-cuda-install.log wh-cuda.txt wh-rocm.txt; do
  [ -f "/var/log/om-a8/$f" ] && cp "/var/log/om-a8/$f" "$D/"
done
echo "  preserved to $D"
ls -1 "$D" | sed 's/^/    /'
echo
echo "  == headline, from the frozen report =="
/opt/om/venv/bin/python - "$D/throughput-report.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
s = r["summary"]
i = r["identity"]
print(f"    model            {i['model']}")
print(f"    modelSha256      {i['modelSha256']}")
print(f"    scheme           {i.get('modelFingerprintScheme')}")
print(f"    files / bytes    {i['modelFiles']} / {i['modelBytes']}")
print(f"    tokenizer        {i['tokenizerIdentity'][:32]}  "
      f"fix_mistral_regex={i['fixMistralRegex']}")
print(f"    promptIdsSha256  {i['promptIdsSha256'][:32]}  "
      f"({i['promptTokenCount']} tokens)")
print(f"    generated        {r['generatedLengths']}  eos={r['endedByEos']}")
for label, key, unit in (("TTFT", "ttftMs", "ms"),
                         ("decode", "decodeTokS", "tok/s"),
                         ("end-to-end", "endToEndTokS", "tok/s")):
    d = s[key]
    print(f"    {label + ' (' + unit + ')':<17} median {d['median']:.2f}   "
          f"range {d['min']:.2f} - {d['max']:.2f}")
PY
