# OpenMycelium proprietary platform layers

## Naming contract

- **MHub (Mycelium Hub)** is the OpenMycelium control plane. It exposes inventory, profiling, placement, admission, job lifecycle, policy, and explainability through one API and CLI.
- **MCCL (Mycelium Collective Communication Layer)** is the multi-vendor communication runtime. It implements qualified point-to-point and collective data movement across runtime boundaries.
- **Mycelium Scheduler** owns the placement algorithm. Workers execute an immutable placement manifest and never calculate an independent plan.
- Existing NVIDIA NCCL, AMD RCCL, Intel oneCCL, UCX, Gloo, and other runtimes remain vendor or transport backends. OpenMycelium orchestrates them; it does not rename or replace them.

The wire protocol version remains independent of product naming. A product rename alone must not invalidate previously qualified byte-level transport evidence.

## Current implementation status (0.2 alpha)

- **Fabric foundation implemented:** NVIDIA UUID identities, complete AMD PCI BDF identities where exposed, weaker bus-only identities when WSL omits device/function, duplicate detection, evidence timestamps and an atomic versioned cache.
- **Scheduler manifest core implemented:** one planner invocation produces one digest-protected manifest with exact layer/tensor ownership, model fingerprint, device identities, memory budgets and the qualified cross-vendor transport. One-shot inference, persistent chat and reproducibility runners pass that same manifest to both workers.
- **Admission is currently one-shot:** a proposed per-device budget is checked against the fresh Fabric snapshot before loading. Durable leases, concurrent-job reservations, queueing, cleanup/recovery and cryptographic manifest signatures are not implemented yet.
- **Intelligence is not yet a product service:** measurements exist in run reports and qualification evidence, but workload observations are not yet stored and used for placement scoring.
- **HetFabric is partially implemented:** MCCL is the measured CUDA-to-ROCm path. Native NCCL/RCCL dispatch interfaces and same-vendor multi-rank qualification remain pending.

This means the current build is a working, Scheduler-controlled cross-vendor inference alpha on one qualified machine. It is not yet a general multi-tenant logical GPU pool.

## Four-layer architecture

```text
User / CLI / OpenAI-compatible API / Kubernetes
                       |
                    MHub API
                       |
       +---------------+----------------+
       |               |                |
Mycelium Fabric  Mycelium Intelligence  Mycelium Scheduler
       |               |                |
       +---------------+----------------+
                       |
             immutable PlacementManifest
                       |
               Mycelium HetFabric
       +---------------+----------------+
       |               |                |
  NCCL / CUDA      RCCL / ROCm      MCCL cross-vendor
       |               |                |
   NVIDIA GPUs       AMD GPUs       staged/native links
```

### 1. Mycelium Fabric

**Responsibility:** discover accelerators and normalize them into one evidence-backed resource model without pretending their memory spaces are coherent.

The normalized `DeviceRecord` must include:

- stable identity derived from host and PCI location, such as `host/pci/vendor/index`;
- vendor, architecture, model, runtime and driver versions;
- total, reserved, committed and currently free memory;
- supported dtypes, operators, graph/runtime features and native collective backend;
- NUMA, PCIe, peer, NIC and cross-host topology;
- evidence source, qualification state, timestamp and expiry.

The normalized `LinkRecord` must include direction, latency, bandwidth, staging requirements, supported memory types, qualification state and evidence timestamp.

**Interfaces:**

```text
Fabric.discover() -> FabricSnapshot
Fabric.watch() -> stream[FabricEvent]
Fabric.qualify(device_or_link, evidence) -> Qualification
openmycelium fabric list [--json]
openmycelium fabric inspect DEVICE_ID
```

**Release gate:** NVIDIA and AMD devices retain distinct stable identities across process restarts; stale or simulated evidence cannot satisfy production admission; adding Intel requires a new provider plugin rather than changes to the core schema.

### 2. Mycelium Intelligence

**Responsibility:** convert observed executions into workload-specific performance, energy, reliability and cost profiles.

Each `RunObservation` records:

- model/checkpoint digest, architecture, precision and placement manifest;
- workload type: inference, fine-tuning, training, image/video or general compute;
- input/output size, batch, sequence/context and concurrency;
- load time, TTFT, prefill rate, decode rate, frames/sec or steps/sec;
- p50/p95/p99 latency, peak memory and host-staging traffic;
- mean/peak power, energy per job/token/frame and configured monetary cost;
- success, error phase, recovery outcome and hardware/runtime tuple.

Start with SQLite and deterministic statistical summaries. A learned predictor may replace an analytic estimate only after it beats that estimate on held-out observations and reports confidence and evidence age.

**Interfaces:**

```text
Intelligence.record(observation)
Intelligence.predict(workload, placement) -> Prediction
Intelligence.compare(candidates, objective) -> RankedCandidates
openmycelium bench ...
openmycelium stats --model MODEL [--json]
```

**Release gate:** repeated runs produce queryable distributions; watts and energy are measured rather than inferred when vendor telemetry is available; every prediction exposes confidence, sample count and fallback method.

### 3. Mycelium Scheduler

**Responsibility:** select and reserve the best feasible placement under compatibility, memory, availability, SLA, energy and cost constraints.

The scheduler is the only planning authority. It enumerates alternatives such as single NVIDIA, single AMD, mixed-vendor pipeline, CPU offload or rejection. It then filters infeasible candidates and scores the rest with Mycelium Intelligence.

The target output is a signed, versioned `PlacementManifest`. The current alpha
implements a versioned, canonical-SHA-256 digest-protected manifest; keyed
signatures are a release-hardening item. The manifest contains:

