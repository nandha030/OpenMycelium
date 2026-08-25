#!/usr/bin/env bash
set -uo pipefail
VENV=/opt/rocmenv
[ -d "$VENV" ] || { python3 -m venv "$VENV"; "$VENV/bin/pip" install -q --upgrade pip; }
echo "=== trying ROCm torch wheel indexes (newest first) ==="
for IDX in rocm7.0 rocm6.4 rocm6.3; do
  echo "--- https://download.pytorch.org/whl/$IDX ---"
  if "$VENV/bin/pip" install -q torch --index-url "https://download.pytorch.org/whl/$IDX" 2>&1 | tail -3; then
    if "$VENV/bin/python" -c 'import torch' 2>/dev/null; then
      echo "installed from $IDX"; break
    fi
  fi
done
"$VENV/bin/python" - <<'PY' 2>&1 | tail -8
try:
    import torch
    print("torch:", torch.__version__)
    print("hip:", getattr(torch.version, "hip", None))
    print("cuda available (maps to ROCm on a ROCm build):", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("device:", torch.cuda.get_device_name(0))
except Exception as e:
    print("BROKEN:", type(e).__name__, e)
PY
