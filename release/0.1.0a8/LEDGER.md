# 0.1.0a8 — release ledger

## Artifact identity

```
openmycelium-0.1.0a8-py3-none-any.whl   eadb44b70164554a49a498f8303fe4ccb086961ad9ca2d764e35f5601901920e
openmycelium_mccl-0.2.0a2-py3-none-any.whl  ce766054068ab627dc6b6930f562819c70e687fdadf3811dc3ba5eed33243694
openmycelium_mccl-0.2.0a2.tar.gz            da41062e1b54bc3426ba1f8a546f81d7ddb405e4976baf97e3cb3a32e9c11089

installedContentSha256  b430eff6e79cd56b30841716431014c7b566942704b8e4c1df5c9336ad43981a  (46 files)
```

## Superseded version identities

Two earlier builds carry the string `0.1.0a7`. Neither may be published.

| Build | Wheel SHA-256 | Status |
|---|---|---|
| `0.1.0a7` candidate-1 | `eb8f44e6…5698f8` | SUPERSEDED-NONRELEASABLE — staged before validation; reinstall detection did not fire |
| `0.1.0a7` rebuilt | `8d94b04e…8751b8` | NONRELEASABLE — technically validated, but the version identity was already spent |

A version that maps to two artifacts identifies nothing. Versions name
immutable artifacts, not roadmap positions.

## Diff from the rebuilt `0.1.0a7`

**Not** version and provenance metadata alone. `0.1.0a8` also changes ROCm
diagnosis:

- `packaging/launcher.py`, `runtime/cli/lifecycle.py`,
  `scripts/build_openmycelium_wheel.py` — version string
- `runtime/cli/rocm_prereq.py` — environment identity tuple: `IDENTITY_FIELDS`,
  `rocm_system_version()`, `environment_identity()`, `identity_matches()`, PCI
  bus captured in `_torch_facts`, identity recorded in `detect()`, and the
  regression branch now gated on the identity matching
- `runtime/cli/provision.py`, `runtime/cli/doctor.py` — pass the resolved state
  directory into detection

**Because that diff is functional, no result was inherited.** Every gate below
was re-run against these exact bytes, including the reboot.

## Results, all against `eadb44b7…`

| Gate | Result |
|---|---|
| Frozen wheels re-verified | 2 of 2 match `SHA256SUMS.frozen` |
| Installed-wheel provenance | version `0.1.0a8`, content `b430eff6…`, HSA runtime `dxcore` / system-provided |
| Configuration | `wsl_distro` = `om-clean2` from `WSL_DISTRO_NAME`; no `Ubuntu-24.04` default |
| Both GPU probes | RTX 5060 Ti and RX 9060 XT, real BF16 on device, no fallback |
| Idempotent provisioning | 6 s, both already usable, nothing downloaded |
| Ledger identity tuple | all eight fields populated |
| `run` | TTFT 156 ms, 10.99 tok/s, 181/182 exclusive |
| `chat` multi-turn | TTFT 145 ms, 10.95 tok/s |
| `serve` API | 11 of 11 protocol checks; 401 / 401 / 200 token enforcement, non-loopback |
| `ps` / `stop` | zero orphan workers |
| Reboot | cold VM at 3 s uptime, boot id `df5aaf29…`, both requalified, `doctor` ready |
| Wheelhouse offline, ROCm | 36 packages, `--no-index`, 2 min 19 s, torch `2.10.0+rocm7.0` |
| Wheelhouse offline, CUDA | 54 packages, `--no-index`, 1 min 46 s, torch `2.11.0+cu128`, real BF16 on the RTX 5060 Ti |
| Throughput campaign | see `THROUGHPUT.md`; every invariant held |

The wheelhouse is complete: **90 of 90 files**, both halves installed offline
with the network refused, each finishing on its own physical GPU. System-level
ROCm apt packages are outside it by construction — it holds Python wheels, and
the AMD prerequisite is a separate manual step that is not cached.

## Hardware support matrix — v0.1

**One NVIDIA GPU and one AMD GPU per OpenMycelium pipeline are qualified.
Multi-AMD identity and scheduling are not qualified in v0.1.**

The ledger's AMD identity records the PCI **bus number**, not the full BDF. That
is sufficient evidence on this validated single-AMD machine, but it cannot
reliably distinguish multiple functions or multiple AMD devices sharing a bus
topology. Full BDF is mandatory before any multi-AMD support.

## Known weakness in the identity tuple

`amdPciId` records the PCI **bus number** (`4`), not the full BDF
(`0000:04:00.0`) that `fabric` uses. It distinguishes cards on different buses
but is weaker than intended. The decisive fields — `wslDistro`, `rocmPython`,
`stateDir`, `torchLibSha256`, `rocmSystemVersion` — already prevent
cross-environment misattribution, so this is a refinement for a later version,
recorded here rather than fixed by churning another version identity.

## ROCm prerequisite — claim boundaries

**Supported path.** AMD's documented installation:
`amdgpu-install --usecase=wsl,rocm --no-dkms`.

**Experimental path, validated only on this exact configuration.** On the
validated configuration, OpenMycelium experimentally reduced the prerequisite to
two AMD packages totalling approximately 0.5 MiB, because the selected PyTorch
wheel already contained the remaining user-space libraries:

```
rocm-core                    7.2.0.70200-43~24.04
hsa-runtime-rocr4wsl-amdgpu  25.30.13-2281980.24.04     508,808 bytes
```

This is **not** a general claim about ROCm. It holds for this WSL build, this
GPU, this ROCm version and this PyTorch wheel, and for nothing else that has
been tested. Do not advertise "ROCm requires only 0.5 MiB".

Repositories used, at the versions the reference system runs:

```
https://repo.radeon.com/rocm/apt/7.2 noble main
https://repo.radeon.com/graphics/7.2/ubuntu noble main
https://repo.radeon.com/amdgpu/30.30/ubuntu noble main
signing key sha256  3e660bdc559b34781b5498e79b9c117394b46ce55592812db0b84d65986b4df2
```

The key was downloaded from AMD and verified byte-identical to the one already
trusted on the reference system before any repository was added.

## What this closes

Documented clean reproduction with a manual system prerequisite.

## What this does not close

Fully automatic clean system provisioning. The privileged installer
(`provision --install-system-rocm`) is future work with no reserved version
number.
