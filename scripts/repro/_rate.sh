set -u
href() { curl -sL --max-time 60 "$1" | grep -oE 'href="[^"]+"' | sed 's/href="//;s/"$//' \
         | sed 's/%2B/+/g' | grep -F "$2" | head -1 | sed 's/#.*//'; }
abs() { case "$1" in http*) echo "$1";; /*) echo "https://download.pytorch.org$1";;
                     *) echo "https://download.pytorch.org/whl/$3/${1#../}";; esac; }

CU=$(abs "$(href https://download.pytorch.org/whl/cu128/torch/ 'torch-2.11.0+cu128-cp312-cp312-manylinux_2_28_x86_64.whl')" x cu128)
RO=$(abs "$(href https://download.pytorch.org/whl/rocm7.0/torch/ 'torch-2.10.0+rocm7.0-cp312-cp312-manylinux_2_28_x86_64.whl')" x rocm7.0)
PY=$(curl -sL --max-time 60 https://pypi.org/simple/nvidia-cublas-cu12/ \
     | grep -oE 'https://files[^"#]+manylinux[^"#]*x86_64\.whl' | tail -1)

echo "== timed transfer, 20 s per trial, two trials per source =="
printf '   %-26s %8s %8s %10s\n' source trial1 trial2 median
run() { curl -sL --max-time 20 --retry 0 -o /dev/null -w '%{speed_download} %{http_code}\n' "$1" 2>/dev/null; }
for pair in "PyPI|$PY" "PyTorch cu128|$CU" "PyTorch rocm7.0|$RO"; do
  name=${pair%%|*}; url=${pair#*|}
  [ -z "$url" ] && { printf '   %-26s URL UNRESOLVED\n' "$name"; continue; }
  a=$(run "$url"); b=$(run "$url")
  as=${a%% *}; bs=${b%% *}; ac=${a##* }; bc=${b##* }
  awk -v n="$name" -v a="$as" -v b="$bs" -v ac="$ac" -v bc="$bc" \
    'BEGIN{m=(a+b)/2/1048576; printf "   %-26s %6.2f   %6.2f   %6.2f MB/s   [http %s/%s]\n",
           n, a/1048576, b/1048576, m, ac, bc}'
  echo "$name $(( (${as%.*} + ${bs%.*}) / 2 ))" >> /tmp/om_rates.txt
done
echo
echo "   urls used:"
printf '     %s\n     %s\n     %s\n' "$(basename "$PY")" "$(basename "$CU")" "$(basename "$RO")"
