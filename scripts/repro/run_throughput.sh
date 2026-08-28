#!/usr/bin/env bash
# Launch the throughput measurement. In a file rather than a -c string because
# nested command substitution does not survive the Windows-to-WSL boundary.
set -uo pipefail
mkdir -p /var/log/om-a8
cp /mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro/throughput.py /root/throughput.py
OM_TOKEN=$(/opt/om/venv/bin/python -c "import secrets; print(secrets.token_hex(16))")
export OM_TOKEN
export PATH=/opt/om/venv/bin:$PATH
unset PYTHONPATH
cd /root || exit 1
exec /opt/om/venv/bin/python /root/throughput.py
