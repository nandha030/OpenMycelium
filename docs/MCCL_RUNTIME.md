# MCCL runtime architecture

MCCL is the data-plane counterpart to the Mycelium placement algorithm.
Mycelium decides where ranks run and which transport evidence is admissible;
MCCL executes collective communication under that contract.

## Implemented in 0.2.0

- `openmycelium-mccl` Python package and `mccl` CLI
- Windows, Linux, and macOS device/capability discovery
- sequence-safe typed AllReduce protocol with rank and metadata validation
- portable TCP coordinator with sum, average, minimum, and maximum reductions
- float32, float64, int32, and int64 payloads
- explainable transport planner that fails closed for direct communication
- C ABI for allocation, copy, stream, event, device discovery, and local reduce
- working host adapter and build-gated CUDA/HIP adapter implementations
- PyTorch host-staged bridge
- explicit `mccl-tcp` execution-plan transport, separate from direct `mccl`
- Docker coordinator image and hardened Kubernetes coordinator manifest
- concurrent two-rank correctness and protocol-failure tests
- bounded, authenticated, checksummed canonical KV-cache transfer broker
- PyTorch MPS Metal host-staging adapter and MLX capability detection
- OpenAI-compatible and vLLM serving adapters with explicit extension discovery
- MRouter whole-request, disaggregated prefill/decode, and speculative plans
- distribution-preserving speculative acceptance and rejection sampling

See [Heterogeneous inference fabric](INFERENCE_FABRIC.md) for the inference
protocol, formulas, commands, and qualification boundary.

## Data path

```text
PyTorch / framework process
        |
        v
MCCL framework bridge
        |
        +--> CUDA adapter --> CUDA memory and streams
        +--> ROCm adapter --> HIP memory and streams
        +--> Host adapter --> CPU memory
        |
        v
Portable TCP coordinator (qualified reference path)
        |
        v
Validated reduction result returned to every rank
```

The portable path is deliberately simple and correct rather than presented as
a high-performance GPU fabric. It copies through host memory and centralizes
the reduction.

## Required next native milestone

1. Dynamic plugin loader and adapter version negotiation in a C++ core.
2. NCCL and RCCL vendor-local reduce-scatter/all-gather execution.
3. Chunked cross-vendor ring with credit-based flow control.
4. libibverbs queue pairs, memory registration, completion queues, and multi-rail routing.
5. GPU-memory registration qualification for NVIDIA peer memory and AMD peer-direct support.
6. PyTorch `ProcessGroup` registration and DeepSpeed compatibility tests.
7. Failure propagation, communicator re-formation, tracing, and Prometheus metrics.
8. A qualification matrix covering exact driver, firmware, NIC, SDK, topology, and payload combinations.

Until these pass on physical NVIDIA and AMD systems, OpenMycelium must continue
to label device-direct plans as blocked and use TCP or Gloo for correctness.
