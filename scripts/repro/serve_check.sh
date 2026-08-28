#!/usr/bin/env bash
# P8: the OpenAI-compatible endpoint, in the fresh distribution.
#
# Bound to 0.0.0.0 with a token, because the Open WebUI step that follows has to
# reach it from a different container. That also puts the token enforcement path
# under test rather than the loopback exemption.
set -uo pipefail

LOG=/var/log/om-bootstrap
OM=/opt/om/venv
S=/mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro
MODEL=Mistral-Nemo-Instruct-2407
FAIL=0
export PATH="$OM/bin:$PATH"
unset PYTHONPATH

check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1"
  else printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); fi
}

TOKEN=$("$OM/bin/python" -c "import secrets; print(secrets.token_hex(16))")
echo "$TOKEN" > "$LOG/serve-token.txt"
chmod 600 "$LOG/serve-token.txt"
IP=$(ip -4 addr show eth0 | awk '/inet /{print $2}' | cut -d/ -f1)

printf '\n  == P8  serve, authenticated and non-loopback ==\n'
echo "  bind      0.0.0.0:11500"
echo "  reachable http://$IP:11500/v1   (token in $LOG/serve-token.txt)"

openmycelium serve --model "$MODEL" --host 0.0.0.0 --port 11500 \
  --token "$TOKEN" --max-new-tokens 512 > "$LOG/serve.log" 2>&1 &
SERVE_PID=$!

printf '  waiting for the model to load'
READY=1
for _ in $(seq 1 150); do
  sleep 4
  printf '.'
  if curl -sf -m 5 -H "Authorization: Bearer $TOKEN" \
       "http://127.0.0.1:11500/v1/models" > /dev/null 2>&1; then
    READY=0
    break
  fi
  if ! kill -0 "$SERVE_PID" 2>/dev/null; then
    echo
    echo "  server exited early:"
    tail -20 "$LOG/serve.log" | sed 's/^/    /'
    break
  fi
done
echo
check "server reached ready" $READY

if [ "$READY" = "0" ]; then
  printf '\n  -- protocol --\n'
  "$OM/bin/python" "$S/api_check.py" "http://127.0.0.1:11500" "$TOKEN" --concurrency \
    2>&1 | tee "$LOG/api.log"
  check "OpenAI protocol checks" "${PIPESTATUS[0]}"

  printf '\n  -- token enforcement on the non-loopback address --\n'
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "http://$IP:11500/v1/models")
  echo "    no token      HTTP $CODE"
  [ "$CODE" = "401" ] || [ "$CODE" = "403" ]
  check "an unauthenticated non-loopback request is refused" $?

  CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 15 \
         -H "Authorization: Bearer wrong-token" "http://$IP:11500/v1/models")
  echo "    wrong token   HTTP $CODE"
  [ "$CODE" = "401" ] || [ "$CODE" = "403" ]
  check "a wrong token is refused" $?

  CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 30 \
         -H "Authorization: Bearer $TOKEN" "http://$IP:11500/v1/models")
  echo "    right token   HTTP $CODE"
  [ "$CODE" = "200" ]
  check "the correct token is accepted over the non-loopback address" $?
fi

printf '\n  -- lifecycle --\n'
openmycelium ps 2>&1 | sed 's/^/    /'
check "openmycelium ps" $?
openmycelium stop > "$LOG/stop.log" 2>&1
check "openmycelium stop" $?
wait "$SERVE_PID" 2>/dev/null
sleep 3

# pgrep -c prints 0 and exits non-zero on no match; a `|| echo 0` fallback then
# yields "0\n0" and the integer comparison fails on a clean system.
ORPHANS=$(pgrep -fc "pipeline_run" 2>/dev/null); ORPHANS=${ORPHANS:-0}
echo "    worker processes remaining: $ORPHANS"
[ "$ORPHANS" -le 1 ]
check "no orphan workers left behind" $?

printf '\n  %d check(s) failed in P8\n' "$FAIL"
echo "$FAIL" > "$LOG/p8fail.txt"
exit "$FAIL"
