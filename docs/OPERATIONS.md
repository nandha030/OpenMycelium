# Operating OpenMycelium

> OpenMycelium aggregates model capacity across one NVIDIA CUDA GPU and one AMD
> ROCm GPU. It does not create unified VRAM. Validated on Windows 11 with WSL2,
> RTX 5060 Ti 16 GB, RX 9060 XT 16 GB, and Mistral-Nemo-Instruct-2407.

Two platforms are described below. **Only the Windows + WSL2 path is
validated.** The native Linux path is written from the same code and is
untested; where it differs, that is said plainly rather than glossed.

---

# Part 1 — Windows 11 with WSL2 (validated)

Everything runs inside a WSL2 distribution. Windows contributes the GPU drivers
and a launcher; it does not run the runtime.

## 1.1 Prerequisites

| | |
|---|---|
| Windows | 11, with WSL2 enabled |
| NVIDIA | Windows driver installed. WSL uses the **Windows** driver, never a Linux one |
| AMD | Windows driver, plus the ROCm-for-WSL system components (§1.3) |
| Distribution | Ubuntu 24.04 validated |
| Disk | ~40 GB: 8.3 GB of Python environments plus your model |

Check WSL sees a GPU before anything else:

```powershell
wsl.exe -d Ubuntu-24.04 -u root ls -l /dev/dxg
wsl.exe -d Ubuntu-24.04 -u root nvidia-smi --query-gpu=name --format=csv,noheader
```

`/dev/dxg` is how WSL exposes both cards. There is **no** `/dev/kfd` under WSL,
and `rocm-smi` will not work — it needs the `amdgpu` kernel module, which does
not exist here. That is expected, not a fault.

## 1.2 Install the package

Inside the distribution:

```bash
python3 -m venv /opt/openmycelium/venv
/opt/openmycelium/venv/bin/pip install \
    openmycelium_mccl-0.2.0a2-py3-none-any.whl \
    openmycelium-0.1.0-py3-none-any.whl
```

Verify what you installed before trusting it:

```bash
sha256sum -c SHA256SUMS.frozen
/opt/openmycelium/venv/bin/openmycelium version --verbose
```

`installedContentSha256` is the hash of the files on disk. It is recorded
separately from the wheel hash because a wheel is usually deleted after
installation, and the question that matters later is what is running now.

## 1.3 Install the AMD ROCm-for-WSL system components

**Manual, and it cannot be skipped.** OpenMycelium will not add
operating-system repositories on your behalf.

The reason is specific: the PyTorch ROCm wheel bundles an HSA runtime built for
native Linux, which talks to `/dev/kfd`. WSL has no `/dev/kfd`. Without AMD's
WSL build of that runtime, `torch` imports cleanly, reports no device, and
explains nothing.

Follow AMD's own instructions —
[install-radeon](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/wsl/install-radeon.html)
and
[install-pytorch](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/wsl/legacywsl/install-pytorch.html):

```bash
# install AMD's amdgpu-install package for your Ubuntu release, then:
sudo amdgpu-install --usecase=wsl,rocm --no-dkms
```

Then put the WSL runtime where torch will load it:

```bash
T=/var/lib/openmycelium/state/env/rocm/lib/python3.12/site-packages/torch/lib
cp $T/libhsa-runtime64.so.1 $T/libhsa-runtime64.so.1.orig     # keep the original
cp /opt/rocm/lib/libhsa-runtime64.so.1 $T/libhsa-runtime64.so.1
cp /opt/rocm/lib/libhsa-runtime64.so.1 $T/libhsa-runtime64.so
```

This edits a file inside an installed package, so **`pip install --upgrade
torch` will put the broken runtime back**. `openmycelium provision` detects that
exact regression and tells you so, instead of sending you to reinstall ROCm.

## 1.4 Provision the two Python environments

```bash
openmycelium provision
```

Creates a CUDA and a ROCm virtual environment, installs pinned wheels into
each, and proves each can run a real BF16 matmul on its own card. Idempotent: a
rerun after a partial failure resumes, and nothing is deleted without
`--clean`. Expect ~8.3 GiB of downloads the first time.

Offline, from a verified wheelhouse:

