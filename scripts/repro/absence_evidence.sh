#!/usr/bin/env bash
# Direct evidence that the environment directories did not exist before
# provisioning created them.
#
# The dry run already recorded "would create" for both, which provision only
# emits when bin/python is absent. This adds a direct filesystem observation:
# provisioning does CUDA first, so while CUDA is still downloading the ROCm
# directory has not been touched yet.
OUT=/var/log/om-bootstrap/absence.txt
{
  echo "observed at $(date -Is)"
  echo
  echo "state directory:"
  ls -la /var/lib/openmycelium/state/env 2>/dev/null || echo "  env/ does not exist"
  echo
  for v in cuda rocm; do
    D=/var/lib/openmycelium/state/env/$v
    if [ -x "$D/bin/python" ]; then
      echo "$v: interpreter present at $D/bin/python"
      echo "    created $(stat -c %w "$D" 2>/dev/null || stat -c %y "$D")"
    elif [ -d "$D" ]; then
      echo "$v: directory exists, NO interpreter yet ($D/bin/python absent)"
      echo "    created $(stat -c %w "$D" 2>/dev/null || stat -c %y "$D")"
    else
      echo "$v: ABSENT -- $D does not exist"
    fi
  done
  echo
  echo "distribution root filesystem created:"
  stat -c '  / %w' / 2>/dev/null || echo "  (birth time unavailable)"
  echo "  oldest file in /var/lib/openmycelium: $(find /var/lib/openmycelium -printf '%T+\n' 2>/dev/null | sort | head -1)"
} | tee "$OUT"
