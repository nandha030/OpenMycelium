# OpenMycelium capability matrix

This document distinguishes working product capabilities from adapters that still require engineering and hardware validation. A configured endpoint is not represented as a live integration until OpenMycelium has verified it.

## Available now

- Premium responsive dashboard with authenticated navigation and truthful empty states
- Persistent light/dark dual-tone visual system with live compute-topology canvas
- Local account bootstrap, admin-created users, bcrypt password hashing, revocable server-side sessions, account deletion, signup policy, admin/operator/viewer RBAC, and self/last-admin protection
- PostgreSQL-backed users, sessions, clusters, accelerator-pool policies, queues, quotas, integrations, settings, and audit events
- Authenticated Kubernetes inventory check-in with node readiness, version, accelerator totals, audit events, and a Windows kubeconfig bridge for local k3s/Vagrant clusters
- Windows CPU, RAM, and display-adapter discovery through the included host agent
- Local NVIDIA and AMD command-line discovery when vendor tools are visible to the control-plane process
- Apple Silicon Metal/Core ML domain identification when running natively on macOS
- CPU-only model planning for dense and MoE models, quantization, KV cache, batch size, context, memory headroom, and approximate FLOPs per token
- Advanced deployment planning for inference, LoRA, and full training with device presets, tensor/pipeline parallelism, bandwidth/compute ceilings, CPU offload, concurrency, and per-device fit
- Ollama model discovery, managed workload registration, lifecycle and deletion controls, validated SSH connection metadata/commands, inference proxy, and live chat
- Workload submission with type, image, pool, and requested accelerator count
- Logical multi-vendor placement policies, with an explicit warning that separate device memories are not merged
- Queue priority, accelerator quota, memory quota, and preemption policy records
- Cluster registration and pending/ready inventory model
- MCP/API integration registry with removal and audit history
- Live control-plane summary plus authenticated or bearer-token Prometheus metrics
- Docker Compose deployment with PostgreSQL and NATS
- Hardened Kubernetes Helm chart for the control plane
- Session-aware, dependency-free Python CLI for discovery, infrastructure resources, workload lifecycle/deletion/SSH, Ollama, observability, user administration, and audit

## Engineering milestones required for production operation

- Signed Windows, macOS, and Linux agents with enrollment tokens, rotation, heartbeats, and remote upgrade
- Kubernetes agent image and reconciler that update node, device, health, and cluster-ready state
- NVIDIA DCGM/NVML, AMD ROCm SMI, Intel Level Zero/oneAPI, Apple Metal, NPU, and TPU telemetry adapters validated on real hardware
- Runtime-aware scheduler integration with Kubernetes Kueue, Volcano, Ray, Slurm, and device plugins
- Distributed training orchestration with framework-specific checkpoint, topology, precision, and failure-recovery contracts
- vLLM, llama.cpp, TensorRT-LLM, TGI, MLX, OpenAI, Anthropic, and cloud GPU runtime adapters
- OIDC discovery/login, Microsoft Entra ID group mapping, LDAP bind/search, SCIM provisioning, organizations, teams, and scoped service accounts
- OpenTelemetry traces, centralized logs, alert rules, utilization history, energy data, and cloud cost ingestion
- Encrypted secret storage using Kubernetes Secrets plus an external KMS or secret manager
- PostgreSQL backup/restore automation, schema migration versioning, disaster-recovery drills, HA topology, and upgrade rollback
- API pagination, idempotency keys, optimistic concurrency, rate limits, signed agent APIs, CSRF protection, and full end-to-end security testing
- Signed native Windows/macOS/Linux installers and packaged CLI binaries

## Platform truth

OpenMycelium can manage heterogeneous devices as one scheduling inventory and split compatible work across separate workers. It cannot turn NVIDIA, AMD, Intel, Apple, NPU, TPU, local, and cloud memory into one hardware-coherent unified-memory pool. Every workload adapter must honor the memory, runtime, topology, and communication limits of the hardware it actually uses.
