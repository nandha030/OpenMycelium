# Changelog

## Mycelium Lab

- Added persistent virtual topologies, planning benchmark profiles, runtime image contracts, model and dataset sizing, communication estimates, qualification gates, and saved experiment evidence.
- Added a CPU/Gloo all-reduce correctness probe, reference CPU image, and generated Kubernetes Job and rendezvous Service handoff to the governed manifest dry-run workflow.
- Added lab audit and NATS events, Prometheus metrics, and Grafana evidence visibility.
- Kept virtual accelerator profiles isolated from physical capacity and forced unqualified MCCL and device-direct plans to remain non-executable.

## Mycelium heterogeneous optimizer

- Added the versioned Mycelium 1.0 optimizer with memory-constrained minimax layer placement, data/pipeline/ZeRO candidate scoring, throughput/balance/efficiency objectives, profile coverage, stage imbalance, memory feasibility, and explainable decisions.
- Added per-vendor CUDA, ROCm, and oneAPI worker-image overrides while preserving one global rank and rendezvous contract.
- Added a PyTorch/Gloo CPU-forwarded reference adapter with pinned host staging and a distributed launcher for functional mixed-vendor testing.
- Added Mycelium dashboard controls, execution-plan traces, CLI arguments, Prometheus metrics, a Grafana panel, documentation, and regression tests.
- Kept MCCL and device-direct transports fail-closed until native adapters and RDMA paths are qualified on every selected node.

All notable OpenMycelium changes are documented in this file.

## Unreleased

### Added

- Branded Windows, macOS, and Linux Docker lifecycle installers with staged progress, secure first-run configuration, persistent Site ID, health validation, diagnostics, and browser launch
- PostgreSQL-backed authentication, users, roles, sessions, settings, audit records, and encrypted cluster credentials
- Connected Kubernetes cluster inventory, node capacity, workspace boundaries, deployment controller, lifecycle reconciliation, logs, events, service access, storage, manifests, probes, and audited container commands
- Heterogeneous accelerator pools, NVIDIA MIG and shared-GPU profiles, gang admission, indexed parallel jobs, Kueue, Volcano, topology controls, rendezvous, and RDMA contracts
- Heterogeneous fabric qualification, persisted benchmark profiles, profile-weighted DP/PP/ZeRO planning, fail-closed cross-vendor transport admission, and per-vendor Kubernetes execution Jobs
- CPU/GPU model sizing for dense and MoE models, quantization, KV cache, training memory, batch size, context, FLOPs, and placement
- Governed model repository, Ollama synchronization, inference proxy, workload registration, and chat console
- Agentic AI definitions, flows, tools, MCP bindings, memory, approvals, Kubernetes-backed runs, evaluations, and traces
- MLOps and AIOps APIs, authenticated Prometheus metrics, alert rules, and provisioned Grafana dashboards
- Premium responsive dashboard, persistent navigation, light/dark themes, live compute topology, and redesigned authentication experience
- Architecture, installation, use-case, feature, and authentication documentation

### Fixed

- Real Windows host inventory check-in for Docker Desktop deployments
- Persistent workload and infrastructure state instead of sample dashboard records
- Kubernetes Service proxy target formatting and workspace endpoint discovery
- Modal cancellation without triggering required-field validation
- Workload deletion, Kubernetes inspection, and service access workflows
- Responsive dashboard overflow, independent frame movement, and sidebar scroll behavior
- Windows installer false failure caused by an unavailable redirected process exit code

### Known limitations

- The Kubernetes discovery-agent image remains an engineering milestone and is not yet a production telemetry agent
- The Helm chart requires external PostgreSQL and NATS and has not completed HA, upgrade, and Canonical MicroK8s production validation
- Logical memory and accelerator pools do not create hardware-coherent unified memory across physically separate devices
- Signed native installers and published multi-architecture release images are not included yet
