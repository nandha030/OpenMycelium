set -u
PIP=/opt/omfresh/venv/bin/pip
PYX=/opt/omfresh/venv/bin/python
echo "== resolving the full dependency closure for each environment =="
for V in cuda rocm; do
  case $V in
    cuda) IDX=https://download.pytorch.org/whl/cu128; PKG="torch==2.11.0";;
    rocm) IDX=https://download.pytorch.org/whl/rocm7.0; PKG="torch==2.10.0";;
  esac
  $PIP install --dry-run --ignore-installed --quiet \
      --report /tmp/rep_$V.json --index-url "$IDX" --extra-index-url https://pypi.org/simple \
      $PKG transformers==5.15.1 tokenizers==0.22.2 safetensors numpy >/dev/null 2>&1
  n=$($PYX -c "import json;print(len(json.load(open('/tmp/rep_$V.json'))['install']))" 2>/dev/null)
  printf '   %-5s %s packages resolved\n' "$V" "${n:-FAILED}"
done
echo
echo "== true byte size of every wheel (Content-Range on a 1-byte request) =="
$PYX - <<'PY' > /tmp/urls_all.txt
import json
for v in ("cuda","rocm"):
    try: rep=json.load(open(f"/tmp/rep_{v}.json"))
    except Exception: continue
    for it in rep["install"]:
        u=it.get("download_info",{}).get("url","")
        if u.startswith("http"): print(v, u)
PY
awk '{print}' /tmp/urls_all.txt | while read -r v u; do
  sz=$(curl -sL --max-time 40 -r 0-0 -D - -o /dev/null "$u" 2>/dev/null \
       | tr -d '\r' | awk 'BEGIN{IGNORECASE=1}/^content-range:/{split($2,a,"/");print a[2]}' | tail -1)
  echo "$v ${sz:-0}"
done > /tmp/sizes.txt
awk '{s[$1]+=$2; n[$1]++} END {
  t=0; for (v in s) { printf "   %-5s %3d wheels  %8.2f GiB\n", v, n[v], s[v]/1073741824; t+=s[v] }
  printf "   %-5s %3s          %8.2f GiB  <- downloaded twice, once per environment\n","total","",t/1073741824
  printf "%d\n", t > "/tmp/total_bytes"
}' /tmp/sizes.txt
echo
echo "== projection at the measured rate =="
T=$(cat /tmp/total_bytes)
awk -v t="$T" 'BEGIN{
  for (r=1.48; r<=1.66; r+=0.17) {
    m=t/(r*1000000)/60
    printf "   %.2f MB/s  ->  %5.1f min transfer\n", r, m
  }
}'
