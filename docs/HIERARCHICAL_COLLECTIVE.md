# Hierarchical cross-vendor collective

This document covers `runtime/mycelium/hierarchical.py`, which reduces a tensor
across NVIDIA and AMD groups without a cross-vendor communicator.

## Why a hierarchy is required

NCCL and RCCL are not wire-compatible. RCCL is a source-level fork of NCCL's
API, not of its transport, and neither library exposes a documented wire
protocol that a third implementation could join. An NVIDIA rank and an AMD rank
therefore cannot share one communicator, and MCCL cannot "speak NCCL".

What is achievable is composition. Each vendor reduces internally with its own
native library, and only the group leaders cross the boundary, carrying host
memory over MCCL's portable TCP transport:

```
  1. reduce    NCCL across the NVIDIA group      -> NVIDIA partial
               RCCL across the AMD group         -> AMD partial
  2. bridge    leaders exchange partials over MCCL host-staged TCP
  3. fan-out   each leader broadcasts the global result down its own group
```

No vendor library ever sees a peer it cannot speak to. The payload crossing the
boundary is an ordinary pinned host buffer.

## Topology

Ranks are partitioned by a spec string, `vendor:count[,vendor:count...]`:

```
MYCELIUM_TOPOLOGY=cuda:2,rocm:2     # 4 ranks, 2 groups, leaders 0 and 2
MYCELIUM_TOPOLOGY=cuda:4,rocm:4     # 8 ranks, 2 groups, bridge world size 2
MYCELIUM_TOPOLOGY=cpu:2,cpu:1,cpu:3 # 6 ranks, 3 groups, no accelerator needed
```

Global ranks are assigned in order. The lowest rank in each group is its leader.
The bridge world size is the **group count**, not the rank count: an eight-rank
job crosses the vendor boundary with two participants.

## Backend selection

Each group's backend is decided by its vendor alone, never by the local build:

| Vendor | `new_group` backend | Library |
|---|---|---|
| `cuda` | `nccl` | NCCL |
| `rocm` | `nccl` | RCCL (ROCm PyTorch registers RCCL under this name) |
| `cpu`  | `gloo` | Gloo |

Every rank calls `new_group` for every group, so all ranks must agree on each
group's backend. Ranks outside a group receive `NON_GROUP_MEMBER` and never
construct the backend, which is why a CPU-only rank can safely name `nccl` for a
CUDA group it does not join. Members do need the matching build; a mismatch
raises a deployment-level error naming the required build rather than a
backend-internal failure.

A Gloo process group spans every rank, but it carries only rendezvous,
sub-group construction, and barriers. No payload crosses a vendor boundary
through it.

## Reductions

`sum`, `min`, and `max` compose directly — the minimum of group minima is the
global minimum. `avg` does not: averaging unequal groups and then averaging the
averages is wrong. It is carried as a sum and divided once by the global world
size at the end.

Float addition is not associative, so a hierarchical sum is not guaranteed to be
bitwise identical to a flat one. The harness asserts within `1e-6`.

## Running it

Start a MCCL coordinator, then launch one process per rank:

```bash
mccl serve --host 127.0.0.1 --port 29500 &
cd runtime
python -m mycelium.launch_hierarchy --topology cpu:2,cpu:2 -- \
    python -m mycelium.hierarchy_check
```

Each rank contributes `rank + 1`, making the expected results analytic and
independent of the collective under test: the sum is `N(N+1)/2`, the minimum
`1`, the maximum `N`, the mean `(N+1)/2`. Every rank prints a JSON verdict and
exits non-zero on any mismatch, so the launcher's exit code gates CI.

## When the bridge is used

A single-group job reduces entirely inside its own vendor group and never
contacts the coordinator; bridging one group to itself is a no-op. Only
`group_count > 1` engages MCCL. This keeps homogeneous NVIDIA-only or
AMD-only jobs independent of cross-vendor infrastructure.

`scripts/wsl_bridge_is_load_bearing.sh` asserts both halves of that claim by
running with the coordinator deliberately stopped:

```
A: two groups,  coordinator down -> must fail  (the bridge carries the payload)
B: one group,   coordinator down -> must pass  (no bridge is needed)
```

If Gloo were secretly carrying cross-group data, case A would pass and the
proof would fail.

## Validation status

Verified on WSL2 Ubuntu 24.04, PyTorch 2.11.0+cu128, NCCL 2.28.9, on an
RTX 5060 Ti (Blackwell, sm_120).

| Layer | What it proves | Status |
|---|---|---|
| Topology, leader election, backend choice | 21 unit tests, no PyTorch needed | passing |
| Multi-group hierarchy on CPU | reduce, bridge, and fan-out compose correctly | passing, 2 and 3 groups, even and uneven |
| Bridge is load-bearing | cross-group payload really travels over MCCL | passing |
| CUDA group with a real device | GPU tensor staged across the bridge and back | passing, `cuda:1,cpu:2` |
| CUDA group standalone | an NVIDIA-only job needs no coordinator | passing, `cuda:1` |
| Multi-GPU NCCL reduce | NCCL's own collective across ranks | **not validated** — needs 2+ NVIDIA GPUs |
| ROCm group with a real device | RCCL leg | **not validated** |
| Cross-vendor NVIDIA + AMD | the full claim | **not validated** |

The last three rows need hardware this project has not run on. A single NVIDIA
GPU makes the intra-group reduce a one-rank no-op, so the CUDA rows above
validate device staging and backend wiring, not NCCL's collective itself.

NCCL and RCCL are both Linux-only, and ROCm needs `/dev/kfd`, which WSL2 does
not expose. Cross-vendor validation therefore requires a Linux host with both
cards installed, or two machines.

The ROCm and cross-vendor rows need hardware this project has not run on.
Note that NCCL and RCCL are both Linux-only; neither has a Windows build, and
ROCm requires `/dev/kfd`, which WSL2 does not expose.
