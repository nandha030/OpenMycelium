set -u
/opt/hetenv/bin/python /mnt/c/Users/User/Documents/Open_Mycelium/runtime/cli/coordinator.py \
  --model /opt/models/Mistral-Nemo-Instruct-2407 \
  --prompt "Explain cross-vendor GPU inference" \
  --max-new-tokens 64 --port 31977
