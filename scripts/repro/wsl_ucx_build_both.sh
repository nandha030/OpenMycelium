#!/usr/bin/env bash
set -uo pipefail
ROOT=/opt/cudaroot
ROCM=/opt/rocm-7.2.0
bash /mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro/cuda_toolkit_root_from_pip.sh /opt/hetenv "$ROOT" >/dev/null 2>&1 || true

cd /opt/ucx-src/ucx-1.18.0 || { echo "no ucx source"; exit 1; }
make clean >/dev/null 2>&1

echo "=== configure with BOTH cuda and rocm ==="
./configure --prefix=/opt/ucx-both --with-cuda="$ROOT" --with-rocm="$ROCM" \
  --enable-mt --without-verbs --without-rdmacm --without-java --without-go \
  --disable-numa > /opt/ucx-both-configure.log 2>&1
echo "configure exit: $?"
grep -iE '^configure:.*(cuda|rocm|modules)' /opt/ucx-both-configure.log | tail -12

echo "=== build ==="
make -j"$(nproc)" > /opt/ucx-both-make.log 2>&1
echo "make exit: $?"
make install > /opt/ucx-both-install.log 2>&1
echo "install exit: $?"

export LD_LIBRARY_PATH=$ROOT/lib64:$ROCM/lib:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
echo "=== ucx_info -v ==="
/opt/ucx-both/bin/ucx_info -v | head -3
echo "=== transports (expect cuda_copy AND rocm_copy) ==="
/opt/ucx-both/bin/ucx_info -d 2>/dev/null | grep -E 'Transport:' | sort -u
echo "=== memory types ==="
/opt/ucx-both/bin/ucx_info -d 2>/dev/null | grep -iE 'memory types' | sort -u
