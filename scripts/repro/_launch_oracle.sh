#!/usr/bin/env bash
# Launch the oracle detached inside WSL, so it survives the Windows-side client
# disconnecting. A running process also keeps the WSL2 VM from idling out,
# which is what killed the previous attempt.
cd /opt
export PROMPT_LEN=16 DECODE_STEPS=32 LIMIT=3600 PORT=31910
setsid nohup bash /mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro/wsl_prefill_decode.sh \
    oracle >/opt/oracle_run.log 2>&1 </dev/null &
echo "launched pid $!"