```bash
pip install --no-index --find-links wheelhouse/cuda torch==2.11.0 \
    transformers==5.15.1 tokenizers==0.22.2 safetensors numpy
pip install --no-index --find-links wheelhouse/rocm torch==2.10.0 \
    transformers==5.15.1 tokenizers==0.22.2 safetensors numpy
```

Measured: 54 packages in 1 min 46 s, 36 in 2 min 19 s. Verify each directory's
`SHA256SUMS` first. The wheelhouse holds Python wheels only — the AMD system
packages of §1.3 are **not** in it.

## 1.5 Configure the Windows launcher

`openmycelium.cmd` locates the installed command inside WSL. It needs to know
which distribution, and where the venv is if it is not on the default `PATH`:

```powershell
$env:OPENMYCELIUM_WSL_DISTRO = "Ubuntu-24.04"
$env:OPENMYCELIUM_VENV       = "/opt/openmycelium/venv"
```

Permanently:

```powershell
[Environment]::SetEnvironmentVariable("OPENMYCELIUM_WSL_DISTRO", "Ubuntu-24.04", "User")
[Environment]::SetEnvironmentVariable("OPENMYCELIUM_VENV", "/opt/openmycelium/venv", "User")
```

Close and reopen PowerShell afterwards. There is **no default distribution**:
if more than one is installed and none is chosen, the launcher lists them and
stops rather than guessing.

## 1.6 Check before loading 22 GB

```powershell
.\openmycelium.cmd config     # every path, and which source decided it
.\openmycelium.cmd doctor
```

`doctor` separates the three ROCm states that are easy to confuse:

```
ROCm Python packages installed   yes
ROCm WSL system runtime          yes
ROCm GPU operation qualified     yes
```

If the middle line is `no`, §1.3 was skipped or a torch upgrade undid it.

## 1.7 Add a model

```powershell
.\openmycelium.cmd model import D:\models\Mistral-Nemo-Instruct-2407
.\openmycelium.cmd model verify Mistral-Nemo-Instruct-2407
.\openmycelium.cmd plan --model Mistral-Nemo-Instruct-2407
```

Safetensors only. Prefer `model import` over `model pull` in this release:
Mistral repositories publish both a consolidated file and a sharded set of the
same weights, and `pull` can fetch both.

## 1.8 Run

```powershell
.\openmycelium.cmd run  --model Mistral-Nemo-Instruct-2407 --prompt "..."
.\openmycelium.cmd chat --model Mistral-Nemo-Instruct-2407
.\openmycelium.cmd serve --model Mistral-Nemo-Instruct-2407
.\openmycelium.cmd console
```

**Keep the window open.** WSL2 stops its VM when the last Windows client
disconnects, so the launcher holds one foreground `wsl.exe` for the life of
`run`, `chat`, `serve` and `console`. Closing the window unloads the model —
intended, and visible rather than a silent death.

---

# Part 2 — Native Linux (untested)

The runtime detects `/dev/kfd` and has code paths for it, but has **only ever
been run on `/dev/dxg` under WSL**. Treat everything here as a starting point,
not a supported configuration, and expect to debug.

What differs from Part 1:

- **No `openmycelium.cmd`.** Call the installed command directly. `OPENMYCELIUM_WSL_DISTRO` and `OPENMYCELIUM_VENV` are meaningless.
- **No ROCm-for-WSL substitution.** On native Linux, `/dev/kfd` exists and the HSA runtime shipped in the PyTorch ROCm wheel is the correct one. **Do not** copy `/opt/rocm/lib/libhsa-runtime64.so.1` over it — §1.3 exists only because WSL has no `/dev/kfd`.
- **`rocm-smi` should work**, unlike under WSL.
- Install ROCm through your distribution's supported method, including the kernel driver, which WSL does not use.

```bash
python3 -m venv /opt/openmycelium/venv
/opt/openmycelium/venv/bin/pip install openmycelium_mccl-*.whl openmycelium-*.whl
export PATH=/opt/openmycelium/venv/bin:$PATH

openmycelium provision
openmycelium doctor
openmycelium model import /path/to/model
openmycelium run --model MODEL --prompt "..."
```

If `doctor` reports the AMD card but `provision` refuses, read its output
before changing anything: it distinguishes missing packages, a missing system
runtime, and a runtime that was replaced by a torch upgrade.

---

# Part 3 — Daily operation, both platforms

