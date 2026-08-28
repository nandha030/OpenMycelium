# OpenMycelium

Run one model across GPUs from different vendors.

**v0.1.0 Technical Preview** aggregates model capacity across one NVIDIA CUDA
GPU and one AMD ROCm GPU. It does not create unified VRAM. Validated on Windows
11 with WSL2, RTX 5060 Ti 16 GB, RX 9060 XT 16 GB, and
Mistral-Nemo-Instruct-2407: a 22.84 GiB checkpoint running on two 16 GiB cards,
181/182 exclusive tensor ownership, byte-exact cross-vendor transfer, 11.09
tok/s median decode.

## Two components, at different maturities

**OpenMycelium Node Runtime** — the Python execution engine: CUDA/ROCm
execution, model partitioning, MCCL cross-vendor transport, an
OpenAI-compatible API, a local operator console, and hardware telemetry.
**Hardware-qualified**, and what v0.1.0 ships.

**MHub Control Plane** — the Go platform: users, clusters, policies, queues,
scheduling, Kubernetes and fleet management. Substantial working code with
tests, **but it has never invoked the Node Runtime**. Treat it as
implemented-not-integrated until an authenticated Node Agent API connects the
two and one workload executes end to end.

[FEATURES.md](FEATURES.md) classifies every capability as hardware-qualified,
integrated, implemented, or roadmap. Read it before relying on anything here.

## What v0.1.0 does not do

Greedy decoding only; sampling parameters are refused rather than ignored. One
request at a time. One NVIDIA and one AMD GPU per pipeline — multi-AMD is not
qualified. Single node. One model family validated. No TLS, no background
service, no training. Manual installation of AMD ROCm-for-WSL system components
is required. See [LIMITATIONS.md](docs/LIMITATIONS.md).

## Why OpenMycelium

AI infrastructure is fragmented across GPU vendors, CPUs, local runtimes, Kubernetes distributions, model stores, schedulers, and agent frameworks. The long-term aim is one vendor-neutral control plane for discovering that capacity, planning model fit, governing placement, deploying workloads, and observing their real lifecycle without replacing the underlying CUDA, ROCm, Metal, Kubernetes, or model-runtime technologies.

That is the direction, not the current state. What exists today is a qualified single-node dual-vendor runtime, and a control plane that does not yet drive it.

## Documentation

- [Operating the runtime: install, configure, run, maintain, stop](docs/OPERATIONS.md)
- [Installing v0.1.0](docs/INSTALL.md)
- [Hardware matrix: qualified and explicitly unqualified](docs/HARDWARE_MATRIX.md)
- [Limitations](docs/LIMITATIONS.md)
- [Rollback and uninstall](docs/ROLLBACK.md)
- [Security notes](docs/SECURITY.md)
- [Control-plane installation (MHub, not integrated)](docs/INSTALLATION.md)
- [Problems solved and use cases](docs/USE_CASES.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Heterogeneous execution fabric](docs/HETEROGENEOUS_FABRIC.md)
- [Heterogeneous inference fabric: MRouter, KV-cache transfer, Metal, vLLM, and speculative decoding](docs/INFERENCE_FABRIC.md)
- [Mycelium and MCCL technical manuscript, formulas, architecture, market gap, and invention disclosure](docs/paper/OPENMYCELIUM_MYCELIUM_MCCL_IEEE_MANUSCRIPT.md)
- [Mycelium Lab and CPU/Gloo experiments](docs/MYCELIUM_LAB.md)
- [Implemented features and roadmap](FEATURES.md)
- [Authentication and security](AUTHENTICATION.md)
- [Changelog](CHANGELOG.md)

## Developer Edition: deploy anywhere

The package includes a Go control-plane service that embeds the dashboard and exposes authenticated APIs for discovery, logical accelerator pools, queues, workloads, lifecycle actions, access control, audit events, and observability.

