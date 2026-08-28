#!/usr/bin/env bash
# Bring up the authenticated OpenMycelium endpoint for the Open WebUI smoke.
#
# Bound to 0.0.0.0 so a container can reach it through the Windows host, with a
# temporary token. The token is written to a mode-600 file that is never copied
# into the release evidence, and is destroyed when the smoke finishes.
set -uo pipefail
OM=/opt/om/venv
LOG=/var/log/om-webui
mkdir -p "$LOG"
chmod 700 "$LOG"
export PATH="$OM/bin:$PATH"
unset PYTHONPATH
cd /root || exit 1

if [ ! -s "$LOG/token" ]; then
  "$OM/bin/python" -c "import secrets; print(secrets.token_hex(16))" > "$LOG/token"
  chmod 600 "$LOG/token"
fi
TOKEN=$(cat "$LOG/token")

nohup "$OM/bin/openmycelium" serve --model Mistral-Nemo-Instruct-2407 \
  --host 0.0.0.0 --port 11500 --token "$TOKEN" --max-new-tokens 512 \
  > "$LOG/serve.log" 2>&1 &
echo $! > "$LOG/serve.pid"

printf '  waiting for the model to load'
for _ in $(seq 1 150); do
  sleep 4
  printf '.'
  if curl -sf -m 5 -H "Authorization: Bearer $TOKEN" \
       http://127.0.0.1:11500/v1/models > /dev/null 2>&1; then
    echo
    echo "  ready"
    echo "  WSL address    $(ip -4 addr show eth0 | awk '/inet /{print $2}' | cut -d/ -f1):11500"
    echo "  token file     $LOG/token  (mode $(stat -c%a "$LOG/token"))"
    exit 0
  fi
done
echo
echo "  server did not become ready"
tail -12 "$LOG/serve.log"
exit 1
