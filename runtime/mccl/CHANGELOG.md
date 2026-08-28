# Changelog

## 0.2.0a2 - MCCL/MHub naming release

- Renamed the OpenMycelium communication package and CLI from HetCCL to MCCL.
- Renamed the OpenMycelium routing/control-plane identity from HetHub/HetRouter to MHub.
- Preserved activation and collective protocol version 1 without a wire-layout change.
- Kept third-party HETHUB and HetCCL research names unchanged in citations.

## 0.2.0a1 — first packaged alpha

First release. Everything listed was verified on the hardware tuple in
`README.md`; nothing is claimed beyond it.

### Transport
- `host-staged-xvendor`: CUDA↔ROCm point-to-point over pinned host staging.
  Device memory is never handed to the wire.
- Per-direction qualification ledger recording GPU models, OS build, WSL
  kernel, driver and runtime versions, chunk size, slots, and throughput. A
  direction that has not passed byte verification is refused.
- Chunk profile 2–8 MiB (4 MiB default, 2 slots) from measurement. Sizes
  outside the profile require an explicit opt-in and requalification.
- Credit-based backpressure, verified to bind: 58 stalls in 60 transfers under
  a delayed receiver, with the pinned pool constant at two allocations.
- 10,000 transfers each direction, 6.8 GB, zero bad payloads or headers.
- No global device synchronisation, enforced by an `LD_PRELOAD` guard that
  self-tests to prove it fires.

### Framing
- Activation frames with dynamic shape and dtype (8 dtypes).
- Collective frames carrying operation, collective ID, epoch, originating rank,
  sequence, dtype, shape, and payload length.
- Wire protocol **frozen**; incompatible changes must bump the version and fail
  negotiation with no downgrade path.

### Collectives
- Broadcast, root on either vendor, verified byte-for-byte. Zero-length tensors
  and non-contiguous inputs handled explicitly.
- All-gather, rank-ordered output independent of arrival order, no root.
  Duplicate, missing, stale, and out-of-range contributions rejected; a
  partially assembled output is never returned as success.
- Connection multiplexing for concurrent collective IDs, verified over a live
  CUDA↔ROCm socket with fragmented (1871 writes) and coalesced (1 write)
  segmentation.
- Fencing policy is connection-wide and explicit; per-collective fencing raises
  rather than silently degrading.

### Known limitations
- UCX direct receive into ROCm memory disabled on WSL/DXG (upstream defect).
- Single-rank NCCL/RCCL groups are degenerate; disclosed in every report.
- ROCm PyTorch on WSL may need a provisional, unsupported HSA runtime patch.
- No unified VRAM. No all-reduce. No model runner.
