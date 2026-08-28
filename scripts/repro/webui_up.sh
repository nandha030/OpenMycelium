#!/usr/bin/env bash
# Bring up the pinned Open WebUI for the integration smoke.
#
#   - unique container and volume names, so nothing existing is reused or
#     disturbed
#   - no GPU access: the UI is a web front end and has no business holding a
#     device that the inference stages need
#   - no OpenMycelium token in the environment. `docker inspect` shows every
#     variable to anyone who can reach the daemon, so the token is typed into
#     the UI and lives only in the container's own database
#   - WEBUI_AUTH disabled: this is a throwaway single-user container, and
#     turning it off avoids creating an account and handling a password
set -uo pipefail
STAMP=$(date +%Y%m%d-%H%M%S)
NAME="om-smoke-webui-$STAMP"
VOLUME="om-smoke-webui-data-$STAMP"
IMAGE="ghcr.io/open-webui/open-webui:v0.11.1"
PORT=3000

echo "  == image identity =="
docker image inspect "$IMAGE" \
  --format '    id        {{.Id}}
    digest    {{index .RepoDigests 0}}
    created   {{.Created}}
    arch      {{.Os}}/{{.Architecture}}' 2>&1

echo
echo "  == volume =="
docker volume create "$VOLUME" > /dev/null
docker volume inspect "$VOLUME" --format '    {{.Name}}  {{.Driver}}  {{.Mountpoint}}'

echo
echo "  == container =="
docker run -d --name "$NAME" \
  -p "$PORT:8080" \
  -v "$VOLUME:/app/backend/data" \
  -e WEBUI_AUTH=False \
  --add-host host.docker.internal:host-gateway \
  --restart no \
  "$IMAGE" > /dev/null
echo "    name      $NAME"
echo "    volume    $VOLUME"
echo "    url       http://localhost:$PORT"

echo
echo "  == no GPU was granted =="
docker inspect "$NAME" --format '    devices        {{.HostConfig.Devices}}
    deviceRequests {{.HostConfig.DeviceRequests}}
    runtime        {{.HostConfig.Runtime}}'

echo
echo "  == no OpenMycelium token in the environment =="
docker inspect "$NAME" --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -iE 'token|api_key|openai' | sed 's/^/    /' || echo "    none present"

echo
printf '  waiting for the UI'
for _ in $(seq 1 60); do
  sleep 3
  printf '.'
  if curl -sf -m 5 "http://localhost:$PORT/health" > /dev/null 2>&1; then
    echo; echo "    healthy"
    break
  fi
done

echo
echo "  == reported version =="
curl -s -m 10 "http://localhost:$PORT/api/version" 2>/dev/null | sed 's/^/    /'
echo
echo "$NAME" > /tmp/om-smoke-container
echo "$VOLUME" > /tmp/om-smoke-volume
