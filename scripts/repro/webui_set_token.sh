#!/usr/bin/env bash
# Configure Open WebUI's OpenMycelium provider with the real token.
#
# Runs inside the distribution that owns the secret, so the value is read from
# its file and posted directly. It never appears in a command line, a shell
# history, or this session's transcript.
set -uo pipefail
SECRET=/run/openmycelium/openwebui.token
JWT=/root/webui.jwt
HOST=$(ip route show default | awk '{print $3}')
UI="http://$HOST:3000"

[ -s "$SECRET" ] || { echo "  no token file"; exit 1; }
[ -s "$JWT" ]    || { echo "  no Open WebUI session token"; exit 1; }

echo "  Open WebUI reachable at $UI (Windows host from this distribution)"
curl -s -o /dev/null -w '  health -> HTTP %{http_code}\n' -m 10 "$UI/health"

# The body is assembled by a program reading the secret from the file, so the
# secret is never an argument to anything.
python3 - "$SECRET" "$JWT" "$UI" <<'PY'
import json, sys, urllib.request

secret = open(sys.argv[1]).read().strip()
jwt = open(sys.argv[2]).read().strip()
ui = sys.argv[3]

body = json.dumps({
    "ENABLE_OPENAI_API": True,
    "OPENAI_API_BASE_URLS": ["http://host.docker.internal:11500/v1"],
    "OPENAI_API_KEYS": [secret],
    "OPENAI_API_CONFIGS": {"0": {"enable": True}},
}).encode()

request = urllib.request.Request(f"{ui}/openai/config", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {jwt}"})
with urllib.request.urlopen(request, timeout=30) as handle:
    reply = json.loads(handle.read() or b"{}")
# Print the shape, never the value.
keys = reply.get("OPENAI_API_KEYS") or []
print(f"    configured  base={reply.get('OPENAI_API_BASE_URLS')}")
print(f"    key         {len(keys)} entry, {len(keys[0]) if keys else 0} chars "
      f"(value not shown)")
print(f"    enabled     {reply.get('ENABLE_OPENAI_API')}")
PY
