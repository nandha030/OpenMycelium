# Installing OpenMycelium v0.1.0 Technical Preview

> OpenMycelium v0.1.0 Technical Preview aggregates model capacity across one
> NVIDIA CUDA GPU and one AMD ROCm GPU. It does not create unified VRAM.
> Validated on Windows 11 with WSL2, RTX 5060 Ti 16 GB, RX 9060 XT 16 GB, and
> Mistral-Nemo-Instruct-2407.

## What you need

| | Requirement |
|---|---|
| OS | Windows 11 with WSL2, or Ubuntu 24.04 under WSL2 |
| NVIDIA | One CUDA GPU with the **Windows** driver installed (WSL uses the Windows driver, not a Linux one) |
| AMD | One ROCm-capable GPU with the Windows AMD driver, plus the ROCm-for-WSL system components (below) |
| Python | 3.10 or newer; 3.12 is what was validated |
| Disk | ~40 GB: 8.3 GB of Python environments plus your model |
| Memory | 16 GB validated |

Both GPUs are required. This build **will not** fall back to one GPU or the
CPU — a partial result would still produce output, and that output would
silently not be the thing this project exists to do.

## 1. Install the package

```bash
python3 -m venv /opt/openmycelium/venv
/opt/openmycelium/venv/bin/pip install \
    openmycelium_mccl-0.2.0a2-py3-none-any.whl \
    openmycelium-0.1.0-py3-none-any.whl
export PATH=/opt/openmycelium/venv/bin:$PATH
```

Verify what you installed before running it:

```bash
sha256sum -c SHA256SUMS.frozen
openmycelium version --verbose
```

## 2. Install the AMD ROCm-for-WSL system components

**This step is manual and cannot be skipped.** OpenMycelium will not add
operating-system repositories on your behalf.

The reason is specific: the PyTorch ROCm wheel bundles an HSA runtime built for
native Linux, which talks to `/dev/kfd`. WSL has no `/dev/kfd` — it exposes the
GPU through `/dev/dxg`. Without AMD's WSL build of that runtime, `torch` imports
cleanly, reports no device, and gives no explanation. `openmycelium doctor` and
`openmycelium provision` detect exactly this and tell you so.

### Supported path — AMD's documented installation

Follow AMD's own instructions:

- <https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/wsl/install-radeon.html>
- <https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/wsl/legacywsl/install-pytorch.html>

In outline, subject to those pages:

```bash
# install AMD's amdgpu-install package for your Ubuntu release, then:
amdgpu-install --usecase=wsl,rocm --no-dkms
```

### Experimental minimal path

On the validated configuration, OpenMycelium experimentally reduced the
prerequisite to two AMD packages totalling approximately 0.5 MiB, because the
selected PyTorch wheel already contained the remaining user-space libraries:

```
rocm-core                    7.2.0.70200-43~24.04
hsa-runtime-rocr4wsl-amdgpu  25.30.13-2281980.24.04
```

This is **not** a general claim about ROCm. It was validated only on this exact
combination of WSL build, GPU, ROCm version and PyTorch wheel. Do not assume it
holds elsewhere.

### Then put the WSL runtime where torch will load it

```bash
T=<rocm-env>/lib/python3.12/site-packages/torch/lib
cp $T/libhsa-runtime64.so.1 $T/libhsa-runtime64.so.1.orig     # keep a backup
cp /opt/rocm/lib/libhsa-runtime64.so.1 $T/libhsa-runtime64.so.1
cp /opt/rocm/lib/libhsa-runtime64.so.1 $T/libhsa-runtime64.so
```

This modifies a file inside an installed package, so **a later
`pip install --upgrade torch` will put the incompatible runtime back**.
`openmycelium provision` detects that specific regression and says so, rather
than telling you to reinstall ROCm from scratch.

## 3. Provision the Python environments

```bash
openmycelium provision
```

Creates a CUDA and a ROCm virtual environment, installs pinned wheels into
each, and proves each one can execute a real BF16 matmul on its own card. It is
idempotent: rerunning after a partial failure resumes rather than destroying
environments that already work, and nothing is removed unless you pass
`--clean`.

Expect roughly 8.3 GiB of downloads on a first run.

### Offline installation

If you have the verified wheelhouse, both environments install from local files
with the network refused:

```bash
pip install --no-index --find-links wheelhouse/cuda torch==2.11.0 \
    transformers==5.15.1 tokenizers==0.22.2 safetensors numpy
pip install --no-index --find-links wheelhouse/rocm torch==2.10.0 \
    transformers==5.15.1 tokenizers==0.22.2 safetensors numpy
```

Measured: 54 packages in 1 min 46 s, and 36 packages in 2 min 19 s. Verify
`SHA256SUMS` in each directory first. The wheelhouse contains Python wheels
only — the AMD system packages above are **not** in it.

## 4. Check before you load 22 GB

```bash
openmycelium doctor
```

Reports each precondition separately, including the three ROCm states that are
easy to confuse:

```
ROCm Python packages installed   yes
ROCm WSL system runtime          yes
ROCm GPU operation qualified     yes
```

## 5. Add a model

```bash
openmycelium model import /path/to/Mistral-Nemo-Instruct-2407
openmycelium model verify Mistral-Nemo-Instruct-2407
openmycelium plan --model Mistral-Nemo-Instruct-2407
```

Safetensors checkpoints only. `model pull` refuses to fetch executable content.

## 6. Run

```bash
openmycelium run  --model Mistral-Nemo-Instruct-2407 --prompt "..."
openmycelium chat --model Mistral-Nemo-Instruct-2407
openmycelium serve --model Mistral-Nemo-Instruct-2407    # OpenAI API on 11500
```

`serve` must be held by a live client. A server started from a script whose
session then exits is terminated with that session; the Windows launcher holds
one foreground `wsl.exe` for exactly this reason. Closing that window unloads
the model — intended, and visible rather than a silent death.

## 7. Connect a client

```
Base URL   http://127.0.0.1:11500/v1
API key    required for non-loopback; set OPENMYCELIUM_TOKEN
```

Port 11500 is deliberately not Ollama's 11434, so both can run side by side.

For a client in a container, bind non-loopback and authenticate:

```bash
OPENMYCELIUM_TOKEN=$(cat /run/openmycelium/token) \
  openmycelium serve --model MODEL --host 0.0.0.0
```

Pass the token through the environment, never `--token`, which would put it in
the process command line where any user can read it.

Verified against Open WebUI v0.11.1 — see `OPEN_WEBUI_SMOKE.md`.
