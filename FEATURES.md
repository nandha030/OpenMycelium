# OpenMycelium capability matrix

This document distinguishes working product capabilities from adapters that still require engineering and hardware validation. A configured endpoint is not represented as a live integration until OpenMycelium has verified it.

## Available now

- Premium responsive dashboard with authenticated navigation and truthful empty states
- Persistent light/dark dual-tone visual system with live compute-topology canvas
- Local account bootstrap, admin-created users, bcrypt password hashing, revocable server-side sessions, account deletion, signup policy, admin/operator/viewer RBAC, and self/last-admin protection
- PostgreSQL-backed users, sessions, clusters, accelerator-pool policies, queues, quotas, integrations, settings, and audit events
- Authenticated Kubernetes connections with encrypted kubeconfig storage, persistent node inventory, live readiness, schedulability, CPU/RAM/pod availability, labels, taints, runtime metadata, extended accelerator resources, and a least-privilege Vagrant/k3s bootstrap
- PostgreSQL-backed Workspaces that bind organization, authenticated cluster, namespace, queue, StorageClass, declared quotas, and policy profile into one governed application boundary
- Celium AI+ launch flow for inference, training, fine-tuning, batch, interactive applications, and AI agents, with workspace/release labels and reconciled release records
- Unified Workspace inventory for controllers, pods, Services and endpoints, logs, audited pod commands, PVCs, redacted configuration inventory, events, policies, node capacity, and release history
- Authenticated workspace-scoped Kubernetes Service gateway for opening ClusterIP applications without direct worker SSH or NodePort exposure
- Dynamic accelerator-pool capacity calculated from discovered nodes, including total, allocatable, available, unavailable, saturated, and selector-matched states
- Explicit accelerator allocation profiles for exclusive GPUs, NVIDIA MIG, time-slicing, MPS, and vendor device-plugin slices, using the exact Kubernetes extended resource advertised by the cluster
- Hypha distributed tensor planner with persistent logical addresses, sharding, replication, explicit consistency modes, GPU-memory assumptions or labels, host spill tiers, capacity rejection, mixed-vendor warnings, dashboard controls, CLI commands, and Kubernetes workload annotations
- Real Kubernetes workload deployment: inference and interactive `Deployment`/`Service` resources, indexed parallel training and batch `Job` resources, namespace creation, CPU/RAM/accelerator requests, selectors, and scheduler preflight that subtracts active pod requests
- Aggregate gang-capacity preflight plus Kueue LocalQueue admission and Volcano gang scheduling contracts, with backend API discovery before resource creation
- Compact or spread topology placement, PriorityClass selection, headless rendezvous Services, worker index/world-size contracts, and RDMA/InfiniBand runtime transport settings for distributed workloads
- Kubernetes lifecycle reconciliation with placement decisions, pod/container diagnostics, restart and failure reasons, start, stop, redeploy, deletion, current/previous logs, events, generated manifests, and ClusterIP/NodePort/LoadBalancer endpoint reporting
- Audited Kubernetes container workspace with non-interactive pod exec, Service probes, PVC state, logs, events, diagnostics, and generated manifests
- Governed namespaced YAML preview and server-side apply with resource allowlisting, dry-run validation, manifest digests, and privileged/host-access policy rejection
- Public and private registry image deployment with existing Kubernetes `imagePullSecret` references and explicit cluster-governed egress behavior
- Versioned model repository with Ollama synchronization, Hugging Face/OCI/object-storage/NFS/HTTPS/local source references, runtime contracts, provenance metadata, and workload selection
- PVC-backed model storage, including an Ollama runtime path that downloads the selected model into a persistent cache and exposes the native Ollama API
- Windows CPU, RAM, and display-adapter discovery through the included host agent
- Branded Windows, macOS, and Linux Docker installers with secure first-run configuration, persistent Site ID, staged progress, readiness checks, lifecycle commands, diagnostic logs, and optional browser launch
- Local NVIDIA and AMD command-line discovery when vendor tools are visible to the control-plane process
- Apple Silicon Metal/Core ML domain identification when running natively on macOS
- CPU-only model planning for dense and MoE models, quantization, KV cache, batch size, context, memory headroom, and approximate FLOPs per token
- Advanced deployment planning for inference, LoRA, and full training with device presets, tensor/pipeline parallelism, bandwidth/compute ceilings, CPU offload, concurrency, and per-device fit
- Local Ollama model discovery, managed workload registration, inference proxy, and live chat
- Workload submission with cluster, namespace, type, image/model, command, pool, CPU, memory, accelerators, replicas, storage, port, and service exposure
- Logical multi-vendor placement policies, with an explicit warning that separate device memories are not merged
- Queue priority, accelerator quota, memory quota, and preemption policy records
- Verified cluster registration and continuously refreshed pending/ready/degraded inventory model
- MCP/API integration registry with removal and audit history
- Organization and workspace-scoped Agent Hub with versioned OCI runtime contracts for generic, LangGraph, OpenAI Agents SDK, Semantic Kernel, AutoGen, and CrewAI workloads
- Persistent agent flows, runs, run events, human approval gates, least-privilege tool bindings, memory profiles, evaluation results, and A2A 1.0 Agent Card metadata
- Real Kubernetes agent releases with dedicated service accounts, disabled automatic API-token mounts, workspace/release/agent/run labels, health probes, resource limits, model contracts, approved-tool contracts, cancellation, pod inspection, logs, service endpoints, and audit history
- Durable agent event streaming through the existing NATS JetStream deployment, with PostgreSQL as the agent system of record and replayable workspace trace views
- Agent CLI commands for definitions, flow import, runs, cancellation, approvals, traces, tools, and memory inventory
- Reference OCI agent runtime with health, runtime-contract, A2A metadata, and OpenAI-compatible model execution endpoints
- Live control-plane summary plus authenticated or bearer-token Prometheus metrics
- MLOps dashboard correlating models, model versions, storage, runtime adoption, Celium AI+ workloads, workspace releases, failures, and restarts
- AIOps dashboard with dependency probes, derived alerts, cluster readiness, active sessions, role-scoped user usage, and recent audit activity
- Expanded bounded-label Prometheus metrics for workloads, runtimes, models, storage, releases, users, sessions, audit activity, clusters, restarts, and dependency health
- Provisioned Prometheus alert rules and Grafana data source/dashboard with persistent Docker volumes
- Docker Compose deployment with PostgreSQL, NATS, Prometheus, and Grafana
- Hardened Kubernetes Helm chart for the control plane
- Session-aware, dependency-free Python CLI for cluster connection and node inventory, Kubernetes workload submission/lifecycle/diagnostics/logs/events/services/manifests/storage, discovery, Ollama, observability, user administration, and audit

