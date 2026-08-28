#!/usr/bin/env bash
# What do the two runtimes actually call these cards?
#
# The post-provision gate matches device names against expected substrings. If
# ROCm reports a gfx identifier rather than a marketing name, the gate would
# fail on a perfectly good environment, so the expected strings are checked
# against the known-working environments first.
for pair in "cuda /opt/hetenv" "rocm /opt/rocmenv"; do
  vendor=${pair%% *}; env=${pair#* }
  [ -x "$env/bin/python" ] || continue
  "$env/bin/python" -c "
import torch
name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else '(unavailable)'
print(f'  $vendor  {name!r}')
print(f'         build vendor: {\"rocm\" if getattr(torch.version, \"hip\", None) else \"cuda\"}')
" 2>/dev/null
done
