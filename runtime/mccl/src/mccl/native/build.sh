#!/usr/bin/env sh
# Deterministic build of the native components shipped with openmycelium-mccl.
#
# No vendor headers or toolkits are required: the CUDA and ROCm runtimes are
# resolved with dlopen at run time, so one binary works on a CUDA-only host, a
# ROCm-only host, or a host with both.
#
#   sh build.sh [OUTDIR]        default OUTDIR: ./bin
#
# Reproducibility: -O2 with no -march flags, and the sources are byte-identical
# to those recorded in SHA256SUMS in the release.
set -eu
OUT="${1:-./bin}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$OUT"

CFLAGS="-O2 -Wall -Wextra -fno-strict-aliasing"
echo "building bridge          -> $OUT/bridge"
cc $CFLAGS "$HERE/cross_vendor_bridge.c" -o "$OUT/bridge" -ldl
echo "building host probe      -> $OUT/host_access_probe"
cc $CFLAGS "$HERE/host_access_probe.c" -o "$OUT/host_access_probe" -ldl
echo "building sync guard      -> $OUT/sync_guard.so"
cc $CFLAGS -shared -fPIC "$HERE/sync_guard.c" -o "$OUT/sync_guard.so" -ldl
echo
echo "done. Point the CLI at them with:"
echo "  export OM_BRIDGE_BIN=$OUT/bridge"
echo "  export OM_PROBE_BIN=$OUT/host_access_probe"
