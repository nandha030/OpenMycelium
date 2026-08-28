# Hardware and software matrix — v0.1.0 Technical Preview

## Qualified

Exactly one configuration has been validated end to end. Everything else is
untested, not "probably fine".

| | Validated configuration |
|---|---|
| Host OS | Windows 11 Pro 10.0.26200 |
| Subsystem | WSL2, kernel 6.18.33.2-microsoft-standard-WSL2 |
| Distribution | Ubuntu 24.04.4 LTS |
| Python | 3.12.3 |
| NVIDIA GPU | GeForce RTX 5060 Ti, 16 GB (15.93 GiB reported) |
| NVIDIA stack | Windows driver 590.57 via `/usr/lib/wsl/lib`, torch 2.11.0+cu128 |
| AMD GPU | Radeon RX 9060 XT, 16 GB (15.81 GiB reported) |
| AMD stack | ROCm 7.2.0, `hsa-runtime-rocr4wsl-amdgpu` 25.30.13, torch 2.10.0+rocm7.0 |
| GPU access | `/dev/dxg` (WSL paravirtualisation). **No `/dev/kfd`** |
| Model | Mistral-Nemo-Instruct-2407, 22.84 GiB, BF16, 40 layers |
| Transport | host-staged cross-vendor, MCCL 0.2.0a2, activation protocol v1 |
| Client | Open WebUI v0.11.1; OpenAI Python SDK |

### Placement on this configuration

```
CUDA stage   layers 0-19    181 tensors   11680 MiB resident
ROCm stage   layers 20-39   182 tensors   11680 MiB resident
             + output head
boundary     after layer 19, [1, 1, 5120] bfloat16, 10240 B per token
overlap      0 tensors
```

## Explicitly not qualified

| | Status |
|---|---|
| More than one AMD GPU | **Not qualified.** The ledger records the AMD PCI bus number, not the full BDF, and cannot distinguish multiple functions or devices sharing a bus topology. Full BDF is mandatory first |
| More than one NVIDIA GPU | Not qualified |
| Two GPUs of the same vendor | Not qualified; the pipeline assumes one CUDA and one ROCm stage |
| Native Linux (`/dev/kfd`) | Untested. The runtime detects the device node but has only ever run on `/dev/dxg` |
| Intel GPUs, Apple Silicon | Not supported |
| Multi-node | Not supported |
| Other model families | Untested. Only Mistral validated |
| Quantised or GGUF checkpoints | Not supported |
| Windows 10 | Untested |
| Ubuntu other than 24.04 | Untested |
| Python 3.10, 3.11, 3.13 | Untested; 3.10+ is accepted but only 3.12.3 was validated |

## Memory guidance

Aggregated capacity, not unified VRAM. A model fits when **each stage's share**
fits on its own card, plus KV cache and activations.

Observed for Mistral-Nemo on 2 × 16 GB:

```
weights per card   ~11.41 GiB
KV cache           81,920 B per token per stage (20 layers, 8 KV heads)
free after load    ~14.8 GiB total reported per card before allocation
```

A single tensor larger than one card's memory will not fit, however many cards
you have.

## Network

Measured on the validated machine and recorded because it materially affects
first-run time:

```
PyPI, download.pytorch.org      ~1.5 MB/s
container registries            ~20-40 KiB/s
```

First provisioning downloads ~8.3 GiB. The offline wheelhouse reduces this to
1 min 46 s (CUDA) and 2 min 19 s (ROCm) from local files.