- **Hardware discovery:** reads a host-agent report and probes available NVIDIA CUDA and AMD ROCm tooling; on macOS it identifies the Apple Silicon Metal/Core ML memory domain.
- **CPU operation:** model planning and Ollama inference remain available when no accelerator is detected. OpenMycelium never reports a simulated accelerator as physical capacity.
- **Persistent governance:** PostgreSQL stores accounts, sessions, clusters, pool policies, queues, integrations, audit events, and control-plane state.
- **Operations:** workload start, stop, redeploy, refresh, deletion, SSH command generation, local Ollama deployment, model chat, local-user administration, and service summaries are controlled from the dashboard.
- **Pre-hardware engineering:** Mycelium Lab persists virtual topologies, benchmark evidence, vendor image contracts, model/dataset sizing, communication simulations, qualification gates, and CPU/Gloo experiment manifests without presenting simulated GPUs as real capacity.

### Branded product installation

The product launcher is the recommended way to install and operate the local five-service stack. It verifies Docker, creates a secure `.env` when needed, assigns a persistent OpenMycelium Site ID, builds the product, displays stage percentages, waits for dependency readiness, and prints the control-plane, Prometheus, and Grafana launch addresses.

Windows PowerShell:

```powershell
cd C:\path\to\OpenMycelium-MVP
.\install.ps1 -Open
```

macOS or Linux:

```bash
cd /path/to/OpenMycelium-MVP
chmod +x install.sh
./install.sh --open
```

The first run generates strong local administrator, cluster-credential, metrics, Grafana, and agent secrets when `.env` does not exist. Existing `.env` files and persistent Docker volumes are preserved. The generated Site ID is available from both `/api/v1/health` and `/api/v1/ready`.

Lifecycle commands:

```powershell
.\install.ps1 -Action Status
.\install.ps1 -Action Restart
.\install.ps1 -Action Logs
.\install.ps1 -Action Stop
```

```bash
./install.sh status
./install.sh restart
./install.sh logs
./install.sh stop
```

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

The Developer Edition Compose stack now starts five services:

- `openmycelium`: dashboard, API, workload lifecycle, and Ollama proxy
- `postgres`: durable host and workload records
- `nats`: control-plane event transport for host and workload lifecycle events
- `prometheus`: authenticated OpenMycelium metric scraping, 15-day local retention, and alert evaluation
- `grafana`: provisioned Prometheus data source and the **OpenMycelium MLOps & AIOps** dashboard

Verify the stack after startup:

```powershell
docker compose ps
docker compose logs openmycelium
```

The control-plane logs should include `PostgreSQL persistence ready` and `NATS event bus ready`. PostgreSQL, NATS, Prometheus, and Grafana volumes retain data across normal restarts.

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

The control plane can connect to external Kubernetes APIs using an encrypted kubeconfig. Kubernetes workloads are created as `Deployment` plus `Service` resources for inference and interactive services, or `Job` resources for training and batch execution. Model caches use dynamically provisioned persistent volume claims. Every verified cluster refresh persists a node-by-node inventory covering readiness, schedulability, CPU, RAM, pods, operating system, container runtime, labels, taints, and extended accelerator resources.

### Workspaces and Celium AI+

**Celium AI+** is the governed launch flow for inference, training, fine-tuning, batch, interactive applications, and AI agents. A Workspace binds one organization boundary to an authenticated Kubernetes cluster and namespace, with declared queue, StorageClass, CPU/RAM/accelerator quotas, and network-policy profile. Selecting a Workspace makes its cluster and namespace authoritative during deployment.

The **Workspaces** menu reconciles live Kubernetes state into eight operational views: Overview, Deployments, Pods, Services & endpoints, Logs & terminal, Storage, Configuration, and Events & policies. Celium AI+ runs and YAML applications receive `openmycelium.io/workspace-id`, `openmycelium.io/release-id`, and `app.kubernetes.io/managed-by` labels. PostgreSQL stores their release records while the Kubernetes reconciler updates Celium AI+ status every 15 seconds; manifest controller state is reconciled during workspace refresh.

