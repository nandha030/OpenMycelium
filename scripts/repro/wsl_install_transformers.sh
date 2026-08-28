#!/usr/bin/env bash
# Transformers in both venvs.
#
# `--no-deps` keeps pip from replacing the carefully-built torch in each venv
# (CUDA cu128 in one, ROCm in the other), so the runtime dependencies are then
# installed explicitly. `tokenizers` must land inside the window transformers
# declares, or its own import-time version check refuses to load. Note that
# tokenizers 0.23.0 was never released, so the upper bound transformers 5.15.1
# states is unreachable; 0.22.2 is the newest real version inside the window.
set -uo pipefail
for V in /opt/hetenv /opt/rocmenv; do
  echo "=== $V ==="
  "$V/bin/pip" install -q --no-deps transformers 2>&1 | tail -2
  "$V/bin/pip" install -q regex requests tqdm filelock packaging pyyaml typer \
      "huggingface-hub>=0.23" safetensors 2>&1 | tail -2
  "$V/bin/pip" install -q --force-reinstall --no-deps "tokenizers==0.22.2" 2>&1 | tail -2
  "$V/bin/python" - <<'PY' 2>&1 | grep -vE 'NumPy|conversion_method|Warning: Resource'
import torch, transformers, tokenizers
print("  torch       ", torch.__version__, "(hip)" if torch.version.hip else "(cuda)")
print("  transformers", transformers.__version__, " tokenizers", tokenizers.__version__)
from transformers.models.mistral.modeling_mistral import MistralDecoderLayer
from transformers import MistralConfig
print("  MistralDecoderLayer importable: yes")
PY
done
