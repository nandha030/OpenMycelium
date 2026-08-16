# Problems and Use Cases

## The problem OpenMycelium solves

AI infrastructure is usually operated through disconnected vendor tools. NVIDIA, AMD, Intel, Apple, CPU, NPU, cloud GPU, Kubernetes, local model runtimes, and agent frameworks expose different inventory, scheduling, deployment, and telemetry interfaces. Teams are forced to maintain separate scripts and dashboards while losing a consistent view of capacity, model fit, workload ownership, policy, and cost.

OpenMycelium provides one vendor-neutral control plane above those systems. It does not replace CUDA, ROCm, Metal, Kubernetes, or model runtimes. It discovers and governs them through a common operational model.

The platform addresses five recurring problems:

1. **Fragmented capacity:** operators cannot see CPU, RAM, GPU, accelerator memory, node health, and runtime support in one inventory.
2. **Unsafe placement:** model size, quantization, KV cache, batch size, training state, and accelerator availability are often evaluated manually.
3. **Deployment drift:** local inference, arbitrary containers, YAML resources, training jobs, and agent systems are deployed through unrelated workflows.
4. **Limited lifecycle visibility:** a successful API submission does not prove that a Kubernetes pod is scheduled, ready, reachable, or healthy.
5. **Weak governance:** infrastructure access, model provenance, user actions, agent tools, approvals, quotas, and operational events need durable records.

## Primary users

- Platform and infrastructure engineering teams
- MLOps and AIOps teams
- AI research laboratories
- Sovereign AI and regulated-enterprise operators
- Managed GPU and private-cloud providers
- Application teams deploying inference, training, fine-tuning, and agents

## Core use cases

### Heterogeneous accelerator inventory

Discover CPU, RAM, NVIDIA CUDA, AMD ROCm, Intel, Apple Silicon, Kubernetes nodes, and extended device-plugin resources. Build logical pools with selectors and scheduling policies while preserving the real hardware boundaries.

### Model sizing and placement

Estimate dense or MoE model weights, quantization memory, KV cache, context length, batch size, training optimizer state, activation memory, FLOPs, and runtime reserve. Compare the estimate with host or cluster capacity before deployment.

### Local inference on CPU or GPU

Discover models already managed by Ollama, register them as OpenMycelium workloads, route generation through the authenticated API, and test them in the built-in chat console.

### Kubernetes AI workload delivery

Submit inference services, training jobs, fine-tuning jobs, batch workloads, interactive applications, and custom containers. OpenMycelium generates or applies Kubernetes resources, records releases, observes pod lifecycle, and exposes logs, events, services, storage, manifests, and audited commands.

### Workspace-based application operations

Bind an organization boundary to a Kubernetes cluster and namespace. Govern quotas, storage, registry credentials, network policy, releases, pods, services, endpoints, configuration, and events in one workspace.

### Multi-GPU scheduling

Request exact Kubernetes accelerator resources, NVIDIA MIG slices, shared-GPU profiles, gang admission, indexed parallel jobs, Kueue, Volcano, topology spread or packing, RDMA contracts, and rendezvous services. Admission checks use reported cluster capacity instead of fictional resources.

### Model repository and provenance

Register models from Ollama, Hugging Face, OCI, object storage, NFS, HTTPS, or local references. Track versions, runtime contracts, size, provenance, and active deployments.

### Agentic AI orchestration

Define versioned agents and flows, bind API or MCP tools, configure memory, request approvals, launch Kubernetes-backed runs, evaluate results, and inspect replayable traces. NATS carries durable run events while PostgreSQL remains the system of record.

### MLOps and AIOps

Observe workload and model usage, deployment status, node readiness, user actions, alerts, service health, and operational activity. Prometheus and Grafana provide metric collection and dashboards.

## Example workflows

### CPU-only laptop evaluation

1. Install with the branded launcher.
2. Run host discovery.
3. Open Model Planner and enter available RAM and model parameters.
4. Discover a small Ollama model.
5. Register and test the inference workload.

### Private Kubernetes inference service

1. Connect a cluster using an encrypted kubeconfig.
2. Create a workspace for its namespace.
3. Register a model and runtime contract.
4. Use Celium AI+ to deploy an inference service.
5. Inspect pod state, logs, events, service endpoint, storage, and readiness.

### Heterogeneous research cluster

1. Install vendor device plugins and telemetry exporters.
2. Verify live node inventory.
3. Create CUDA, ROCm, MIG, and shared-accelerator pools.
4. Configure queues, quotas, and gang scheduling.
5. Submit compatible workload stages to the appropriate pools.
6. Compare utilization and failures through MLOps/AIOps.

## Important technical boundaries

- Logical pooling does not turn physically separate NVIDIA, AMD, cloud, or remote GPU memory into hardware-coherent unified memory.
- A single process can span devices only when its framework and communication backend support those devices.
- Cross-vendor training is normally expressed as independent stages, workers, services, or data pipelines, not one transparent tensor operation.
- OpenMycelium reports discovered capacity; it does not present simulated accelerators as physical hardware.
- GPU slicing depends on vendor hardware, device plugins, and operators such as NVIDIA MIG or supported time-slicing implementations.
- The current Kubernetes discovery-agent image is an engineering milestone and must be completed before production cluster-wide telemetry claims.

These boundaries are deliberate. OpenMycelium is an orchestration and governance layer, not a claim that software can bypass hardware coherence, interconnect, driver, or runtime limitations.
