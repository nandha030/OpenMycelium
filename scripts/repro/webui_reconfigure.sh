#!/usr/bin/env bash
# Recreate the smoke container so its provider can be configured without a GUI.
#
# The browser pane cannot be displayed in this environment, so the settings page
# cannot be driven by clicking. Open WebUI gates POST /openai/config behind
# ENABLE_OPENAI_API_PASSTHROUGH, so that flag is enabled here to reach the same
# endpoint the settings page posts to. This is a property of the test harness,
# not of OpenMycelium, and the report says so: it means the integration is
# exercised through Open WebUI's real backend request path, while visual
# rendering is not verified.
#
# Ollama is disabled. The host is running one, Open WebUI discovers it by
# default, and a smoke that silently mixed in someone else's inference server
# would prove nothing about OpenMycelium.
#
# Still no OpenMycelium token in the environment: it is posted afterwards from
# the distribution that holds the secret file.
set -uo pipefail
NAME=$(cat /tmp/om-smoke-container 2>/dev/null)
VOLUME=$(cat /tmp/om-smoke-volume 2>/dev/null)
IMAGE="ghcr.io/open-webui/open-webui@sha256:6bb1fbe8ab0a3e0456067f493044ffb66a30a65a34be47f6a5862176a370dd16"

echo "  == replacing the container, keeping volume $VOLUME =="
docker rm -f "$NAME" > /dev/null 2>&1
docker run -d --name "$NAME" \
  -p 3000:8080 \
  -v "$VOLUME:/app/backend/data" \
  -e WEBUI_AUTH=False \
  -e ENABLE_OPENAI_API_PASSTHROUGH=True \
  -e ENABLE_OLLAMA_API=False \
  --add-host host.docker.internal:host-gateway \
  --restart no \
  "$IMAGE" > /dev/null
echo "    pinned by digest, not tag"

echo
echo "  == no token, no GPU =="
docker inspect "$NAME" --format '    devices {{.HostConfig.Devices}}  deviceRequests {{.HostConfig.DeviceRequests}}'
docker inspect "$NAME" --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -icE '^(OPENAI_API_KEY|OPENMYCELIUM_TOKEN)=.+' \
  | sed 's/^/    secrets in environment: /'

printf '  waiting for the UI'
for _ in $(seq 1 80); do
  sleep 4
  printf '.'
  if curl -sf -m 5 http://localhost:3000/health > /dev/null 2>&1; then
    echo; echo "    healthy"
    break
  fi
done
echo "  == version =="
curl -s -m 10 http://localhost:3000/api/version | sed 's/^/    /'
echo
