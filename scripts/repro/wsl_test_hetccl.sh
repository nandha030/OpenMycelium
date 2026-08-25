#!/usr/bin/env bash
S=/mnt/c/Users/User/Documents/Open_Mycelium
cd "$S/runtime/hetccl/tests" || exit 1
export PYTHONPATH="$S/runtime/hetccl/src"
if [ -n "${1:-}" ]; then
  /opt/hetenv/bin/python -m unittest "$1" 2>&1 | tail -6
else
  /opt/hetenv/bin/python -m unittest discover -s . 2>&1 | tail -5
  echo "--- failures ---"
  /opt/hetenv/bin/python -m unittest discover -s . 2>&1 | grep -E '^(FAIL|ERROR):'
fi
