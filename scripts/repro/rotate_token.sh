#!/usr/bin/env bash
# Rotate the smoke token onto an ephemeral secret path.
#
# Three corrections over the first attempt:
#   - /var/log is for logs. The secret moves to /run, which is tmpfs and does
#     not survive a reboot.
#   - the value is never printed, so it cannot reach a tool call or transcript.
#   - the value is never passed as --token, which would put it in the process
#     command line where any user can read it from ps. It goes through the
#     environment instead, readable only by the owner via /proc/<pid>/environ.
set -uo pipefail
OM=/opt/om/venv
SECRET_DIR=/run/openmycelium
SECRET="$SECRET_DIR/openwebui.token"
LOG=/var/log/om-webui
export PATH="$OM/bin:$PATH"
unset PYTHONPATH
cd /root || exit 1

echo "  == stop the endpoint and destroy the old token =="
"$OM/bin/openmycelium" stop > /dev/null 2>&1
sleep 3
if [ -f "$LOG/token" ]; then
  shred -u "$LOG/token" 2>/dev/null || rm -f "$LOG/token"
  echo "    destroyed $LOG/token"
fi
[ -e "$LOG/token" ] && echo "    STILL PRESENT" || echo "    confirmed gone"
find /var/log -name 'token*' 2>/dev/null | sed 's/^/    stray: /'

echo
echo "  == new token on an ephemeral path =="
install -d -m 700 "$SECRET_DIR"
umask 077
"$OM/bin/python" -c "import secrets; print(secrets.token_hex(24))" > "$SECRET"
chmod 600 "$SECRET"
echo "    path        $SECRET"
echo "    mode        $(stat -c%a "$SECRET")  owner $(stat -c%U "$SECRET")"
echo "    length      $(wc -c < "$SECRET") bytes (value not shown)"
echo "    filesystem  $(df --output=fstype "$SECRET_DIR" | tail -1)"

echo
echo "  == start the endpoint, token via environment only =="
OPENMYCELIUM_TOKEN=$(cat "$SECRET")
export OPENMYCELIUM_TOKEN
mkdir -p "$LOG"
nohup "$OM/bin/openmycelium" serve --model Mistral-Nemo-Instruct-2407 \
  --host 0.0.0.0 --port 11500 --max-new-tokens 512 \
  > "$LOG/serve.log" 2>&1 &
PID=$!
echo "$PID" > "$LOG/serve.pid"

printf '  waiting for the model to load'
for _ in $(seq 1 150); do
  sleep 4
  printf '.'
  if curl -sf -m 5 -H "Authorization: Bearer $OPENMYCELIUM_TOKEN" \
       http://127.0.0.1:11500/v1/models > /dev/null 2>&1; then
    echo
    echo "    ready"
    echo "    command line carries no token:"
    tr '\0' ' ' < "/proc/$PID/cmdline" | sed 's/^/      /'
    echo
    exit 0
  fi
done
echo
echo "  server did not become ready"
tail -12 "$LOG/serve.log"
exit 1
