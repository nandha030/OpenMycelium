# Platform support

What runs where, stated by evidence rather than intention.

| Platform | Node Runtime | MHub Control Plane |
|---|---|---|
| **Windows 11 + WSL2** | **Validated** | Installable, not connected to a runtime |
| **Native Linux** | Experimental, unqualified | Installable, not connected to a runtime |
| **macOS** | **Unsupported** | Installable, not connected to a runtime |

Two components, and they have different answers. The **Node Runtime** is the
Python engine that executes a model across an NVIDIA and an AMD GPU. **MHub**
is the Go control plane. MHub can be installed anywhere Docker runs, but it has
never invoked the Node Runtime on any platform, so installing it does not give
you working cross-vendor inference.

---

## Windows 11 with WSL2 — validated

The only configuration with hardware evidence behind it.

```
Windows 11 Pro 10.0.26200
WSL2, kernel 6.18.33.2-microsoft-standard-WSL2
Ubuntu 24.04.4 LTS, Python 3.12.3
NVIDIA GeForce RTX 5060 Ti 16 GB, torch 2.11.0+cu128
AMD Radeon RX 9060 XT 16 GB, torch 2.10.0+rocm7.0
Mistral-Nemo-Instruct-2407, 22.84 GiB
```

Both GPUs are reached through `/dev/dxg`, WSL's paravirtualisation device.
There is no `/dev/kfd`, and `rocm-smi` does not work — it needs the `amdgpu`
kernel module, which WSL does not have. That is expected, not a fault.

**One manual step is required and cannot be skipped:** AMD's ROCm-for-WSL
system components, plus substituting their HSA runtime into torch. The PyTorch
ROCm wheel ships a runtime built for `/dev/kfd`; under WSL it imports cleanly,
finds no device, and explains nothing.

Full instructions: [OPERATIONS.md](OPERATIONS.md) Part 1.

## Native Linux — experimental, unqualified

The runtime detects `/dev/kfd` and has code paths for it, but **has only ever
executed on `/dev/dxg` under WSL**. Nothing here has hardware evidence. Expect
to debug.

Two differences matter, and getting the second wrong will break a working
install:

- No `openmycelium.cmd`. Call the installed command directly; `OPENMYCELIUM_WSL_DISTRO` and `OPENMYCELIUM_VENV` are meaningless.
- **Do not perform the HSA runtime substitution.** On native Linux `/dev/kfd` exists and the runtime shipped in the PyTorch ROCm wheel is the correct one. That step exists *only* because WSL lacks `/dev/kfd`.

`rocm-smi` should work here, unlike under WSL. Install ROCm through your
distribution's supported method, including the kernel driver.

```bash
python3 -m venv /opt/openmycelium/venv
/opt/openmycelium/venv/bin/pip install openmycelium_mccl-*.whl openmycelium-*.whl
export PATH=/opt/openmycelium/venv/bin:$PATH

openmycelium provision
openmycelium doctor
openmycelium model import /path/to/model
openmycelium run --model MODEL --prompt "..."
```

If you qualify this on real hardware, the evidence would be welcome.

## macOS — the Node Runtime is unsupported

**Not "untested". Unsupported, for a structural reason.**

The runtime requires one CUDA device and one ROCm device in the same machine.
macOS provides neither: Apple dropped NVIDIA driver support years ago, and
ROCm has never targeted macOS. No Mac — Intel or Apple Silicon — can present
the CUDA + ROCm pair the runtime is built around. This is not a gap that
testing would close.

What you *can* run on macOS:

- **MHub control plane.** There is a macOS Docker installer. It manages clusters, models, workspaces and Kubernetes, and it has never invoked a Node Runtime on any platform. Installing it does not give you cross-vendor inference.
- **The MCCL package**, for planning and discovery. Its Metal adapter identifies Apple GPUs. This is device identification and planning, not execution.

**Apple Metal execution is roadmap.** The MCCL package contains Metal staging
code and the Lab can model Apple topologies, but no model has been executed on
Metal through this project, and none of it is qualified. Treat any Metal
capability listed in [FEATURES.md](../FEATURES.md) as roadmap until it appears
under hardware-qualified.

### If you want to use OpenMycelium from a Mac

Run the Node Runtime on a Windows or Linux machine that has both GPUs, serve
the OpenAI-compatible API, and connect to it from the Mac:

```bash
# on the machine with the GPUs
OPENMYCELIUM_TOKEN=$(cat /run/openmycelium/token) \
  openmycelium serve --model MODEL --host 0.0.0.0

# from the Mac
curl -H "Authorization: Bearer $TOKEN" http://<host>:11500/v1/models
```

Pass the token through the environment, never `--token` — that puts the secret
in the process command line. There is no TLS; put it behind a reverse proxy if
it crosses a network you do not control. See [SECURITY.md](SECURITY.md).

---

## Hardware

One NVIDIA GPU and one AMD GPU per pipeline are qualified.

**Multi-AMD is not qualified.** The ledger records the AMD PCI bus number
rather than the full BDF, which cannot reliably distinguish multiple functions
or devices sharing a bus topology. Full BDF is mandatory before multi-AMD
support.

Two GPUs of the same vendor, more than two GPUs, multi-node, Intel, NPU and TPU
are all unqualified. See [HARDWARE_MATRIX.md](HARDWARE_MATRIX.md).
