# Evidence

Measurements from one machine, kept so a claim can be traced to the run that
produced it. **None of it transfers to other hardware.** A different GPU, driver,
WSL kernel, or ROCm version is unqualified until it runs the qualification
itself.

## Files

| File | What it is |
|---|---|
| `qualification-report-DESKTOP-DTHF5UD.json` | `hetccl diagnose` output at 0.2.0a1 |

## The machine

| | |
|---|---|
| NVIDIA | GeForce RTX 5060 Ti, driver 591.86 |
| AMD | Radeon RX 9060 XT (gfx1200) |
| ROCm | 7.2.0, WSL DXG path (no `/dev/kfd`) |
| Windows | 10.0.26200.9168, Adrenalin 26.8.1 |
| WSL kernel | 6.18.33.2-microsoft-standard-WSL2 |
| Distro | Ubuntu 24.04 |

## Reproducing it

Scripts are in `scripts/repro/`. They assume the layout above and are written
for this machine; treat them as a record of what was run, not a portable
installer.

```sh
# authoritative test suite
bash scripts/repro/wsl_test_hetccl.sh

# build the package and validate it in a clean venv
bash scripts/repro/wsl_package.sh

# record a qualification for each direction (byte-verified, CRC enabled)
bash scripts/repro/wsl_qualify_directions.sh

# transport
bash scripts/repro/wsl_stress.sh ci          # 100 transfers
bash scripts/repro/wsl_stress.sh stress      # 10,000 transfers
bash scripts/repro/wsl_faults.sh
bash scripts/repro/wsl_forced_backpressure.sh

# collectives
bash scripts/repro/wsl_broadcast.sh
bash scripts/repro/wsl_allgather.sh
bash scripts/repro/wsl_mux_live.sh

# pipeline inference across both vendors
bash scripts/repro/wsl_pipeline.sh
bash scripts/repro/wsl_pipeline_sizes.sh
```

## Upstream defect evidence

`ucx_rocm_recv_repro.c` is the minimal reproducer for the UCX defect described
in the package README: receiving into `hipMalloc` memory faults on the WSL DXG
path, while `malloc` and `hipHostMalloc` receive buffers are unaffected.
`hip_hostaccess_probe.c` establishes the underlying fact — that ROCm device
memory there answers neither a host load nor a host store.

Run them with `wsl_repro_run.sh` and `wsl_repro_trace.sh`.

## Build artifacts

Wheels and sdists are **not** committed. They embed timestamps and are not
byte-reproducible: two builds of identical source produced different digests.
Hashes for a specific build belong in that release's notes, next to the
artifacts they describe.
