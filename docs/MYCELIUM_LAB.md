# Mycelium Lab

Mycelium Lab is the pre-hardware design and evidence workspace for heterogeneous execution. It lets an operator model NVIDIA, AMD, Intel, Apple, and CPU groups without registering virtual devices as physical capacity or allowing them into the production scheduler.

## Available without accelerator hardware

- Persistent virtual topology assets with device count, memory, compute, bandwidth, power, cost, NIC, NUMA, RDMA, GPUDirect, DirectGMA, MCCL, and fault assumptions
- Built-in planning envelopes for common accelerators plus isolated benchmark JSON imports
- Mycelium 1.0 plan compilation across data, pipeline, and ZeRO candidates
- Dense and MoE sizing for total and active weights, KV cache, activations, optimizer state, checkpoint retention, dataset staging, and approximate step FLOPs
- CPU-forwarded, MCCL, and device-direct communication estimates with explicit evidence labels
- Vendor runtime image contracts and saved qualification state
- Persistent experiment results, audit events, NATS events, Prometheus metrics, and a Grafana evidence panel
- A functional CPU/Gloo correctness path that generates Kubernetes YAML for the existing preview, policy, server dry-run, and apply workflow

Virtual profiles are planning evidence only. They never appear in cluster inventory, pool capacity, or scheduler admission. MCCL and device-direct plans stay blocked until measured hardware qualification is attached.

## Run a CPU/Gloo experiment

Build the reference image from the repository root:

```powershell
docker build -f runtime/mycelium/Dockerfile.cpu -t openmycelium/mycelium-pytorch-cpu:local .
```

For a remote k3s or Kubernetes cluster, push the image to a registry that its workers can pull, or import the image into each node's container runtime. Enter that complete image reference in the CPU row of **Mycelium Lab**.

1. Open **Mycelium Lab** and select the CPU/Gloo template.
2. Set the worker image, model, dataset, parallelism, and storage assumptions.
3. Choose **CPU forwarding / Gloo** and compile the simulation.
4. Review Mycelium's decision trace, sizing, communication estimate, and qualification gates.
5. Select **Load dry-run manifest**.
6. Choose a verified cluster and run **Preview & validate**.
7. Apply only after the Kubernetes server dry-run and OpenMycelium policy checks pass.
8. Inspect the resulting Job from Workloads or the Workspace inventory and read each rank's JSON correctness result in Logs.

The smoke test initializes a real `torch.distributed` Gloo process group, performs an all-reduce, verifies the expected sum on every rank, then exits nonzero when correctness fails.

## Hardware qualification path

When physical devices are available, replace planning envelopes with measured profiles and complete these gates per vendor pair:

- Runtime image build, signature, SBOM, vulnerability scan, pull, and framework import
- Device plugin and extended-resource discovery
- Tensor allocation and framework smoke tests
- Point-to-point bandwidth and latency
- All-reduce correctness across sizes and ranks
- RDMA, peer-memory, GPUDirect, DirectGMA, MCCL, or device-direct evidence as applicable
- Failure injection, restart, checkpoint recovery, and numerical parity
- Soak, power, thermal, and cost measurements

Only the existing physical inventory and execution-plan APIs can convert measured evidence into deployable scheduler capacity.