**Open application** prefers the direct NodePort or LoadBalancer address reported by live Kubernetes node discovery. ClusterIP-only applications use an authenticated, workspace-scoped Kubernetes Service proxy and remain sandboxed from the OpenMycelium control-plane origin. The current pod console supports bounded, non-interactive Kubernetes exec commands with command digests in the audit trail; it is not an unrestricted interactive shell. Logs are fetched directly from the selected pod, and Secret inventory exposes names and types only, never values.

### Multi-GPU sharing and gang scheduling

Accelerator pools can request a whole device or the exact extended resource exposed by a vendor device plugin. Examples include `nvidia.com/gpu`, `nvidia.com/mig-1g.10gb`, and `nvidia.com/gpu.shared`. OpenMycelium discovers these resources from node capacity and requests them without translating them into fictional fractional memory. NVIDIA MIG slices provide hardware memory and fault isolation; time-slicing and MPS share a physical GPU and do not create additional VRAM. The GPU Operator or another device-plugin administrator must configure those resources before a pool can become ready.

Celium AI+ exposes three scheduler backends:

- `kubernetes`: normal placement with per-pod and aggregate parallel-job capacity preflight
- `kueue`: labels an indexed parallel Job with its LocalQueue and creates it suspended for quota admission
- `volcano`: selects the Volcano scheduler and emits gang minimum and queue annotations used to create a PodGroup

Training, fine-tuning, and batch replica counts produce indexed Kubernetes Jobs with matching `parallelism` and `completions`. Multi-worker jobs also receive a headless rendezvous Service plus world-size, worker-index, and rendezvous environment contracts. Compact topology uses preferred pod affinity; spread topology uses a strict Kubernetes topology-spread constraint. RDMA and InfiniBand modes configure NCCL/UCX transport variables, but the accelerator-pool selector must still target nodes with the required NIC, driver, and network operator.

Example CLI configuration:

```powershell
python .\openmycelium.py pool add h100-mig `
  --vendor NVIDIA --runtime cuda --sharing-mode mig `
  --resource-name nvidia.com/mig-1g.10gb --slice-profile 1g.10gb

python .\openmycelium.py workload submit distributed-train `
  --kind training --cluster CLUSTER_ID --image REGISTRY/trainer:TAG `
  --pool h100-mig --accelerators 1 --replicas 4 `
  --scheduler volcano --queue research --gang-min 4 `
  --topology-mode compact --topology-key kubernetes.io/hostname `
  --network-mode rdma --cpu 8 --memory 64Gi
