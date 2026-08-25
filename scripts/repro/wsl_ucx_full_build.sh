#!/usr/bin/env bash
set -uo pipefail
NV=/opt/hetenv/lib/python3.12/site-packages/nvidia
ROOT=/opt/cudaroot

# Rebuild the toolkit root (idempotent; /tmp does not survive).
rm -rf "$ROOT"; mkdir -p "$ROOT/include" "$ROOT/lib64"
ln -sf "$NV/cuda_runtime/include/"* "$ROOT/include/" 2>/dev/null
ln -sfn "$NV/cuda_nvcc/include/crt" "$ROOT/include/crt"
ln -sf "$NV/cuda_nvcc/include/"*.h "$ROOT/include/" 2>/dev/null
ln -sf "$NV/cuda_runtime/lib/libcudart.so.12" "$ROOT/lib64/libcudart.so"
ln -sf "$NV/cuda_runtime/lib/libcudart.so.12" "$ROOT/lib64/libcudart.so.12"
ln -sf /usr/lib/wsl/lib/libcuda.so.1 "$ROOT/lib64/libcuda.so"
ln -sf "$NV/nvml_dev/include/nvml.h" "$ROOT/include/nvml.h"
ln -sf /usr/lib/wsl/lib/libnvidia-ml.so.1 "$ROOT/lib64/libnvidia-ml.so"

printf '#include <cuda_runtime.h>\nint main(void){return 0;}\n' > /tmp/t.c
gcc -I"$ROOT/include" -c /tmp/t.c -o /tmp/t.o || { echo "HEADER TEST FAILED"; exit 1; }
echo "headers ok"

mkdir -p /opt/ucx-src && cd /opt/ucx-src
if [ ! -d ucx-1.18.0 ]; then
  wget -q https://github.com/openucx/ucx/releases/download/v1.18.0/ucx-1.18.0.tar.gz -O ucx.tar.gz || exit 1
  tar xzf ucx.tar.gz
fi
cd ucx-1.18.0

echo "=== configure ==="
./configure --prefix=/opt/ucx-cuda --with-cuda="$ROOT" --without-rocm \
  --enable-mt --without-verbs --without-rdmacm --without-java --without-go \
  --disable-numa > /opt/ucx-configure.log 2>&1
CFG=$?
echo "configure exit: $CFG"
grep -iE 'cuda' /opt/ucx-configure.log | tail -6
[ $CFG -ne 0 ] && { tail -15 /opt/ucx-configure.log; exit 1; }

echo "=== make (this takes a few minutes) ==="
make -j"$(nproc)" > /opt/ucx-make.log 2>&1
echo "make exit: $?"
make install > /opt/ucx-install.log 2>&1
echo "install exit: $?"

echo "=== verify CUDA modules are in ==="
export LD_LIBRARY_PATH="$ROOT/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}"
/opt/ucx-cuda/bin/ucx_info -b 2>/dev/null | grep -iE 'uct_cuda_MODULES|uct_rocm_MODULES'
echo "--- transports ---"
/opt/ucx-cuda/bin/ucx_info -d 2>/dev/null | grep -E 'Transport:' | sort -u
echo "--- memory types ---"
/opt/ucx-cuda/bin/ucx_info -d 2>/dev/null | grep -iE 'memory types' | sort -u
