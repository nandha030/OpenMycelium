# OpenMycelium

An installable control-plane foundation for discovering compute, governing logical accelerator pools, and operating AI workloads across local hosts and clusters. The interface is designed as a quiet, premium operations workspace: real data, explicit state, compact controls, and no fabricated infrastructure.

See [FEATURES.md](FEATURES.md) for the implemented capability matrix and the remaining production roadmap.

## Developer Edition: deploy anywhere

The package includes a Go control-plane service that embeds the dashboard and exposes authenticated APIs for discovery, logical accelerator pools, queues, workloads, lifecycle actions, access control, audit events, and observability.

- **Hardware discovery:** reads a host-agent report and probes available NVIDIA CUDA and AMD ROCm tooling; on macOS it identifies the Apple Silicon Metal/Core ML memory domain.
- **CPU operation:** model planning and Ollama inference remain available when no accelerator is detected. OpenMycelium never reports a simulated accelerator as physical capacity.
- **Persistent governance:** PostgreSQL stores accounts, sessions, clusters, pool policies, queues, integrations, audit events, and control-plane state.
- **Operations:** workload start, stop, redeploy, refresh, deletion, SSH command generation, local Ollama deployment, model chat, local-user administration, and service summaries are controlled from the dashboard.

### Native binary

Requires Go 1.22+ for a local build:

```powershell
go run .
```

Or build a native executable:

```powershell
go build -o openmycelium.exe .
.\openmycelium.exe
```

Open `http://127.0.0.1:8080`. Release packaging is defined in `.goreleaser.yaml` for Windows, macOS (Intel and Apple Silicon), and Linux (x64 and ARM64).

### Docker

```powershell
docker compose up --build
```

The Docker deployment is published at `http://127.0.0.1:8081` so it does not conflict with a native local instance using port 8080.

The Developer Edition Compose stack now starts three services:

- `openmycelium`: dashboard, API, workload lifecycle, and Ollama proxy
- `postgres`: durable host and workload records
- `nats`: control-plane event transport for host and workload lifecycle events

Verify the stack after startup:

```powershell
docker compose ps
docker compose logs openmycelium
```

The control-plane logs should include `PostgreSQL persistence ready` and `NATS event bus ready`. PostgreSQL and NATS volumes retain data across normal restarts.

### Sign in and local accounts

Docker Compose loads authentication settings from the local `.env` file. With `AUTH_MODE=local`, the first startup creates a PostgreSQL-backed platform administrator from `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD`. Passwords are stored as bcrypt hashes; browser sessions use random, revocable, HttpOnly cookies.

```powershell
docker compose down
docker compose up --build -d
docker compose logs openmycelium
```

Open `http://127.0.0.1:8081`. An unauthenticated browser is redirected to `/login.html`; sign in with the administrator values saved in `.env`. The bootstrap credentials create an account only when the users table is empty, so changing `.env` later does not silently replace an existing password.

Public signup is closed by default. A platform administrator can enable **Settings > Allow local user registration**; new self-registered accounts receive the read-only `viewer` role. Operators and platform administrators can change workload state, while security settings require `platform_admin`.

Platform administrators can also open **User access & audit > Add user** to create an account directly, choose its role, disable it, or delete it. OpenMycelium prevents deletion of the current account and removal of the final active platform administrator.

### Kubernetes

Build or publish the Docker image first, then deploy it:

```powershell
docker build -t openmycelium/developer-edition:local .
kubectl apply -f .\deploy\kubernetes.yaml
kubectl port-forward -n openmycelium service/control-plane 8080:8080
```

The current Kubernetes manifest deploys the control plane. Real cluster accelerator discovery requires a published, privileged-enough but read-only node agent with vendor tooling; the checked-in DaemonSet still references a future agent image.

### API quick test

