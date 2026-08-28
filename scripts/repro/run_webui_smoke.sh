#!/usr/bin/env bash
# Run the Open WebUI smoke from the distribution, pointing at the Windows host
# where the container publishes its port.
set -uo pipefail
SRC=/mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro
HOST=$(ip route show default | awk '{print $3}')
cp /root/webui.jwt /tmp/webui.jwt
sed "s#^UI = .*#UI = \"http://$HOST:3000\"#" "$SRC/${1:-webui_smoke.py}" > /tmp/ws.py
echo "  driving Open WebUI at http://$HOST:3000"
exec /opt/om/venv/bin/python /tmp/ws.py
