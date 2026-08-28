set -u
cd /mnt/c/Users/User/Documents/Open_Mycelium
export PYTHONPATH=runtime/serving:runtime/scheduler:runtime/fabric:runtime/cli:runtime/mccl/src
rm -rf /opt/openmycelium/servesmoke /opt/openmycelium/run
nohup /opt/hetenv/bin/python runtime/cli/serve.py --model tiny-mistral --allow-cpu \
  --port 11500 --worker-port 31996 --context-length 512 --max-new-tokens 8 \
  --work-dir /opt/openmycelium/servesmoke > /opt/serve.log 2>&1 &
for i in $(seq 1 120); do
  curl -s --max-time 2 http://127.0.0.1:11500/health >/dev/null 2>&1 && break
  sleep 1
done
echo "--- health ---";  curl -s http://127.0.0.1:11500/health | head -c 320; echo
echo "--- models ---";  curl -s http://127.0.0.1:11500/v1/models; echo
echo "--- ps sees it ---"; /opt/hetenv/bin/python runtime/cli/lifecycle.py ps 2>/dev/null | head -6
echo "--- temperature 0.7 refused ---"
curl -s -X POST http://127.0.0.1:11500/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"t","messages":[{"role":"user","content":"hi"}],"temperature":0.7}' | head -c 280; echo
echo "--- top_p refused ---"
curl -s -X POST http://127.0.0.1:11500/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"t","messages":[{"role":"user","content":"hi"}],"top_p":0.9}' | head -c 280; echo
echo "--- non-streaming completion ---"
curl -s -X POST http://127.0.0.1:11500/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"t","messages":[{"role":"user","content":"hello"}],"temperature":0}' | head -c 400; echo
echo "--- streaming (first 5 events) ---"
curl -s -N -X POST http://127.0.0.1:11500/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"t","messages":[{"role":"user","content":"hello"}],"stream":true}' | head -5
echo "--- stop ---"; /opt/hetenv/bin/python runtime/cli/lifecycle.py stop 2>/dev/null
sleep 2
echo "--- port released? ---"; curl -s --max-time 2 http://127.0.0.1:11500/health >/dev/null 2>&1 && echo "still up" || echo "gone"
echo "--- ps after stop ---"; /opt/hetenv/bin/python runtime/cli/lifecycle.py ps 2>/dev/null | head -4
