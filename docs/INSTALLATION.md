# OpenMycelium Installation Guide

This guide covers the current Developer Edition deployment paths. The branded Docker launcher is the recommended installation method for a workstation, lab server, or evaluation environment.

## Platform support

| Target | Status | Installation path |
| --- | --- | --- |
| Windows 10/11 with Docker Desktop | Supported | `install.ps1` |
| macOS Intel or Apple Silicon with Docker Desktop | Supported | `install.sh` |
| Ubuntu and other modern Linux distributions | Supported | `install.sh` with Docker Engine and Compose v2 |
| Canonical Ubuntu Server | Supported | `install.sh` |
| Generic Kubernetes | Evaluation chart available | Helm with external PostgreSQL and NATS |
| MicroK8s or Canonical Kubernetes | Evaluation path | Standard Helm chart; production validation remains |

The Compose stack builds for the host architecture. Native release definitions cover Windows, macOS, and Linux on AMD64 and ARM64, but signed native packages are not published yet.

## Prerequisites

- Docker Desktop, or Docker Engine with the Compose v2 plugin
- At least 4 CPU cores and 8 GiB RAM for the complete local stack
- Ports `8081`, `9090`, and `3000` available
- PowerShell 5.1+ on Windows, or a POSIX shell plus `curl` on macOS/Linux
- Ollama only when local model inference is required

GPU hardware is optional. OpenMycelium supports CPU-only planning, orchestration, and inference when the selected model fits available RAM.

## Windows installation

Open PowerShell in the repository directory:

```powershell
cd C:\path\to\OpenMycelium-MVP
.\install.ps1 -Open
```

If script execution is restricted:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Open
```

The launcher validates Docker, creates secure first-run configuration when `.env` is absent, assigns a persistent Site ID, builds the images, starts all services, and waits for readiness.

## macOS and Linux installation

```bash
cd /path/to/OpenMycelium-MVP
chmod +x install.sh
./install.sh --open
```

On Ubuntu Server, install Docker Engine and the Compose v2 plugin before running the launcher. Run Docker as a non-root user or invoke the installer from a shell that can access the Docker daemon.

## First-run configuration

When `.env` does not exist, the installer creates it with:

- a generated platform-administrator password
- a persistent OpenMycelium Site ID
- an AES-256 cluster credential key
- Prometheus, Grafana, and discovery-agent credentials
- local authentication defaults

The generated administrator password is displayed once. The `.env` file is ignored by Git and must not be committed.

Existing `.env` files are preserved. A missing Site ID is appended without replacing existing credentials.

## Verify the product

```powershell
.\install.ps1 -Action Status
Invoke-RestMethod http://127.0.0.1:8081/api/v1/ready
```

```bash
./install.sh status
curl -fsS http://127.0.0.1:8081/api/v1/ready
```

Expected readiness response:

```json
{"status":"ready","siteId":"OM-XXXXXXXXXX"}
```

Product endpoints:

- Control plane: `http://127.0.0.1:8081`
- Prometheus: `http://127.0.0.1:9090`
- Grafana: `http://127.0.0.1:3000`

## Lifecycle commands

| Operation | Windows | macOS/Linux |
| --- | --- | --- |
| Start | `.\install.ps1 -Action Start -Open` | `./install.sh start --open` |
| Restart | `.\install.ps1 -Action Restart` | `./install.sh restart` |
| Status | `.\install.ps1 -Action Status` | `./install.sh status` |
| Logs | `.\install.ps1 -Action Logs` | `./install.sh logs` |
| Stop | `.\install.ps1 -Action Stop` | `./install.sh stop` |

Stopping the product preserves Docker volumes. Do not use `docker compose down --volumes` unless permanent deletion of PostgreSQL, NATS, Prometheus, Grafana, and OpenMycelium state is intended.

## Windows hardware discovery

Docker Desktop cannot directly inventory all Windows hardware. After the control plane is ready, run the read-only host agent:

```powershell
.\agents\windows\discover.ps1 -ControlPlane http://127.0.0.1:8081
```

For unattended enrollment, set `OPENMYCELIUM_AGENT_TOKEN` to the value configured as `AGENT_TOKEN` in `.env`.

## Local Ollama integration

Start Ollama on the host and download a model normally:

```powershell
ollama pull gemma3:4b
ollama list
```

Docker Desktop reaches the host runtime through `host.docker.internal:11434`. In OpenMycelium, open **Inference**, discover local models, register the selected model as a workload, and use the chat console for a live test.

## Kubernetes deployment

The Helm chart deploys the OpenMycelium control plane and expects production PostgreSQL and NATS endpoints:

```bash
helm upgrade --install openmycelium ./deploy/helm/openmycelium \
  --namespace openmycelium --create-namespace \
  --set image.repository=ghcr.io/nandha030/openmycelium \
  --set image.tag=latest \
  --set secrets.databaseUrl='postgres://USER:PASSWORD@HOST:5432/openmycelium' \
  --set secrets.natsUrl='nats://HOST:4222' \
  --set secrets.bootstrapAdminEmail='admin@example.com' \
  --set secrets.bootstrapAdminPassword='REPLACE_ME' \
  --set secrets.clusterCredentialKey='REPLACE_WITH_32_PLUS_CHARACTERS' \
  --set secrets.metricsToken='REPLACE_ME' \
  --set secrets.agentToken='REPLACE_ME'
```

The chart includes a non-root security context, health probes, resource controls, a ClusterIP Service, and an optional NetworkPolicy. Before production use, add an Ingress or gateway with TLS, external secret management, highly available PostgreSQL/NATS, backups, a published multi-architecture image, and a validated discovery-agent image.

MicroK8s and Canonical Kubernetes use the same Kubernetes APIs, but a dedicated MicroK8s add-on, GPU Operator integration, upgrade test matrix, and production support bundle are not yet included.

## Troubleshooting

If the installer stops, inspect:

```powershell
Get-Content .\.openmycelium\installer.log -Tail 100
docker compose ps
docker compose logs openmycelium postgres nats
```

```bash
tail -n 100 .openmycelium/installer.log
docker compose ps
docker compose logs openmycelium postgres nats
```

Common causes are a stopped Docker daemon, occupied ports, placeholder values in `.env`, insufficient Docker memory, or an unavailable container registry.