```powershell
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$login = @{ email = 'admin@example.com'; password = 'your-password-from-.env' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8081/api/v1/auth/login -ContentType 'application/json' -Body $login -WebSession $session
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8081/api/v1/discovery/scan -WebSession $session
Invoke-RestMethod -Uri http://127.0.0.1:8081/api/v1/pools -WebSession $session
Invoke-RestMethod -Uri http://127.0.0.1:8081/api/v1/observability/summary -WebSession $session
```

### CLI

The dependency-free CLI keeps a local revocable session cookie and calls the same RBAC-protected APIs as the dashboard:

```powershell
python .\openmycelium.py login admin@example.com
python .\openmycelium.py discover scan
python .\openmycelium.py cluster list
python .\openmycelium.py pool add cpu-local --runtime cpu
python .\openmycelium.py queue add research --priority 60 --memory-gb 32
python .\openmycelium.py user add operator@example.com --role operator
python .\openmycelium.py workload submit remote-trainer --kind training --pool cuda-production --ssh-host worker.example.com --ssh-user ubuntu
python .\openmycelium.py workload ssh-config JOB_ID --host worker.example.com --user ubuntu
python .\openmycelium.py workload ssh JOB_ID
python .\openmycelium.py workload delete JOB_ID
python .\openmycelium.py model list
python .\openmycelium.py model generate gemma4:12b "Reply with one concise sentence."
python .\openmycelium.py observability summary
python .\openmycelium.py audit list
```

Prometheus metrics are exposed at `/metrics`. Set `METRICS_TOKEN` and send it as a bearer token for a non-browser collector:

```powershell
Invoke-WebRequest -Uri http://127.0.0.1:8081/metrics -Headers @{ Authorization = "Bearer $env:METRICS_TOKEN" }
```

Configure the Prometheus scrape job or ServiceMonitor to read that bearer token from a Kubernetes Secret. The Helm chart does not add unauthenticated scrape annotations by default.

### Windows host discovery with Docker

Docker Desktop runs Linux containers inside a VM, so a container cannot reliably inspect Windows CPU, RAM, or display adapters directly. Run the included read-only Windows host agent after the Docker control plane is listening:

```powershell
.\agents\windows\discover.ps1 -ControlPlane http://127.0.0.1:8081
```

It uses Windows CIM to report the CPU, logical cores, physical RAM, and display adapters into the local control plane. Refresh the dashboard, then choose **Discovery & install** and select **Scan hardware**.

The Docker Compose deployment stores this profile and managed workload records in the `openmycelium-data` Docker volume. A normal `docker compose down`, restart, or rebuild preserves them. Do not use `docker compose down --volumes` unless you intentionally want to erase local OpenMycelium state.

### Ollama model integration

When running through Docker Desktop, the control plane connects to the Ollama service already running on Windows through `host.docker.internal:11434`. It discovers models with the Ollama tags API; it does not copy or re-download them.

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8081/api/v1/models -WebSession $session
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8081/api/v1/inference/deploy -ContentType 'application/json' -Body '{"model":"gemma4:12b"}' -WebSession $session
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8081/api/v1/inference/generate -ContentType 'application/json' -Body '{"model":"gemma4:12b","prompt":"Reply with one concise sentence."}' -WebSession $session
```

The last command performs actual inference through the local Ollama runtime. On a CPU-only host, this may be slow for a 12B model; OpenMycelium registers that fact as a CPU-backed workload rather than presenting it as GPU execution.

Ollama remains the model-runtime process on the Windows host. OpenMycelium deploys a managed workload record and routes requests to that runtime; it intentionally does not create a second container or duplicate the downloaded model.

### Workload SSH access

SSH is optional metadata for remote or cluster-backed workloads. Supply the host, port, and username when submitting the workload, or choose **Add SSH** on any existing workload. The dashboard's **SSH** control returns a validated command such as `ssh -p 22 ubuntu@worker.example.com` and can copy it to the clipboard. Passwords and private keys are never stored by OpenMycelium; the command uses the SSH agent, key files, host verification, and access policy configured on the operator's machine. Local Ollama workload records can also be associated with a remote endpoint, but OpenMycelium does not create an SSH service inside the Ollama process.

## Run locally

Requires Python 3.10+ only.

```powershell
cd C:\path\to\OpenMycelium-MVP
.\install.ps1
```

Open `http://127.0.0.1:8080`, then choose **Discovery & install** and press **Scan hardware**. The API calls `nvidia-smi` and `rocm-smi` when they are available on the host.

