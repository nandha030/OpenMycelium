#!/usr/bin/env bash
# RC1 serve gate: the official OpenAI Python SDK against the endpoint, then
# lifecycle cleanup.
#
# The earlier API checks used only the standard library, deliberately, so they
# tested the wire rather than one vendor's client. This one uses the SDK the
# documentation tells people to use, because that is what they will actually
# run.
set -uo pipefail
OM=/opt/om/venv
LOG=/var/log/om-rc1
SDK=/opt/om/sdk
MODEL=Mistral-Nemo-Instruct-2407
FAIL=0
mkdir -p "$LOG"
cd /root || exit 1
export PATH="$OM/bin:$PATH"
unset PYTHONPATH
check() {
  if [ "$2" = "0" ]; then printf '  [PASS] %s\n' "$1" | tee -a "$LOG/rc1.log"
  else printf '  [FAIL] %s\n' "$1" | tee -a "$LOG/rc1.log"; FAIL=$((FAIL+1)); fi
}

printf '\n  == the official OpenAI SDK ==\n'
if [ ! -x "$SDK/bin/python" ]; then
  python3 -m venv "$SDK" > /dev/null 2>&1
  "$SDK/bin/pip" install -q openai > "$LOG/sdk-install.log" 2>&1
fi
"$SDK/bin/python" -c "import openai; print(f'    openai {openai.__version__}')"
check "OpenAI SDK available" $?

printf '\n  == start serve ==\n'
install -d -m 700 /run/openmycelium
umask 077
"$OM/bin/python" -c "import secrets; print(secrets.token_hex(24))" > /run/openmycelium/rc1.token
chmod 600 /run/openmycelium/rc1.token
OPENMYCELIUM_TOKEN=$(cat /run/openmycelium/rc1.token)
export OPENMYCELIUM_TOKEN
nohup "$OM/bin/openmycelium" serve --model "$MODEL" --host 127.0.0.1 --port 11500 \
  --max-new-tokens 256 > "$LOG/serve.log" 2>&1 &
printf '  waiting'
READY=1
for _ in $(seq 1 150); do
  sleep 4; printf '.'
  if curl -sf -m 5 -H "Authorization: Bearer $OPENMYCELIUM_TOKEN" \
       http://127.0.0.1:11500/v1/models > /dev/null 2>&1; then READY=0; break; fi
done
echo
check "serve reached ready" $READY

if [ "$READY" = "0" ]; then
  OPENAI_API_KEY="$OPENMYCELIUM_TOKEN" "$SDK/bin/python" - <<'PY'
import os, sys
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:11500/v1",
                api_key=os.environ["OPENAI_API_KEY"])
bad = 0

models = [m.id for m in client.models.list().data]
print(f"    models        {models}")
bad += 0 if "Mistral-Nemo-Instruct-2407" in models else 1

reply = client.chat.completions.create(
    model="Mistral-Nemo-Instruct-2407",
    messages=[{"role": "user", "content": "Reply with exactly: ready"}],
    max_tokens=16)
print(f"    non-streaming {reply.choices[0].message.content.strip()!r}  "
      f"finish={reply.choices[0].finish_reason}  usage={reply.usage.total_tokens}")
bad += 0 if reply.choices[0].message.content.strip() else 1

stream = client.chat.completions.create(
    model="Mistral-Nemo-Instruct-2407",
    messages=[{"role": "user", "content": "Count from one to eight."}],
    max_tokens=48, stream=True, stream_options={"include_usage": True})
chunks, text, usage = 0, [], None
for event in stream:
    if event.usage:
        usage = event.usage
    for choice in event.choices:
        if choice.delta.content:
            chunks += 1
            text.append(choice.delta.content)
print(f"    streaming     {chunks} chunks, usage={usage.total_tokens if usage else None}")
print(f"    text          {''.join(text)[:60]!r}")
bad += 0 if chunks > 3 and "".join(text).strip() else 1

try:
    client.chat.completions.create(
        model="Mistral-Nemo-Instruct-2407",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=8, temperature=0.9)
    print("    sampling      ACCEPTED (should have been refused)")
    bad += 1
except Exception as error:
    print(f"    sampling      refused: {type(error).__name__}")

sys.exit(1 if bad else 0)
PY
  check "OpenAI SDK: discovery, non-streaming, streaming, sampling refusal" $?
fi

printf '\n  == lifecycle ==\n'
openmycelium ps 2>&1 | sed 's/^/    /' | head -6
openmycelium stop > "$LOG/stop.log" 2>&1
check "openmycelium stop" $?
sleep 5
ORPHANS=$(pgrep -fc "pipeline_run" 2>/dev/null); ORPHANS=${ORPHANS:-0}
echo "    worker processes remaining: $ORPHANS"
[ "$ORPHANS" = "0" ]
check "zero orphan workers" $?
nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null | sed 's/^/    CUDA in use: /'
USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1)
[ "${USED:-99999}" -lt 3000 ]
check "VRAM released" $?

shred -u /run/openmycelium/rc1.token 2>/dev/null || rm -f /run/openmycelium/rc1.token
rmdir /run/openmycelium 2>/dev/null
echo "    temporary token destroyed"

printf '\n  %d check(s) failed in the serve gate\n' "$FAIL" | tee -a "$LOG/rc1.log"
exit "$FAIL"
