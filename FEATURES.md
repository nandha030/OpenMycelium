# OpenMycelium capability matrix

Four states, not two. Code existing is not the same as code that has ever
driven the hardware, and collapsing those into "available now" is how a
capability matrix stops being useful.

| State | Meaning |
|---|---|
| **Hardware-qualified** | Runs on real NVIDIA + AMD hardware, with recorded evidence in `release/` |
| **Integrated** | Invokes the real Node Runtime, but has not been qualified on hardware |
| **Implemented** | Code exists and is tested, but has never invoked the Node Runtime |
| **Roadmap** | Design or partial code. Not a feature. Do not present it as one |

The product has two real components today:

- **OpenMycelium Node Runtime** — the Python dual-vendor execution engine. Hardware-qualified.
- **MHub Control Plane** — the Go platform: users, clusters, policies, queues, scheduling, Kubernetes, fleet. Implemented, **not integrated**.

---

## Hardware-qualified

Validated on Windows 11 + WSL2, RTX 5060 Ti 16 GB, RX 9060 XT 16 GB, and
Mistral-Nemo-Instruct-2407. Evidence under `release/0.1.0*/`.

- Single-node inference with one model split across one NVIDIA CUDA GPU and one AMD ROCm GPU: 22.84 GiB checkpoint on two 16 GiB cards
- 181/182 exclusive tensor ownership, 363 total, zero overlap, boundary after layer 19
- Byte-exact host-staged cross-vendor activation transfer, SHA-256 verified either side of the boundary
- Greedy decode: TTFT 142.09 ms median, 11.09 tok/s decode, 10.95 tok/s end-to-end over five independent sessions
- OpenAI-compatible API on port 11500, verified with the OpenAI Python SDK 3.5.0 and Open WebUI v0.11.1
- Local operator console on port 11501: readiness, Fabric, Models, Plan, one dual-GPU Run with streamed events, safe Stop
- Provisioning both GPU environments from zero, each proved with a real BF16 matmul on its own card
- Idempotent provisioning: rerun changes no environment fingerprint
- Survives a WSL shutdown and restart, requalifying both GPUs
- Accelerator discovery with stable identity: NVIDIA by UUID, AMD by PCI location
- Placement manifests with digest, and an audit gate that refuses tampering, wrong run ids, replayed sequences and impostor writers
- Clean bootstrap on a fresh WSL distribution, with the documented manual AMD prerequisite
- Offline installation of all 90 Python wheels from a SHA-256-verified wheelhouse
- Model import and shard verification against declared lengths

## Integrated

**Nothing yet.**

No part of the MHub control plane has ever invoked the Node Runtime. There is
no `.go` file referencing `openmycelium run`, `serve`, `plan`, `provision` or
`console`, nor `pipeline_run`, nor ports 11500 or 11501. Closing this gap — an
authenticated Node Agent API, and one end-to-end execution through it — is the
next product milestone.

## Implemented, not integrated

Real code with tests, in the Go control plane and its dashboard. None of it has
executed a workload on the qualified runtime. Treat every item as unproven
against hardware.

**Identity and governance**
- Local account bootstrap, admin-created users, bcrypt hashing, revocable server-side sessions, signup policy, admin/operator/viewer RBAC, self and last-admin protection
- PostgreSQL-backed users, sessions, clusters, accelerator-pool policies, queues, quotas, integrations, settings and audit events
- Queue priority, accelerator quota, memory quota and preemption policy records
- MCP/API integration registry with removal and audit history

**Fleet and inventory**
- Authenticated Kubernetes connections with encrypted kubeconfig storage, persistent node inventory, live readiness and schedulability
- Verified cluster registration and a refreshed pending/ready/degraded inventory model
- Dynamic accelerator-pool capacity from discovered nodes: total, allocatable, available, unavailable, saturated
- Accelerator allocation profiles for exclusive GPUs, NVIDIA MIG, time-slicing, MPS and vendor device-plugin slices
- Windows CPU, RAM and display-adapter discovery through the host agent
- Local NVIDIA and AMD command-line discovery when vendor tools are visible to the control plane
- Local Ollama model discovery, managed workload registration, inference proxy and chat