```

OpenMycelium rejects a Kueue or Volcano submission when the corresponding scheduler API is not installed. It also rejects strict gang requests when aggregate live CPU, RAM, or accelerator-slice capacity cannot accommodate the minimum member count.

### Mycelium heterogeneous execution planning

For the local NVIDIA + AMD inference alpha, the Scheduler can be inspected
before loading either GPU:

```powershell
.\openmycelium.cmd fabric list
.\openmycelium.cmd plan --model mistral-nemo --output /opt/openmycelium/placement.json
.\openmycelium.cmd chat --model mistral-nemo
```

`plan` issues one digest-protected placement with exact model ownership and
stable Fabric identities. `run` and `chat` issue the same contract internally;
both workers validate it and do not independently choose a split. This is
aggregated schedulable capacity across separate CUDA and ROCm memory spaces,
not coherent or unified VRAM.

The **Memory & execution fabric** dashboard qualifies each connected node for RDMA, native collectives, GPUDirect or DirectGMA evidence, and optional cross-vendor transport adapters. PostgreSQL stores benchmark profiles and compiled execution plans. **Mycelium 1.0** groups available devices by vendor/runtime/model, performs memory-constrained minimax layer placement, evaluates admissible data/pipeline/ZeRO candidates, and scores throughput, balance, or performance-per-watt objectives. The persisted contract includes profile coverage, stage imbalance, memory feasibility, and an explainable decision trace.

Within one vendor, the contract selects NCCL, RCCL, or oneCCL. Cross-vendor direct plans are blocked unless every selected node advertises RDMA and qualified native adapters. `gloo` remains an explicit PyTorch host-staging fallback. The installable `runtime/mccl` package adds a sequence-safe TCP AllReduce coordinator, device discovery, transport planning, a PyTorch bridge, build-gated host/CUDA/ROCm adapter implementations, and a 0.2 reference inference fabric with MRouter planning, canonical KV-cache transfer, Metal staging, vLLM endpoint discovery, and speculative decoding. Its portable paths stage data through host memory; device-direct RDMA is still blocked pending physical qualification. A ready plan can be selected in Celium AI+ or passed with `--execution-plan`. Training, fine-tuning, and batch plans expand into indexed Jobs per vendor group, with exact extended-resource requests, qualified-node constraints, shared rendezvous metadata, and non-overlapping rank bases. The standalone inference adapters are functional, while the Kubernetes controller continues to reject multi-group inference until model-specific worker codecs and release reconciliation are connected. See [Mycelium platform layers](docs/MYCELIUM_PLATFORM_LAYERS.md), [Mycelium algorithm](docs/MYCELIUM_ALGORITHM.md), [MCCL runtime](docs/MCCL_RUNTIME.md), [Heterogeneous inference fabric](docs/INFERENCE_FABRIC.md), and [Heterogeneous execution fabric](docs/HETEROGENEOUS_FABRIC.md).

### Vagrant k3s laptop integration

For the included Vagrant k3s topology, create a least-privilege OpenMycelium service account and generate a dedicated kubeconfig. The helper applies `k8s/remote-access.yaml`, requests a bounded service-account token, embeds the cluster CA, writes a git-ignored kubeconfig, and places it on the clipboard.

Rebuild OpenMycelium once so PostgreSQL receives the cluster-inventory migration:

```powershell
docker compose up --build -d
```

Run this from the OpenMycelium project directory:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\agents\windows\connect-vagrant-k3s.ps1 `
  -VagrantDirectory "C:\path\to\k8s-vagrant-lite"
```

Open **Clusters > Connect cluster**, enter `laptop-k3s`, leave the endpoint blank, and paste the generated kubeconfig. Use `openmycelium-workloads` as the namespace and `local-path` as the k3s StorageClass. Connection is rejected unless authentication and node listing succeed. The **Verify** control refreshes node readiness, version, and Kubernetes extended accelerator resources directly from the API.

Open **Workspaces** and use **Celium AI+** to deploy into the cluster's governed namespace. The controller subtracts active pod requests from each eligible node, validates taints and pool selectors, and records its placement candidate before creating resources. Kubernetes remains the final scheduler; OpenMycelium records the selected pod and node, continuously reconciles lifecycle state, and exposes diagnostics, logs, events, generated manifests, service endpoints, and persistent-storage status.

The **Clusters > Cluster nodes** panel is the live capacity view. It shows allocatable and currently available CPU/RAM, pod occupancy, node runtime, and vendor accelerator inventory. **Accelerator pools** aggregates that inventory against each pool selector so unavailable or saturated pools are visible before submission.

For inference on the CPU-only Vagrant workers, use an image available to containerd, request realistic CPU/RAM, set accelerators to `0`, and choose `NodePort` when the service must be reachable from Windows. An Ollama model name automatically configures port `11434` and mounts the model PVC at `/root/.ollama`.

### Container images, internet access, and YAML

Open **Containers & manifests** after connecting a cluster. **Deploy container** opens the governed workload form for public or private images. Public image names such as `nginx:alpine`, `ollama/ollama:latest`, or a versioned GHCR image are pulled by the Kubernetes node's container runtime. OpenMycelium does not proxy image bytes or bypass the cluster network: worker DNS, default routes, firewalls, HTTP proxies, registry allowlists, and Kubernetes `NetworkPolicy` still control internet access.

For a private registry, create a pull secret in the target namespace, then enter only that secret name in **Private registry secret**:

```powershell
kubectl -n openmycelium-workloads create secret docker-registry registry-credentials `
  --docker-server=registry.example.com `
  --docker-username=YOUR_USER `
  --docker-password=YOUR_TOKEN
```

