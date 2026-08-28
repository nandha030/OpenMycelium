set -u
D=/mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro
echo "################## PREFILL ##################"
LIMIT=3000 bash "$D/wsl_prefill_decode.sh" prefill
echo
echo "################## ORACLE ##################"
PROMPT_LEN=16 DECODE_STEPS=32 LIMIT=3600 PORT=31910 bash "$D/wsl_prefill_decode.sh" oracle
