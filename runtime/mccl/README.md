# openmycelium-mccl

Qualified cross-vendor communication runtime: move tensors between an **NVIDIA
CUDA** device and an **AMD ROCm** device, with every direction byte-verified
before it is permitted.

```
NVIDIA VRAM --cudaMemcpyAsync--> pinned host --TCP--> pinned host --hipMemcpyAsync--> AMD VRAM
```

**Version 0.2.0a2 — alpha.** Read the limitations before relying on anything here.

## What this is

A point-to-point transport plus two collectives, with a qualification gate in
front of them. NCCL and RCCL remain responsible for all vendor-local
communication; this package only bridges between vendors.

| Capability | State |
|---|---|
| CUDA↔ROCm host-staged point-to-point transport | verified both directions |
| Capability discovery and qualification ledger | verified |
| Dynamic tensor framing (8 dtypes, dynamic shapes) | verified |
| Timeout, failure, and epoch fencing | verified |
| Pipeline activation transfer | verified |
| Broadcast (root on either vendor) | verified |
| All-gather (rank-ordered, no root) | verified |
| Connection multiplexing with concurrent collective IDs | verified over a live socket |
| Diagnostics and qualification CLI | included |

## What this is **not**

* **Not unified VRAM.** The two cards remain separate memory domains. Nothing
  here makes 16 GB + 16 GB addressable as 32 GB.
* **Not production ready.** Alpha, on one measured machine.
* **Not an all-reduce.** Cross-vendor reduction semantics — dtype, operation
  order, and numerical tolerance — are undefined here and deliberately omitted.
* **Not a model runner.** No checkpoint loading, batching, or KV-cache
  management.

## Known limitations

**UCX direct receive into ROCm memory is disabled on WSL/DXG.** On the DXG path
a `hipMalloc` pointer is not CPU-accessible — a direct load or store faults.
UCX's `uct_rocm_copy_ep_put_short` stores from the CPU, and the generic eager
unpack `memcpy`s, so both fault. `mccl.qualification` therefore routes ROCm
receives through host staging and refuses UCX direct receive. CUDA is
unaffected because `uct_cuda_copy` uses `cuMemcpyAsync`.

**Single-rank NCCL/RCCL groups are degenerate.** On a machine with one GPU per
vendor, vendor-local collectives have a single participant. Results validate
orchestration and cross-vendor routing, not multi-rank vendor-local behaviour.
Every collective report carries a `degeneracy` note stating this.

**ROCm PyTorch on WSL may need a provisional patch.** ROCm PyTorch wheels bundle
an HSA runtime that does not enumerate the GPU on the DXG path. Replacing it
with the system runtime works but produces a mixed-runtime configuration that
neither AMD nor PyTorch supports. See `rocm_torch_patch.sh` — it records hashes,
keeps a restorable backup, checks ABI compatibility, and runs a real kernel
before declaring success. A `pip install --upgrade torch` silently reverts it.

## Qualified hardware and runtime tuple

Measured on:

| | |
|---|---|
| NVIDIA GPU | GeForce RTX 5060 Ti, driver 591.86 |
| AMD GPU | Radeon RX 9060 XT (gfx1200) |
| ROCm | 7.2.0 (WSL DXG path, no `/dev/kfd`) |
| Windows | 10.0.26200.9168, Adrenalin 26.8.1 |
| WSL kernel | 6.18.33.2-microsoft-standard-WSL2 |
| Distro | Ubuntu 24.04 |

Other hardware is **unqualified** until you run the qualification yourself.

## Install and verify

```sh
python -m venv /tmp/mccl-clean
. /tmp/mccl-clean/bin/activate
pip install openmycelium_mccl-0.2.0a2-py3-none-any.whl

# Build the native components (no CUDA/ROCm toolkit needed; dlopen at run time)
python -c "import mccl, os; print(os.path.join(os.path.dirname(mccl.__file__), 'native'))"
sh <that path>/build.sh /tmp/mccl-bin
export OM_BRIDGE_BIN=/tmp/mccl-bin/bridge
export OM_PROBE_BIN=/tmp/mccl-bin/host_access_probe

mccl diagnose
mccl qualify --direction cuda-to-rocm
mccl qualify --direction rocm-to-cuda
mccl smoke-test
```

`mccl qualify` **reads** the ledger; it does not create one. Produce records
with the qualification runner, which transfers with payload CRC enabled and
records the platform tuple only if the run is byte-verified:

```sh
python runtime/bridge/qualify_direction.py --send cuda --recv rocm
python runtime/bridge/qualify_direction.py --send rocm --recv cuda
```

Qualification is **per direction**. The measured failure mode was asymmetric —
receive into ROCm memory faulted while ROCm as a source worked — so qualifying
`rocm->cuda` says nothing about `cuda->rocm`.

## Wire protocol

**Protocol v1, originally frozen in 0.2.0a1 and unchanged by the 0.2.0a2 naming release.** Two protocols share the link with distinct magic
values so neither can be parsed as the other:

| Protocol | Magic | Version |
|---|---|---|
| Activation (point-to-point) | `XMC1` | 1 |
| Collective (broadcast, all-gather) | `OMC2` | 1 |

Incompatible frame changes must increment the version. `negotiate()` refuses a
mismatch with a clear message and has **no downgrade path** — a newer build
talking to an older one fails visibly rather than guessing at a layout.

## Tuning

Defaults come from measurement, not preference: **2 slots**, **4 MiB chunks**,
payload CRC **off** in production (it cost 3.3× throughput). The 2–8 MiB range
is this platform's *qualified profile*, not a universal optimum — PCIe topology,
NUMA layout, and bare metal versus WSL all move it. A chunk outside the profile
is permitted with `allow_unqualified_chunk=True` and should be requalified.

## Licence

Apache-2.0. See `LICENSE` and `NOTICE`.
