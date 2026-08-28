#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX=${MCCL_PREFIX:-"$HOME/.local"}

printf '%s\n' 'OM  OPENMYCELIUM / MCCL'
printf '%s\n' '    Portable heterogeneous collective runtime'
command -v python3 >/dev/null 2>&1 || { printf '%s\n' 'Python 3.9 or newer is required.' >&2; exit 1; }
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'

printf '%s\n' '[1/3] Installing the MCCL Python SDK and CLI'
python3 -m pip install --user --upgrade "$ROOT"

if [ "${MCCL_BUILD_NATIVE:-0}" = "1" ]; then
  command -v cmake >/dev/null 2>&1 || { printf '%s\n' 'CMake 3.24 or newer is required.' >&2; exit 1; }
  CUDA_OPTION=OFF
  ROCM_OPTION=OFF
  [ "${MCCL_ENABLE_CUDA:-0}" = "1" ] && CUDA_OPTION=ON
  [ "${MCCL_ENABLE_ROCM:-0}" = "1" ] && ROCM_OPTION=ON
  printf '%s\n' '[2/3] Building selected native adapters'
  cmake -S "$ROOT/native" -B "$ROOT/build" -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DMCCL_ENABLE_CUDA="$CUDA_OPTION" -DMCCL_ENABLE_ROCM="$ROCM_OPTION"
  cmake --build "$ROOT/build" --parallel
  cmake --install "$ROOT/build"
else
  printf '%s\n' '[2/3] Native adapters skipped; set MCCL_BUILD_NATIVE=1 after installing a vendor SDK'
fi

printf '%s\n' '[3/3] Running capability discovery'
python3 -m mccl.cli doctor
printf '%s\n' 'MCCL installation complete.'