Drop the `.\openmycelium.cmd` prefix on Linux; the subcommands are identical.

## 3.1 The console

```
openmycelium console        →  http://127.0.0.1:11501
```

Loopback only, no authentication, and no `--host` flag — exposing it would take
a code change. Six screens: Overview, Fabric, Models, Plan, Run, Runtime and
evidence.

It can read everything, start one inference run, and stop the run it started.
It cannot pull, import or remove models, provision, change configuration, or
manage the API server. Use the CLI for those.

Before a run it requires readiness to pass, the model to verify, a placement to
compile, and the machine to be idle — then shows the expected GPU allocation
before anything starts.

## 3.2 The API

```
openmycelium serve --model MODEL       →  http://127.0.0.1:11500/v1
```

Port 11500 is deliberately not Ollama's 11434, so both can run side by side.
Verified against Open WebUI v0.11.1 and the OpenAI Python SDK.

Loopback is unauthenticated. Anything else needs a token, passed through the
environment:

```bash
OPENMYCELIUM_TOKEN=$(cat /run/openmycelium/token) \
  openmycelium serve --model MODEL --host 0.0.0.0
```

Never use `--token`: it puts the secret in the process command line where any
user can read it from `ps`. Keep the file on `tmpfs` with mode 600, not under
`/var/log`.

Greedy decoding only. `temperature`, `top_p` and penalties are refused with
HTTP 400 rather than silently ignored. One request at a time; a second gets 429
with `Retry-After`.

## 3.3 Watching what is running

```bash
openmycelium ps            # running runtimes, and stale records
openmycelium stats         # per-run measurements
openmycelium fabric list   # discovered accelerators and their identities
```

`ps` distinguishes a live process from a record whose process is gone. A stale
record is normal after an abrupt exit and is not an error.

## 3.4 Stopping

```bash
openmycelium stop            # graceful shutdown of running runtimes
openmycelium stop RUN        # one run, by id
openmycelium stop --prune    # clear records whose process has exited
```

Always confirm afterwards:

```bash
pgrep -fc pipeline_run                                        # expect 0
nvidia-smi --query-gpu=memory.used --format=csv,noheader      # expect idle
```

Idle on the validated machine is roughly 1.3 GiB on the NVIDIA card and under
100 MiB on the AMD one. Anything near 11 GiB means a worker is still holding
weights.

Closing the foreground window also stops everything, on Windows by design.

## 3.5 Maintenance

**After a torch upgrade on WSL**, the AMD side will stop working, because the
upgrade restores the `/dev/kfd` HSA runtime. Symptom:
`torch.cuda.is_available()` false with no explanation.

```bash
openmycelium provision
```

It reports this as a regression rather than a missing installation, and prints
the single `cp` that fixes it.

**Checking what is installed:**

```bash
openmycelium version --verbose     # version, content hash, MCCL, HSA runtime origin
openmycelium config                # every resolved path and its source
```

**Rebuilding an environment** (destructive, removes and recreates both):

```bash
openmycelium provision --clean
```

**Removing a model** — strict by design, no prefix matching and no substitution
of a similarly named copy:

```bash
openmycelium model list
openmycelium model remove MODEL --yes
```

Full uninstall and rollback: see `ROLLBACK.md`.

## 3.6 When something is wrong

| Symptom | Where to look |
|---|---|
| Launcher cannot find the command | `OPENMYCELIUM_WSL_DISTRO` and `OPENMYCELIUM_VENV`; test with `wsl.exe -d DISTRO -u root /opt/.../bin/openmycelium version` |
| More than one distribution installed | The launcher lists them and stops. Choose one; it will not guess |
| AMD card absent after an upgrade | §3.5 — run `provision` and read its verdict |
| `rocm-smi` fails under WSL | Expected. It needs the `amdgpu` module, which WSL does not have |
| Model unloads when the window closes | By design on Windows. Keep the window open |
| A second API request gets 429 | By design. One request at a time |
| Sampling parameters rejected | By design. Greedy only in this release |
| Console shows a stale reading | Readiness is cached ~20 s. The status pill's tooltip shows its age |

Everything `doctor` reports is a precondition checked before a long load, which
is the point: discovering after ten minutes that the ROCm runtime cannot see
its GPU wastes all of it.
