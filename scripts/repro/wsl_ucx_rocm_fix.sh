#!/usr/bin/env bash
set -uo pipefail
ROCM=/opt/rocm-7.2.0
ROOT=/opt/cudaroot

# WORKAROUND (upstream bug): UCX 1.18 configure appends -lhsakmt unconditionally
# when probing hsa_init. ROCm-for-WSL is DXG-based and ships no libhsakmt; its
# libhsa-runtime64 has no undefined hsaKmt* symbols, so the library is not
# actually needed. Ubuntu's ROCm 5.x libhsakmt.so.1 exists but has no .so
# symlink for the linker. Providing one satisfies the probe; nothing calls it.
if [ ! -e "$ROCM/lib/libhsakmt.so" ] && [ -e /lib/x86_64-linux-gnu/libhsakmt.so.1 ]; then
  ln -sf /lib/x86_64-linux-gnu/libhsakmt.so.1 "$ROCM/lib/libhsakmt.so"
  echo "created workaround symlink: $ROCM/lib/libhsakmt.so"
fi

cd /opt/ucx-src/ucx-1.18.0
make clean >/dev/null 2>&1
./configure --prefix=/opt/ucx-both --with-cuda="$ROOT" --with-rocm="$ROCM" \
  --enable-mt --without-verbs --without-rdmacm --without-java --without-go \
  --disable-numa > /opt/ucx-both-configure.log 2>&1
echo "configure exit: $?"
grep -iE 'hsa_init in|ROCm|ROCM modules|CUDA modules|UCT modules' /opt/ucx-both-configure.log | tail -8

make -j"$(nproc)" > /opt/ucx-both-make.log 2>&1
echo "make exit: $?"
make install > /opt/ucx-both-install.log 2>&1
echo "install exit: $?"

export LD_LIBRARY_PATH=$ROOT/lib64:$ROCM/lib:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}
echo "=== transports ==="
/opt/ucx-both/bin/ucx_info -d 2>/dev/null | grep -E 'Transport:' | sort -u
echo "=== memory types ==="
/opt/ucx-both/bin/ucx_info -d 2>/dev/null | grep -iE 'memory types' | sort -u