**Workloads and Kubernetes**
- Workspaces binding organization, cluster, namespace, queue, StorageClass, quotas and policy
- Celium AI+ launch flow for inference, training, fine-tuning, batch, interactive applications and agents
- Kubernetes `Deployment`/`Service` rendering, indexed parallel training and batch Jobs
- Gang-capacity preflight, Kueue LocalQueue admission and Volcano gang scheduling contracts
- Topology placement, PriorityClass selection, headless rendezvous Services, worker index and world-size contracts
- Lifecycle reconciliation with placement decisions, pod diagnostics, restart and failure reasons
- Governed namespaced YAML preview and server-side apply with allowlisting, dry-run validation and manifest digests
- Registry image deployment with existing `imagePullSecret` references and cluster-governed egress
- Audited container workspace: non-interactive pod exec, Service probes, PVC state, logs, events

**Models and planning**
- Versioned model repository with Ollama sync and Hugging Face / OCI / object-storage / NFS / HTTPS / local source references
- PVC-backed model storage including an Ollama runtime cache path
- CPU-only model planning for dense and MoE models: quantization, KV cache, batch, context, headroom, approximate FLOPs
- Deployment planning for inference, LoRA and full training with device presets and tensor/pipeline parallelism
- Accelerator benchmark profile storage: model, precision, throughput, memory, P2P, all-reduce
- Mycelium 1.0 optimization: memory-constrained minimax layer allocation, data/pipeline/ZeRO candidate scoring
- Mycelium Lab with isolated virtual NVIDIA/AMD/Intel/Apple/CPU topologies and a planning benchmark catalogue

**Observability and deployment**
- Live control-plane summary, authenticated or bearer-token Prometheus metrics
- MLOps and AIOps dashboards, bounded-label metrics, provisioned alert rules, Grafana data source and dashboards
- Docker Compose deployment with PostgreSQL, NATS, Prometheus and Grafana; hardened Helm chart
- Branded Windows, macOS and Linux Docker installers with first-run configuration and persistent Site ID
- Dashboard with authenticated navigation, dual-tone visual system and compute-topology canvas
- Session-aware Python CLI for cluster connection, node inventory and workload lifecycle

**MCCL package (Python)**
- Installable MCCL alpha: cross-platform discovery, sequence-safe typed TCP AllReduce, checksummed KV-cache transfer, MRouter planning, Metal staging, vLLM endpoint discovery, speculative decoding
- Native NCCL/RCCL/oneCCL selection with strict device-direct gates and an explicit Gloo host-staging fallback
- PyTorch Gloo CPU-forwarding reference adapter with pinned host buffers and global rank normalization

Only the host-staged cross-vendor path in this package is hardware-qualified.
The device-direct and native-collective paths are not.

## Roadmap and experimental

Isolated on purpose. **Do not present any of this as a release feature.**

- Hypha native distributed-memory data plane, MLIR dialect, RDMA/UCX kernels
- Cross-vendor training and fine-tuning
- Multi-node MCCL and RDMA; device-direct collectives
- Agent Hub, MCP broker and A2A task gateway
- Apple, Intel, NPU and TPU execution
- Energy-aware scheduling
- Advanced Kubernetes gang scheduling beyond the current contracts
- Failure recovery and checkpoint migration
- Signed agents with enrollment tokens, rotation and remote upgrade
- OIDC, Entra ID group mapping, LDAP, SCIM, organizations and teams
- OpenTelemetry traces, log aggregation, long-term utilization, cost ingestion
- Temporal workflow adapter
- Encrypted secret storage via KMS
- PostgreSQL backup/restore automation, migration versioning, HA, upgrade rollback
- API pagination, idempotency keys, optimistic concurrency, rate limits, signed agent APIs
- Signed native installers and packaged CLI binaries

## Platform truth

OpenMycelium can manage heterogeneous devices as one scheduling inventory and
split compatible work across separate workers. It **cannot** turn NVIDIA, AMD,
Intel, Apple, NPU, TPU, local and cloud memory into one hardware-coherent
unified-memory pool.

What v0.1.0 proves is narrower still: **aggregated capacity across two GPUs in
one machine**. A 22.84 GiB model runs on two 16 GiB cards because the layers
are split and each stage keeps its own memory space. There is no shared address
space and no page migration. A tensor that does not fit on one card still does
not fit.

One NVIDIA GPU and one AMD GPU per pipeline are qualified. Multi-AMD identity
and scheduling are **not** qualified: the ledger records the AMD PCI bus number
rather than the full BDF, which cannot distinguish multiple functions or
devices sharing a bus topology.
