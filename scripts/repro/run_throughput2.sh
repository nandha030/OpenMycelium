#!/usr/bin/env bash
set -uo pipefail
mkdir -p /var/log/om-a8
OM_TOKEN=$(/opt/om/venv/bin/python -c "import secrets; print(secrets.token_hex(16))")
export OM_TOKEN
export PATH=/opt/om/venv/bin:$PATH
unset PYTHONPATH
cd /root || exit 1
exec /opt/om/venv/bin/python \
  /mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro/throughput2.py
