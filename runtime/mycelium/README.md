# Mycelium PyTorch runtime

The reference runtime consumes the execution contract injected by the
OpenMycelium Kubernetes controller. It provides a functional cross-vendor
baseline by copying accelerator tensors into pinned host memory and running
the collective through Gloo.

Include the `runtime` directory in both CUDA and ROCm training images, then
initialize the adapter in the training process:

```python
from runtime.mycelium import CPUForwardedCollective

collective = CPUForwardedCollective()
collective.initialize()
collective.all_reduce(gradient)
```

Alternatively, normalize `RANK`, `WORLD_SIZE`, `MASTER_ADDR`, and
`MASTER_PORT` before starting an existing application:

```bash
python -m runtime.mycelium.launch -- python train.py
```

This adapter prioritizes correctness and portability. It does not reproduce
the device-direct results reported by mixed-vendor research. `hetccl` and
`device-direct` plans remain blocked until every selected node advertises a
qualified native adapter and RDMA path. The separate `runtime/hetccl` package
provides a portable TCP reference implementation and native adapter SDK. Use
`HetCCLForwardedCollective` for a functional PyTorch compatibility test after
installing `openmycelium-hetccl` in the workload image.

Build the CPU validation image from the repository root:

```bash
docker build -f runtime/mycelium/Dockerfile.cpu -t openmycelium/mycelium-pytorch-cpu:local .
```

The image runs `python -m runtime.mycelium.smoke`, performs a real Gloo
all-reduce, prints one JSON result per rank, and returns a failing exit code
when the collective result is incorrect.
