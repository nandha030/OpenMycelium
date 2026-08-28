set -u
D=/mnt/c/Users/User/Documents/Open_Mycelium
echo "########## CPU smoke: prefill ##########"
bash $D/scripts/repro/wsl_pipeline_smoke.sh prefill 2>&1 | tail -3
echo "########## CPU smoke: oracle ##########"
bash $D/scripts/repro/wsl_pipeline_smoke.sh oracle 2>&1 | tail -3
echo "########## two-GPU chat under the Scheduler ##########"
printf 'Explain cross-vendor GPU inference in two sentences.\nName one limitation of that approach.\n/stats\n/bye\n' \
  | /opt/hetenv/bin/python $D/runtime/cli/chat.py --model mistral-nemo --max-new-tokens 48 --port 32011