## Engineering milestones required for production operation

- Signed Windows, macOS, and Linux agents with enrollment tokens, rotation, heartbeats, and remote upgrade
- Kubernetes agent image and reconciler that update node, device, health, and cluster-ready state
- NVIDIA DCGM/NVML, AMD ROCm SMI, Intel Level Zero/oneAPI, Apple Metal, NPU, and TPU telemetry adapters validated on real hardware
- Runtime-aware scheduler adapters for Ray, Slurm, JobSet, Kubeflow Training Operator, LeaderWorkerSet, and automated vendor device-plugin/operator configuration
- Hypha native data-plane workers, MLIR dialect, CUDA/HIP/Level Zero/Metal transfer backends, UCX/RDMA transport, framework tensor-storage adapters, checkpoint recovery, and benchmark-driven automatic execution
- Distributed training orchestration with framework-specific checkpoint, topology, precision, and failure-recovery contracts
- vLLM, llama.cpp, TensorRT-LLM, TGI, MLX, OpenAI, Anthropic, and cloud GPU runtime adapters
- OIDC discovery/login, Microsoft Entra ID group mapping, LDAP bind/search, SCIM provisioning, organizations, teams, and scoped service accounts
- OpenTelemetry traces, centralized log aggregation, long-term utilization history, energy data, cloud cost ingestion, Alertmanager routing, and notification integrations
- Temporal workflow-engine adapter for multi-day timers, resumable graph execution, cross-agent compensation, and distributed human tasks; the current release uses PostgreSQL state plus JetStream events
- Executable MCP broker and A2A task gateway with credential exchange, streaming, consent prompts, and protocol conformance testing; the current release governs bindings and Agent Card metadata
- Encrypted secret storage using Kubernetes Secrets plus an external KMS or secret manager
- PostgreSQL backup/restore automation, schema migration versioning, disaster-recovery drills, HA topology, and upgrade rollback
- API pagination, idempotency keys, optimistic concurrency, rate limits, signed agent APIs, CSRF protection, and full end-to-end security testing
- Signed native Windows/macOS/Linux installers and packaged CLI binaries

## Platform truth

OpenMycelium can manage heterogeneous devices as one scheduling inventory and split compatible work across separate workers. It cannot turn NVIDIA, AMD, Intel, Apple, NPU, TPU, local, and cloud memory into one hardware-coherent unified-memory pool. Every workload adapter must honor the memory, runtime, topology, and communication limits of the hardware it actually uses.
