# OpenMycelium v0.1.0 — Technical Preview

> OpenMycelium v0.1.0 Technical Preview aggregates model capacity across one
> NVIDIA CUDA GPU and one AMD ROCm GPU. It does not create unified VRAM.
> Validated on Windows 11 with WSL2, RTX 5060 Ti 16 GB, RX 9060 XT 16 GB, and
> Mistral-Nemo-Instruct-2407.

## Artifacts

```
openmycelium-0.1.0-py3-none-any.whl          4e4bde2fc1d3637cc8e04dc499c6b0fc6a0677bf611806eef829b147cd11bdc9
openmycelium_mccl-0.2.0a2-py3-none-any.whl   ce766054068ab627dc6b6930f562819c70e687fdadf3811dc3ba5eed33243694
openmycelium_mccl-0.2.0a2.tar.gz             da41062e1b54bc3426ba1f8a546f81d7ddb405e4976baf97e3cb3a32e9c11089

installedContentSha256   6524d854755e62ad0252142cab27b62e893725fca3cd44c5ddf22db484541c70   (46 files)
SBOM                     SBOM.cyclonedx.json, CycloneDX 1.5, 95 components
```

The installed-content hash is recorded separately from the wheel hash, because
a wheel is usually deleted after installation and the question that matters
later is what is on disk now. `openmycelium version --json` reports it.

## What it does

Runs one model across two GPUs from different vendors. On the validated
machine, a 22.84 GiB Mistral-Nemo checkpoint runs on two 16 GiB cards:

```
CUDA stage   layers 0-19    181 tensors   11680 MiB resident
ROCm stage   layers 20-39   182 tensors   11680 MiB resident, plus the output head
boundary     after layer 19, [1, 1, 5120] bfloat16, 10240 B per token
overlap      0 tensors
```

Every activation crossing between stages goes device → pinned host → TCP →
pinned host → device, and arrives byte-exact.

## Measured performance

Five independent server sessions, fixed 35-token prompt, exactly 64 generated
tokens, `finish_reason=length` in all five:

| Metric | Median | Range |
|---|---|---|
| TTFT | **142.09 ms** | 141.99 – 143.99 |
| Decode | **11.09 tok/s** | 10.98 – 11.24 |
| End-to-end | **10.95 tok/s** | 10.84 – 11.10 |
| Server load | 26.01 s | 24.02 – 26.02, **excluded** from throughput |

Byte-exact transport was qualified immediately before and after the campaign;
full-payload hashing was disabled during the timed sessions so as not to
contaminate what was being measured. The five sessions produced identical
output, which is deterministic functional consistency, **not** per-session
byte-level transport proof.

## Commands

```
provision  config  doctor  fabric  version
model list | inspect | pull | import | verify | remove
plan  run  chat  serve  ps  stop
```

`serve` presents an OpenAI-compatible API on port **11500** — deliberately not
Ollama's 11434, so both can run side by side.

## Verified for this release

- Clean bootstrap on a fresh WSL distribution, with the manual AMD prerequisite
- Both environments created from zero; real BF16 on each physical card, no fallback
- Idempotent provisioning: rerun changes no fingerprint
- Survives a WSL shutdown and restart, requalifying both GPUs
- Offline installation of all 90 wheels from a verified wheelhouse, each half
  finishing with a real BF16 operation on its own GPU
- OpenAI Python SDK 3.5.0: discovery, non-streaming, streaming with usage,
  sampling refusal
- Open WebUI v0.11.1 backend integration
- Audit trail replays through the coordinator's gate, and the gate still refuses
  tampering, wrong run ids, replayed sequences and impostor writers
- Zero orphan workers and VRAM released after every stop

## Known limits, stated plainly

Greedy decoding only — sampling parameters are refused with HTTP 400 rather
than ignored. One request at a time; a second gets 429 with `Retry-After`. No
worker-side cancellation: closing a stream drains the generation, it does not
stop it. One NVIDIA and one AMD GPU per pipeline; **multi-AMD is not
qualified**. Single node. One model family. No TLS. No background service.

**Manual installation of AMD ROCm-for-WSL system components is required.**
OpenMycelium can provision the isolated CUDA and ROCm Python environments after
system prerequisites are available. It will not add operating-system
repositories on your behalf.

Open WebUI compatibility was verified through its backend request path; **GUI
rendering was not visually certified**.

See `docs/LIMITATIONS.md` for the full list, and `docs/HARDWARE_MATRIX.md` for
what is and is not qualified.

## Known issues

These are release-page notes. They do not affect the frozen artifact and were
not rebuilt into it.

**`model pull` can download a repository twice over.** Mistral repositories
publish both a consolidated Safetensors file and a sharded set of the same
weights. `model pull` fetches what the repository lists, so it can retrieve
both and roughly double the download for no benefit. **For v0.1.0, prefer
`model import` from a checkout you already have.** To be fixed in v0.1.1.

**Only Mistral-Nemo-Instruct-2407 is qualified.** Other architectures may load
far enough to be inspected and planned — `model inspect` and `plan` will
produce a placement — but a successful plan is **not** a guarantee that the
model executes correctly across the two stages. Treat any other checkpoint as
untested. To be addressed in v0.1.1.

## Documentation

| | |
|---|---|
| `docs/INSTALL.md` | installation, including the AMD prerequisite |
| `docs/HARDWARE_MATRIX.md` | qualified and explicitly unqualified configurations |
| `docs/LIMITATIONS.md` | what this does not do |
| `docs/SECURITY.md` | threat model, token handling, supply chain |
| `docs/ROLLBACK.md` | downgrade, uninstall, and recovering from a torch upgrade |

## Provenance

`0.1.0` is the identical code to `0.1.0rc1`, differing only in the version
string. The RC1 gate passed with zero failures; its logs are preserved under
`release/0.1.0rc1/gate/`.

Two earlier builds carry the string `0.1.0a7` and **neither may be published**:
a version that maps to two artifacts identifies nothing. Both are retained,
marked non-releasable, with the reasons recorded.