Do not paste registry passwords into the workload image field or YAML editor. The controller adds the existing secret as `imagePullSecrets`; credentials remain in Kubernetes.

The YAML editor accepts up to 25 namespaced resources and 1 MiB per request. **Preview & validate** performs OpenMycelium policy checks and a Kubernetes server dry-run. **Apply to cluster** uses server-side apply only when the cluster, namespace, and YAML still match the successful preview. Cluster-scoped resources and unsafe pod settings such as privileged mode, host namespaces, `hostPath`, `hostPort`, added capabilities, and privilege escalation are rejected. Secrets may be applied, but their raw manifest is never copied into the audit event.

```powershell
python .\openmycelium.py manifest preview --cluster CLUSTER_ID --namespace openmycelium-workloads --file .\app.yaml
python .\openmycelium.py manifest apply --cluster CLUSTER_ID --namespace openmycelium-workloads --file .\app.yaml
```

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
python .\openmycelium.py cluster add laptop-k3s --kubeconfig .\.openmycelium.kubeconfig --storage-class local-path
python .\openmycelium.py cluster list
python .\openmycelium.py cluster nodes CLUSTER_ID
python .\openmycelium.py pool add cpu-local --runtime cpu
python .\openmycelium.py fabric capabilities --cluster CLUSTER_ID --device-memory-gib 24
python .\openmycelium.py fabric plan model-weights --cluster CLUSTER_ID --tensor weights --size-gib 40 --strategy shard --consistency immutable --device-memory-gib 24
python .\openmycelium.py fabric list
python .\openmycelium.py fabric qualification --cluster CLUSTER_ID
python .\openmycelium.py fabric profile-add a100-profile --cluster CLUSTER_ID --vendor nvidia --runtime cuda --model "A100 80GB" --tokens-per-second 300 --memory-gib 80 --source measured
python .\openmycelium.py fabric execution-plan mixed-train --cluster CLUSTER_ID --model custom-12b --parameters-b 12 --layers 48 --global-batch 16 --allow-cpu-fallback --dynamic-microbatch --objective balanced --nvidia-image REGISTRY/trainer:cuda --amd-image REGISTRY/trainer:rocm
python .\openmycelium.py queue add research --priority 60 --memory-gb 32
python .\openmycelium.py user add operator@example.com --role operator
python .\openmycelium.py workload submit gemma-k3s --kind inference --cluster CLUSTER_ID --model gemma4:12b --cpu 4 --memory 10Gi --storage-gb 20 --service-type NodePort --port 11434 --fabric-plan FABRIC_PLAN_ID --execution-plan EXECUTION_PLAN_ID
python .\openmycelium.py workload logs JOB_ID
python .\openmycelium.py workload events JOB_ID
python .\openmycelium.py workload diagnostics JOB_ID
python .\openmycelium.py workload service JOB_ID
python .\openmycelium.py workload manifest JOB_ID
python .\openmycelium.py workload storage JOB_ID
python .\openmycelium.py workload exec JOB_ID "uname -a"
python .\openmycelium.py workload probe JOB_ID --path /
python .\openmycelium.py workload delete JOB_ID
python .\openmycelium.py model catalog
python .\openmycelium.py model sync-ollama
python .\openmycelium.py model list
python .\openmycelium.py model generate gemma4:12b "Reply with one concise sentence."
python .\openmycelium.py observability summary
python .\openmycelium.py audit list
```

### MLOps, AIOps, Prometheus, and Grafana

The authenticated **MLOps** dashboard correlates governed model artifacts and versions with Celium AI+ workloads, workspace releases, runtime adoption, restarts, and lifecycle health. The **AIOps** dashboard derives actionable alerts from reconciled workload failures, restart pressure, dependency connectivity, and Kubernetes readiness; it also reports active sessions, role-scoped user usage, and recent audit activity. These views use persisted OpenMycelium and Kubernetes state and do not invent GPU utilization samples.

Prometheus metrics are exposed at `/metrics`. Compose securely writes `METRICS_TOKEN` into an ephemeral credentials file for Prometheus and provisions alert rules for failed workloads, dependency outages, non-ready nodes, restart growth, and scrape failure. Set the following values in `.env` before deployment:

```dotenv
METRICS_TOKEN=replace-with-a-long-random-metrics-token
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=replace-with-a-long-unique-grafana-password
PROMETHEUS_PUBLIC_URL=http://127.0.0.1:9090
GRAFANA_PUBLIC_URL=http://127.0.0.1:3000
```

After `docker compose up --build -d`, open:

- OpenMycelium MLOps/AIOps: `http://127.0.0.1:8081`
- Prometheus: `http://127.0.0.1:9090`
- Grafana: `http://127.0.0.1:3000`

