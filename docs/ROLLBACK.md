# Rollback and uninstall

Nothing here removes a model store or a GPU driver unless you say so
explicitly. Read the step before running it.

## What OpenMycelium puts on your machine

| Path | What it is | Removed by |
|---|---|---|
| `<venv>/…/openmycelium/` | the package itself | `pip uninstall` |
| `<state>/env/cuda`, `<state>/env/rocm` | the two GPU Python environments | step 2 |
| `<state>/` | control records, event logs | step 3 |
| `<model_store>/` | imported checkpoints, tens of GB | step 4, only if you ask |
| `<ledger>` | qualification ledger | step 3 |
| `/opt/rocm`, AMD apt packages | **installed by you**, not by OpenMycelium | step 5 |

Find the real paths on your machine — they are resolved, not fixed:

```bash
openmycelium config
```

## 1. Stop everything first

```bash
openmycelium ps
openmycelium stop
openmycelium stop --prune        # clear records whose process is gone
```

Confirm nothing is left holding a GPU:

```bash
pgrep -fc pipeline_run           # expect 0
nvidia-smi --query-gpu=memory.used --format=csv,noheader
```

## 2. Roll back to a previous version

Versions identify immutable artifacts. Downgrading is a plain reinstall:

```bash
pip uninstall -y openmycelium
pip install openmycelium-<previous>-py3-none-any.whl
openmycelium version --verbose   # check installedContentSha256 against the release
```

The GPU environments are independent of the package version and do **not** need
rebuilding. `openmycelium provision` will recognise them as already usable.

## 3. Remove the environments and state

```bash
rm -rf "$(openmycelium config --json | jq -r .settings.state_dir.value)"
```

Or rebuild them in place instead of deleting:

```bash
openmycelium provision --clean   # destructive: removes and rebuilds both
```

## 4. Remove models — only if you mean it

```bash
openmycelium model list
openmycelium model remove MODEL --yes
```

`model remove` resolves strictly against the store: no prefix matching, no
substitution of a similarly named copy. This is deliberate. An earlier
permissive resolver deleted 22.84 GiB that had been imported from elsewhere.

## 5. Remove the AMD ROCm system components

Only if you want ROCm gone entirely. **These were installed by you, and other
software may depend on them.**

```bash
sudo amdgpu-install --uninstall
# or, for the minimal path:
sudo apt-get remove hsa-runtime-rocr4wsl-amdgpu rocm-core
sudo rm /etc/apt/sources.list.d/rocm.list /etc/apt/sources.list.d/amdgpu.list
sudo rm /etc/apt/keyrings/rocm.gpg
sudo apt-get update
```

If you replaced torch's `libhsa-runtime64.so`, restore the wheel's original:

```bash
T=<rocm-env>/lib/python3.12/site-packages/torch/lib
cp $T/libhsa-runtime64.so.1.orig $T/libhsa-runtime64.so.1
```

## 6. Complete removal

```bash
pip uninstall -y openmycelium openmycelium-mccl
rm -rf <state_dir> <model_store> <ledger>
```

To discard an entire WSL distribution that was used only for this:

```powershell
wsl --unregister <distro>        # irreversible; destroys everything in it
```

## If a torch upgrade breaks the AMD side

The symptom is `torch.cuda.is_available()` returning false with no explanation.
The cause is that `pip install --upgrade torch` restored the wheel's `/dev/kfd`
HSA runtime over the WSL one.

```bash
openmycelium provision
```

It distinguishes this from a machine that was never set up, and prints the
single `cp` needed — it will not tell you to reinstall ROCm.
