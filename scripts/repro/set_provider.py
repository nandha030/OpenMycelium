"""Set Open WebUI's OpenMycelium provider key, reading the secret from stdin.

Runs inside the Open WebUI container. The secret is piped in, so it is never an
argument, never in a shell history, and never visible to `docker inspect`.
"""

from __future__ import annotations

import json
import sys
import urllib.request

secret = sys.stdin.read().strip()
jwt = sys.argv[1]
base = "http://localhost:8080"

body = json.dumps({
    "ENABLE_OPENAI_API": True,
    "OPENAI_API_BASE_URLS": ["http://host.docker.internal:11500/v1"],
    "OPENAI_API_KEYS": [secret],
    "OPENAI_API_CONFIGS": {"0": {"enable": True}},
}).encode()

request = urllib.request.Request(f"{base}/openai/config/update", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {jwt}"})
with urllib.request.urlopen(request, timeout=60) as handle:
    reply = json.loads(handle.read() or b"{}")

keys = reply.get("OPENAI_API_KEYS") or []
print(f"    base URL   {reply.get('OPENAI_API_BASE_URLS')}")
print(f"    key        {len(keys)} entry, {len(keys[0]) if keys else 0} chars "
      f"(value not shown)")
print(f"    enabled    {reply.get('ENABLE_OPENAI_API')}")
