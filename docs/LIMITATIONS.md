# Limitations — v0.1.0 Technical Preview

Written to be believed. Everything below is a limit that was observed, not one
that was assumed.

## The central claim, stated exactly

> OpenMycelium v0.1.0 Technical Preview aggregates model capacity across one
> NVIDIA CUDA GPU and one AMD ROCm GPU. It does not create unified VRAM.
> Validated on Windows 11 with WSL2, RTX 5060 Ti 16 GB, RX 9060 XT 16 GB, and
> Mistral-Nemo-Instruct-2407.

**Aggregated capacity, not unified VRAM.** A 22.84 GiB model runs across two
16 GiB cards because the layers are split between them and each stage keeps its
own memory space. There is no shared address space, no page migration, and no
single 32 GiB pool. A tensor that does not fit on one card still does not fit.

## Hardware and scale

- **One NVIDIA GPU and one AMD GPU per pipeline are qualified.** Multi-AMD
  identity and scheduling are **not** qualified in v0.1.
  The ledger records the AMD PCI **bus number**, not the full BDF, which cannot
  reliably distinguish multiple functions or devices sharing a bus topology.
  Full BDF is mandatory before any multi-AMD support.
- Single node only. No multi-node, no RDMA.
- Validated on exactly one physical machine. Independent reproduction on another
  mixed-vendor system has not happened.

## Inference

- **Greedy decoding only.** `temperature`, `top_p`, `top_k` and penalties are
  refused with HTTP 400 rather than silently ignored. Sampled decoding is not
  validated and is not offered.
- **One request at a time.** A concurrent request receives HTTP 429 with
  `Retry-After`. No continuous batching.
- **No worker-side cancellation.** Closing a stream — what a UI's Stop button
  does — does not stop the generation. The runtime drains it safely, refuses
  new work with 429 meanwhile, and serves the next request with nothing carried
  over. That is drain handling, not cancellation.
- No cross-request KV-cache reuse; each request re-prefills.
- No quantised models, no GGUF. Safetensors BF16 only.
- One model family validated: Mistral. Other architectures are untested.
- Context length beyond the validated range is untested; SDPA and Flash
  Attention are not qualified.

## Performance

Measured on the validated machine, five independent server sessions, fixed
prompt of 35 tokens and exactly 64 generated tokens:

| Metric | Median | Range |
|---|---|---|
| TTFT | 142.09 ms | 141.99 – 143.99 |
| Decode | 11.09 tok/s | 10.98 – 11.24 |
| End-to-end | 10.95 tok/s | 10.84 – 11.10 |
| Server load | 26.01 s | 24.02 – 26.02 (excluded from throughput) |

These describe this model on this hardware over a host-staged transport. They
are not a general performance claim, and no comparison against Ollama,
llama.cpp or vLLM has been made.

The cross-vendor boundary transfer measured **238.0 MB/s end-to-end at 20 MiB**.
Every activation crossing between stages passes device → pinned host → TCP →
pinned host → device. There is no peer-to-peer path between vendors.

## Operations

- No background service or daemon. `serve` dies with the session that started
  it.
- No TLS. The API is plaintext HTTP; put it behind a reverse proxy if it leaves
  the machine.
- Bearer-token authentication only, and only enforced for non-loopback
  requests. No users, roles or quotas.
- No metrics endpoint, no log rotation policy beyond bounded event files.
- No upgrade or migration path between versions yet.

## Not present at all

Fine-tuning, training, gradients, optimizer state, checkpoint recovery, GPU
leases, queueing, priorities, admission control, multiple simultaneous models,
and scheduler-driven placement across a pool. These are roadmap items, not
hidden features.

## Verified but narrow

- **Clean bootstrap** was proven on a fresh WSL distribution, but only with the
  manual AMD prerequisite installed by hand. Fully automatic system
  provisioning is **not** closed.
- **Open WebUI v0.11.1 compatibility** was verified through its backend request
  path. **GUI rendering was not visually certified.**
- The audit trail and placement manifests are sealed against accidental
  corruption, not against a determined tamperer.