The Grafana dashboard is provisioned from `observability/grafana/dashboards/openmycelium-operations.json`. Prometheus configuration and alert rules live in `observability/prometheus`. For a direct metrics check:

```powershell
Invoke-WebRequest -Uri http://127.0.0.1:8081/metrics -Headers @{ Authorization = "Bearer $env:METRICS_TOKEN" }
```

### Agentic AI orchestration

**Agent Hub** is the organization catalog for versioned agent definitions, framework/runtime contracts, reusable flows, MCP/API integrations, and A2A discovery metadata. Actual execution is workspace-scoped. Choose **Workspaces**, select a Kubernetes workspace, then use the **Agents**, **Flows**, **Runs**, **Tools & MCP**, **Memory**, **Approvals**, **Evaluations**, and **Traces** tabs.

An agent definition requires a registry-accessible OCI image. A run creates a normal OpenMycelium workload and workspace release, then deploys a Kubernetes `Deployment` and `Service` with `openmycelium.io/agent-id` and `openmycelium.io/agent-run-id` labels. Each runtime receives the immutable agent specification, flow, and approved tool bindings through `OPENMYCELIUM_AGENT_*` environment variables. Agent pods use a dedicated ServiceAccount and do not automatically mount a Kubernetes API token.

Agents with **Require operator approval** enabled create a durable pending approval before any Kubernetes resource is deployed. Approve or reject the request from the workspace **Approvals** tab or CLI. Run state and traces are persisted in PostgreSQL; lifecycle events are also retained in the `OPENMYCELIUM_AGENT_EVENTS` NATS JetStream stream for replay and external consumers.

Build and publish the included reference runtime to a registry reachable by the cluster:

```powershell
docker build -t YOUR_REGISTRY/openmycelium-agent-runtime:0.1.0 .\agents\runtime
docker push YOUR_REGISTRY/openmycelium-agent-runtime:0.1.0
```

The reference runtime exposes `/health`, `/v1/runtime`, `/.well-known/agent.json`, and `/v1/run`. Set `MODEL_BASE_URL`, `MODEL_NAME`, and optionally `MODEL_API_KEY` in your own derived image or workload configuration to call an OpenAI-compatible model endpoint. Production agents can use any framework as long as their image exposes the configured HTTP port and `/health` endpoint.

CLI examples:

```powershell
python .\openmycelium.py agent list --workspace WORKSPACE_ID
python .\openmycelium.py agent create support-agent --workspace WORKSPACE_ID --framework langgraph --image YOUR_REGISTRY/support-agent:1.0.0 --require-approval
python .\openmycelium.py agent flow-create triage --workspace WORKSPACE_ID --file .\flow.json
python .\openmycelium.py agent run --workspace WORKSPACE_ID --agent AGENT_ID --input '{"message":"Investigate the failed service."}'
python .\openmycelium.py agent approvals --workspace WORKSPACE_ID
python .\openmycelium.py agent approve APPROVAL_ID
python .\openmycelium.py agent trace RUN_ID
python .\openmycelium.py agent cancel RUN_ID
```

