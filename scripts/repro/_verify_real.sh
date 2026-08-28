set -u
printf 'Explain cross-vendor GPU inference in two sentences.\nName one limitation of that approach.\n/stats\n/bye\n' \
  | /opt/hetenv/bin/python /mnt/c/Users/User/Documents/Open_Mycelium/runtime/cli/chat.py \
      --model mistral-nemo --max-new-tokens 48 --port 32001
