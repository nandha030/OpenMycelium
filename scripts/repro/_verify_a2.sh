set -u
export PATH="/opt/omfresh/venv/bin:$PATH"
unset PYTHONPATH
cd /opt/omfresh/work
echo "  command -v -> $(command -v openmycelium)"
openmycelium version | head -3
python - <<'PY'
import hashlib, os
w="/mnt/c/Users/User/Documents/Open_Mycelium/dist/openmycelium-0.1.0a2-py3-none-any.whl"
print("  wheel sha256", hashlib.sha256(open(w,"rb").read()).hexdigest())
PY
sha256sum -c /mnt/c/Users/User/Documents/Open_Mycelium/dist/SHA256SUMS 2>&1 | sed 's/^/  /'
