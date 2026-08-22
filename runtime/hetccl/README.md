# OpenMycelium HetCCL

HetCCL is OpenMycelium's heterogeneous collective runtime package. Version
`0.1.0` provides a functional, cross-platform host-staged AllReduce for Linux,
Windows, and macOS plus a stable native adapter ABI for CUDA and ROCm.

It does not create coherent memory between unrelated GPUs. Each device keeps
its native memory allocation. The portable backend stages typed buffers in
host memory and exchanges them through a checked TCP coordinator. Native
adapters provide device allocation, asynchronous copies, events, and local
reduction kernels; direct RDMA transport remains blocked until it is qualified
on real hardware.

## Install

Windows PowerShell:

```powershell
cd runtime\hetccl
.\install.ps1
hetccl doctor
```

Linux or macOS:

```bash
cd runtime/hetccl
chmod +x install.sh
./install.sh
hetccl doctor
```

Build the host native adapter with CMake:

```bash
HETCCL_BUILD_NATIVE=1 ./install.sh
```

On a CUDA build host, add `HETCCL_ENABLE_CUDA=1`. On a ROCm build host, add
`HETCCL_ENABLE_ROCM=1`. Build CUDA and ROCm adapters in their respective
vendor images; a single compiler image is not required.

## Functional two-rank test

Terminal 1:

```bash
hetccl serve --host 0.0.0.0 --port 29500
```

Terminal 2:

```bash
hetccl allreduce --rank 0 --world-size 2 --group smoke 1 2 3
```

Terminal 3:

```bash
hetccl allreduce --rank 1 --world-size 2 --group smoke 10 20 30
```

Both ranks return `[11.0, 22.0, 33.0]`.

## Python API

```python
from hetccl import CollectiveConfig, HetCCLCollective

collective = HetCCLCollective(
    CollectiveConfig(rank=rank, world_size=world_size, host="coordinator")
)
reduced = collective.all_reduce([1.0, 2.0], dtype="f64")
```

OpenMycelium workloads normally consume the environment contract directly:

```python
from hetccl import HetCCLCollective

collective = HetCCLCollective()
```

The control plane supplies rank, world size, plan ID, and coordinator service.
Choose **HetCCL portable TCP** in Mycelium Plan Studio, or use
`--transport hetccl-tcp` in the OpenMycelium CLI. The separate `hetccl`
transport name remains reserved for qualified device-direct execution.

## Qualification states

- `ready`: the portable TCP path has functional tests.
- `detected-unqualified`: hardware or an SDK was found, but that node has not
  passed correctness, stress, failure, and performance qualification.
- `qualified`: an operator-provided hardware qualification record permits the
  optimized path.
- `blocked-until-qualified`: HetCCL refuses to infer safety from device presence.

The current release is an alpha reference runtime. Production-scale direct
GPU communication still requires native plugin loading, NCCL/RCCL local
collective integration, libibverbs transport, framework registration, and
mixed-vendor hardware qualification.
