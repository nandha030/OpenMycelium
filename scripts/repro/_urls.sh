set -u
OUT=/tmp/om_urls.txt; : > "$OUT"
pick() { # name index regex
  curl -sL --max-time 60 "$2" | sed 's/%2B/+/g' | grep -oE "$3" | sort -u | head -1
}
CU=$(pick cuda https://download.pytorch.org/whl/cu128/torch/ 'torch-2\.11\.0\+cu128-cp312-cp312-manylinux_2_28_x86_64\.whl')
RO=$(pick rocm https://download.pytorch.org/whl/rocm7.0/torch/ 'torch-2\.10\.0\+rocm7\.0-cp312-cp312-manylinux_2_28_x86_64\.whl')
echo "== the pinned wheels exist on their pinned indexes =="
printf '   cu128    %s\n   rocm7.0  %s\n' "${CU:-MISSING}" "${RO:-MISSING}"
echo "https://download.pytorch.org/whl/cu128/$CU"   >> "$OUT"
echo "https://download.pytorch.org/whl/rocm7.0/$RO" >> "$OUT"
curl -sL --max-time 60 https://pypi.org/simple/tokenizers/ | grep -oE 'https://files[^"#]+cp39-abi3-manylinux_2_17_x86_64[^"#]+\.whl' | tail -1 >> "$OUT"
echo
echo "== full sizes (HEAD, follows redirects) =="
while read -r u; do
  read -r code len < <(curl -sIL --max-time 60 -o /dev/null -w '%{http_code} %{size_download}\n' "$u" >/dev/null 2>&1; \
                       curl -sIL --max-time 60 "$u" | awk 'BEGIN{IGNORECASE=1}/^HTTP/{c=$2}/^content-length:/{l=$2}END{gsub(/\r/,"",l);print c, l}')
  printf '   %-9s %8.1f MiB  %s\n' "$code" "$((len))e-6" "$(basename "$u" | cut -c1-46)" 2>/dev/null \
    || printf '   %s %s %s\n' "$code" "$len" "$(basename "$u")"
done < "$OUT"