The current orchestration engine is PostgreSQL plus JetStream: it provides durable definitions, admission, approval, Kubernetes release, lifecycle reconciliation, cancellation, and replayable traces. A Temporal adapter remains the planned engine for multi-day timers, compensation and resumable multi-agent graph execution. MCP bindings and A2A Agent Cards are governed now; remote MCP tool execution and full A2A task transport still require their credentialed gateways and conformance testing.

For Helm or external Prometheus deployments, configure the scrape job or ServiceMonitor to read the bearer token from a Kubernetes Secret. The Helm chart does not add unauthenticated scrape annotations by default.

### Windows host discovery with Docker

Docker Desktop runs Linux containers inside a VM, so a container cannot reliably inspect Windows CPU, RAM, or display adapters directly. Run the included read-only Windows host agent after the Docker control plane is listening:

```powershell
.\agents\windows\discover.ps1 -ControlPlane http://127.0.0.1:8081
```

The script prompts for an operator account. For unattended discovery, set `OPENMYCELIUM_AGENT_TOKEN` to the same long random value configured as `AGENT_TOKEN` in `.env`. It uses Windows CIM to report the CPU, logical cores, physical RAM, and display adapters into the local control plane. Refresh the dashboard, then choose **Discovery & install** and select **Scan hardware**.

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

### Kubernetes workload operations

Kubernetes workloads use the Kubernetes API rather than node SSH. **Inspect** opens a workload workspace combining placement decisions, pod phase, container waiting/termination reasons, restarts, endpoint state, and related events. The same workspace exposes current or previous logs, events, Service coordinates, PVC state, generated manifests, an API-proxied Service probe, and audited non-interactive commands inside the workload container. Start and stop scale services, redeploy replaces controller-owned resources, and delete removes the workload, Service, and model PVC. Legacy SSH metadata remains available through the API only for non-Kubernetes records.

## CLI and host diagnostics

The branded installer above runs the complete product stack. Python 3.10+ is required only for the standalone CLI, MCP server, and diagnostic agent commands below.

```powershell
cd C:\path\to\OpenMycelium-MVP
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

## Hypha memory fabric

The **Memory fabric** workspace compiles persistent, topology-aware tensor plans from verified Kubernetes inventory. A plan has a global `hypha://` logical address, explicit consistency mode, physical shard or replica placements, transfer backends, reserve policy, physical footprint, and warnings. Plans can use discovered per-device memory labels or a recorded operator assumption and may optionally include available host RAM as a slower spill tier.

Supported planning semantics are:

- `immutable`: model weights and read-only lookup data
- `single-writer`: KV cache, activations, and owner-managed state
- `reduce`: gradients that require staged collective reduction
- `transactional`: checkpoint or metadata updates committed at synchronization boundaries

Attaching a plan to a Kubernetes workload adds `hypha.openmycelium.io/*` pod annotations and self-contained `OPENMYCELIUM_FABRIC_*` environment variables, including the validated plan JSON. This is a functional placement and execution contract for fabric-aware containers without giving them control-plane credentials. The current release does not intercept CUDA, HIP, Level Zero, or Metal memory calls and does not claim hardware-coherent cross-vendor pointers. A native worker runtime, compiler dialect, transport implementations, and framework storage adapters remain required before arbitrary application tensors can move automatically according to the plan.

To report GPU memory without an operator assumption, label each applicable node with per-device memory:

```powershell
kubectl label node WORKER_NAME accelerator.openmycelium.io/memory-gib=24
```

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
  --set-string secrets.clusterCredentialKey='replace-with-32-plus-random-characters' `
  --set-string secrets.metricsToken='replace-me' `
  --set-string secrets.agentToken='replace-me'
```

## Architecture choice

The architecture is self-hosted and Kubernetes-oriented: a Helm-installed control plane, PostgreSQL system of record, NATS event transport, local or in-cluster discovery agents, and runtime adapters. Logical pools coordinate placement across vendors; they do not combine physically separate VRAM into hardware unified memory.
