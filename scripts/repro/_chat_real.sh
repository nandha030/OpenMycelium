set -u
printf 'Explain cross-vendor GPU inference in two sentences.\nNow name one limitation of that approach.\n/stats\n/bye\n' \
  | /opt/hetenv/bin/python /mnt/c/Users/User/Documents/Open_Mycelium/runtime/cli/chat.py \
      --model "C:\Users\User\Downloads\Models\Mistral-Nemo-Instruct-2407" \
      --max-new-tokens 48 --port 31996
