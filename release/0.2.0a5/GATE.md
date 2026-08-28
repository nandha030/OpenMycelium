# 0.2.0a5 — installed-wheel hardware gate

**Result: passed, zero failures.**

Qualifies the local operator console and the MRouter/MCCL rename on real
hardware, from installed wheels rather than the source tree.

## Artifacts

```
openmycelium-0.2.0a5-py3-none-any.whl        ec009d12fd0ab29997ef9f31c16095b05c14062610528f31bc8d92e2583f329b
openmycelium_mccl-0.2.0a3-py3-none-any.whl   9575a8cc3c3bec14c59c40302c31f19fbfe955d95526b9391c5bc5ec442f8dfe
openmycelium_mccl-0.2.0a3.tar.gz             0f1155942cc5b8efbd7c54731167906318c02d1f823fc174b10e217ec3c141e2

installedContentSha256   74729745a44f79b13845786134852c3513cb6d89867d52009c8440ed488b0999   (48 files)
```

## Dependency pin

Read from the built wheel's own `METADATA`, not from the source that generated
it:

```
Version: 0.2.0a5
Requires-Dist: openmycelium-mccl==0.2.0a3
pinVerified: true
```

An offline resolve into a clean prefix produced `openmycelium==0.2.0a5` and
`openmycelium-mccl==0.2.0a3`.

## Runtime and hardware tuple

```
cuda     NVIDIA GeForce RTX 5060 Ti   torch 2.11.0+cu128
rocm     AMD Radeon RX 9060 XT        torch 2.10.0+rocm7.0
kernel   6.18.33.2-microsoft-standard-WSL2
distro   Ubuntu 24.04.4 LTS
mccl     0.2.0a3, activation protocol 1, host-staged-xvendor
hsa      dxcore, system-provided, 83bedf380844ae17…
model    Mistral-Nemo-Instruct-2407
```

## Placement

```
boundary after layer 19
tensors  [181, 182], 363 total, overlap 0
cuda     layers 0-19    11.41 GiB of 14 GiB budget    nvidia:device-0
rocm     layers 20-39   11.41 GiB of 14 GiB budget    amd:device-0
```

Device identities are placeholders here. Raw identities remain in the frozen
v0.1.0 evidence, where a measurement has to stay attributable to a card.

## Branding, from the installed package

```
mccl.__version__        0.2.0a3
MRouter in root         True
HetRouter importable    True
HetRouter in __all__    False
star import offers      MRouter=True  HetRouter=False
```

## Gate result

| Check | Result |
|---|---|
| Wheels installed, versions reported | `openmycelium 0.2.0a5`, `mccl 0.2.0a3` |
| Port 11501 free before starting | pass |
| Console started, and is the process still running | pass |
| Assets served from inside the package | `/`, `/assets/app.js`, `/assets/styles.css` all 200 |
| Run gate: readiness, verify, placement, idle | 4 of 4 |
| Expected allocation shown before start | both stages, 11.41 GiB each |
| Real dual-GPU run | **TTFT 182.5 ms, 11.18 tok/s, 24 tokens** |
| Event stream | `started=1, stdout=627, worker=9, result=1, finished=1` |
| Zero orphan workers | pass |
| VRAM released | CUDA 1142 MiB, ROCm 79 MiB |
| stdout clean, diagnostics on stderr | pass |

Gate harness: `scripts/repro/console_wheel_gate.sh` at commit `4f7b2ff`.

## The first attempt was VOID

An earlier run of this gate reported one failure. **That result is void and must
not be cited.** It did not test `0.2.0a5`.

A console from an earlier session — pid 389, running `0.2.0a4`, up 92 minutes —
still held port 11501. The gate's own console failed to bind with
`OSError: [Errno 98] Address already in use`, and every subsequent request was
answered by that stale process. The evidence of it is in the output that was
produced: `started=2` from replayed transcript, 64 generated tokens where the
gate requested 24, and generated text answering a prompt the gate never sent.
The "orphan workers" were a run the gate had triggered on the stale console,
31 seconds old and still finishing — not leaked.

The harness was at fault: it treated "console reachable" as proof its own
console had started. It now refuses to run if the port is occupied, and asserts
the process it spawned is still alive before making any claim. A gate that
silently tests the wrong binary is worse than one that fails.

The stale console was stopped, its run drained to zero workers, and the gate
re-run from a confirmed-idle machine. The table above is that run.

## Scope

This gate qualifies the console's read paths, one dual-GPU run and safe stop.
It does not qualify: model pull, import or remove through the console;
provisioning or configuration changes; persistent chat; API-server management;
non-loopback access; or any MHub control-plane integration, which does not
exist.

Visual rendering of the console was **not** certified — the browser could not
be rendered in the environment that built and gated it.