- job and model identity;
- exact stage, layer and tensor ownership;
- stable device IDs and per-device memory budgets;
- KV-cache, activation and workspace reservations;
- rank, rendezvous and worker launch configuration;
- selected transport per edge and the evidence authorizing it;
- lease, heartbeat, cleanup and failure policy;
- considered alternatives and explainable rejection/score reasons.

Workers must validate the manifest, acquire their lease and execute only their assigned stage. They must not call the planner independently.

**Interfaces:**

```text
Scheduler.plan(WorkloadSpec, FabricSnapshot) -> PlacementManifest
Scheduler.admit(PlacementManifest) -> Lease
Scheduler.release(job_id)
openmycelium plan ... --explain --json
openmycelium submit inference|finetune|training ...
openmycelium jobs list|inspect|cancel
```

**Release gate:** two workers receive one identical manifest; committed memory cannot be double-booked; incompatible or under-capacity jobs fail before model loading; a worker death releases or recovers its lease without orphaning reservations.

### 4. Mycelium HetFabric

**Responsibility:** execute the communication graph chosen by the scheduler using the most appropriate qualified backend for each edge.

Dispatch policy:

1. NVIDIA-local communication uses NCCL when qualified.
2. AMD-local communication uses RCCL when qualified.
3. Intel-local communication uses oneCCL or the qualified Intel backend.
4. A genuine cross-vendor edge uses MCCL.
5. An unqualified direct path falls back to an explicitly admitted staged path or blocks; it never silently upgrades its capability claim.

MCCL owns cross-vendor framing, typed transfers, backpressure, fencing, retries, metrics and collective composition. HetFabric owns the higher-level graph and backend dispatch.

**Interfaces:**

```text
HetFabric.compile(PlacementManifest) -> CommunicationPlan
HetFabric.execute(CommunicationPlan)
MCCL.send/recv/broadcast/all_gather(...)
openmycelium fabric qualify-link SOURCE DEST
```

**Release gate:** the execution report identifies the backend used for every edge; vendor-local and cross-vendor paths can be tested independently; no result claims NCCL/RCCL execution until those calls have run on qualifying multi-card hardware.

## End-to-end decision flow

1. MHub receives a `WorkloadSpec`.
2. Fabric returns a current, qualified snapshot and existing reservations.
3. Scheduler enumerates memory- and compatibility-feasible placements.
4. Intelligence predicts latency, throughput, energy, cost and SLA risk for each candidate.
5. Scheduler selects a candidate, persists the explanation and acquires device-memory leases.
6. HetFabric compiles each communication edge to NCCL, RCCL, oneCCL, MCCL or an explicit fallback.
7. Workers load only their assigned tensors and execute the manifest.
8. Runtime metrics and outcomes return to Intelligence, improving later decisions.
9. MHub exposes status, metrics, logs, cancellation and recovery to the user.

## Implementation sequence

### Milestone A - naming and compatibility

- Publish MCCL/MHub terminology across source, package metadata, CLI, containers, Helm and documents.
- Preserve protocol version 1 and historical Git tags.
- Rebuild distributions under new artifact names; do not relabel old wheels.

### Milestone B - Fabric foundation

- Introduce provider plugins for NVIDIA and AMD discovery.
- Add stable PCI-based identities, normalized records and evidence TTLs.
- Persist `FabricSnapshot` and expose `openmycelium fabric list`.

**Status:** phase-one implementation complete for the qualified NVIDIA + AMD WSL host. Full AMD BDF is used when exposed; bus-only identity is explicitly marked with weaker confidence. Power is unavailable for AMD under this WSL runtime and is reported as unavailable, never zero.

### Milestone C - authoritative Scheduler

- Move planning out of workers.
- Define and validate the versioned placement-manifest schema.
- Add leases, VRAM admission, queueing, cleanup and `--explain`.

This milestone turns the current cross-vendor inference runner into a logical GPU pool.

**Status:** single planning authority, immutable/digest-checked manifest, exact ownership and fresh-snapshot capacity refusal are implemented. Durable leases, double-book prevention across concurrent jobs, queueing and failure cleanup remain before this milestone is complete.

### Milestone D - Intelligence store

- Persist existing inference metrics and hardware tuples in SQLite.
- Add vendor power sampling and energy/cost calculations.
- Compare analytic estimates with historical predictions and calibrate confidence.

### Milestone E - HetFabric dispatch

- Register MCCL as the cross-vendor backend.
- Add native-backend interfaces for NCCL, RCCL and oneCCL.
- Use MCCL only on cross-vendor edges and retain fail-closed qualification.
- Validate same-vendor native paths when additional qualifying hardware is available.

### Milestone F - workload expansion

- Inference: persistent serving, batching, KV-cache accounting and recovery.
- Fine-tuning: LoRA/QLoRA backward gradients, optimizer ownership and checkpoint resume.
- Training: activation checkpointing, gradient collectives, distributed optimizer state and multi-node recovery.

## Product acceptance criteria

OpenMycelium may describe the devices as one **logical accelerator pool** only when:

- Fabric reports stable, evidence-backed resources and links;
- Scheduler is the single placement authority and prevents overcommit;
- HetFabric reports exactly which backend moved every load-bearing tensor;
- Intelligence stores measured performance and energy rather than only static coefficients;
- job failure, cancellation and restart release reservations deterministically;
- user-facing output says aggregated schedulable capacity, not coherent unified VRAM.

The first deployable pool is inference-oriented. Fine-tuning and training join the same pool only after backward-gradient and optimizer-state correctness have independent reference tests.
