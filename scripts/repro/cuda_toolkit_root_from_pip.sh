#!/usr/bin/env bash
# Assemble a CUDA "toolkit root" sufficient to COMPILE against, using only pip
# wheels plus the WSL driver passthrough. No CUDA Toolkit install required.
#
# Why: building UCX (or anything) with --with-cuda needs cuda.h, cuda_runtime.h,
# crt/host_config.h, nvml.h, libcudart and libcuda. The full toolkit is ~3 GB;
# the three pip wheels below are roughly 200 MB and supply every header needed.
#
#   nvidia-cuda-runtime-cu12  -> cuda.h, cuda_runtime.h, libcudart.so.12
#   nvidia-cuda-nvcc-cu12     -> crt/host_config.h  (the piece most often missing)
#   nvidia-nvml-dev-cu12      -> nvml.h
#   /usr/lib/wsl/lib          -> libcuda.so.1, libnvidia-ml.so.1 (driver, from WSL)
#
# Usage:  ./cuda_toolkit_root_from_pip.sh [VENV_PYTHON_PREFIX] [OUTPUT_ROOT]
#   e.g.  ./cuda_toolkit_root_from_pip.sh /opt/hetenv /opt/cudaroot
#   then: ./configure --with-cuda=/opt/cudaroot
set -euo pipefail

VENV="${1:-/opt/hetenv}"
ROOT="${2:-/opt/cudaroot}"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"

[ -x "$PY" ] || { echo "no python at $PY" >&2; exit 1; }

echo "==> installing the three header-bearing wheels"
"$PIP" install -q nvidia-cuda-runtime-cu12 nvidia-cuda-nvcc-cu12 nvidia-nvml-dev-cu12

SITE="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
NV="$SITE/nvidia"
[ -d "$NV" ] || { echo "no nvidia packages under $SITE" >&2; exit 1; }

echo "==> assembling $ROOT"
rm -rf "$ROOT"
mkdir -p "$ROOT/include" "$ROOT/lib64"

ln -sf "$NV/cuda_runtime/include/"*        "$ROOT/include/" 2>/dev/null || true
ln -sfn "$NV/cuda_nvcc/include/crt"        "$ROOT/include/crt"
ln -sf "$NV/cuda_nvcc/include/"*.h         "$ROOT/include/" 2>/dev/null || true
ln -sf "$NV/nvml_dev/include/nvml.h"       "$ROOT/include/nvml.h"

ln -sf "$NV/cuda_runtime/lib/libcudart.so.12" "$ROOT/lib64/libcudart.so"
ln -sf "$NV/cuda_runtime/lib/libcudart.so.12" "$ROOT/lib64/libcudart.so.12"
# Driver libraries are supplied by the WSL passthrough, never by the wheels.
ln -sf /usr/lib/wsl/lib/libcuda.so.1          "$ROOT/lib64/libcuda.so"
ln -sf /usr/lib/wsl/lib/libnvidia-ml.so.1     "$ROOT/lib64/libnvidia-ml.so"

echo "==> verifying the headers actually compile"
printf '#include <cuda_runtime.h>\n#include <nvml.h>\nint main(void){return 0;}\n' > /tmp/_cudaroot_check.c
gcc -I"$ROOT/include" -c /tmp/_cudaroot_check.c -o /tmp/_cudaroot_check.o
rm -f /tmp/_cudaroot_check.c /tmp/_cudaroot_check.o

echo "OK: $ROOT is usable as --with-cuda=$ROOT"
ls "$ROOT/lib64"