```powershell
python .\openmycelium.py discover scan
python .\openmycelium.py accelerator list
python .\om_agent.py
```

## CPU and GPU model planning

The dashboard's **Model planner** works on CPU-only systems and accelerator hosts. It estimates:

- resident weight memory from total parameter count and weight quantization
- KV cache from transformer layers, KV heads, head dimension, context length, batch, and KV precision
- a conservative runtime overhead and remaining host/GPU memory
- per-token compute from active parameters; this matters for mixture-of-experts (MoE) models

For MoE, all model weights usually need to remain resident, while only active experts contribute to the approximate FLOPs-per-token number. The planner is for capacity decisions, not a replacement for a runtime benchmark, because CPU architecture, memory bandwidth, kernels, offloading, and model implementation substantially affect tokens per second.

The deployment lab also models inference, LoRA/QLoRA, and full-parameter training; multi-device tensor and pipeline parallelism; per-device memory; CPU offload; safety reserve; interconnect overhead; memory-bandwidth and compute ceilings; estimated throughput; and maximum inference concurrency. Hardware profiles are planning envelopes rather than benchmark guarantees.

## Kubernetes discovery-agent install

First configure a Kubernetes context and install the appropriate NVIDIA or AMD device plugin. Then apply the included read-only RBAC and DaemonSet manifest:

```powershell
kubectl apply -f .\k8s\openmycelium.yaml
kubectl get daemonset -n openmycelium
kubectl get pods -n openmycelium
```

The manifest intentionally references the future published agent image `ghcr.io/nandha030/openmycelium-agent:0.1.0`; it will not become runnable in a cluster until that image is built and pushed. The RBAC policy is valid and read-only, but the current agent container is a placeholder, not a real device collector.

## MCP and operations agent

`mcp_server.py` implements the standard-input MCP protocol and provides three tools: `openmycelium_scan_hardware`, `openmycelium_list_accelerators`, and `openmycelium_k8s_install_plan`. Register it with an MCP client using `python` as the command and `mcp_server.py` as the argument, from this package directory.

`om_agent.py` is intentionally safe: it diagnoses local readiness and emits a configuration plan. It does not make cluster changes without an explicit operator command.

The dashboard must be served by the Go control plane. Opening `index.html` directly cannot provide authentication or API-backed resources.

## Helm deployment

The chart in `deploy/helm/openmycelium` deploys the control plane with non-root security context, probes, resource limits, Prometheus annotations, and an optional default-deny ingress policy. Supply PostgreSQL and NATS endpoints from production services:

```powershell
helm upgrade --install openmycelium .\deploy\helm\openmycelium `
  --namespace openmycelium --create-namespace `
  --set image.repository=ghcr.io/nandha030/openmycelium `
  --set image.tag=0.1.0 `
  --set-string secrets.databaseUrl='postgres://...' `
  --set-string secrets.natsUrl='nats://...' `
  --set-string secrets.bootstrapAdminEmail='admin@example.com' `
  --set-string secrets.bootstrapAdminPassword='replace-me' `
  --set-string secrets.metricsToken='replace-me'
```

## Architecture choice

The architecture is self-hosted and Kubernetes-oriented: a Helm-installed control plane, PostgreSQL system of record, NATS event transport, local or in-cluster discovery agents, and runtime adapters. Logical pools coordinate placement across vendors; they do not combine physically separate VRAM into hardware unified memory.
