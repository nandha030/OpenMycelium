#!/usr/bin/env bash
# Why does the freshly provisioned ROCm environment see no device?
#
# torch installed and imports, but torch.cuda.is_available() is false. The
# question is what the pip-installed stack needs from the system that a bare
# Ubuntu image does not have.
E=/var/lib/openmycelium/state/env/rocm
echo "  == the runtime's own account of itself =="
AMD_LOG_LEVEL=3 "$E/bin/python" -c "
import torch
print('    torch     ', torch.__version__)
print('    hip       ', torch.version.hip)
print('    available ', torch.cuda.is_available())
print('    count     ', torch.cuda.device_count())
try:
    torch.zeros(1, device='cuda')
except Exception as error:
    print('    alloc err ', type(error).__name__, str(error)[:300])
" 2>&1 | head -40

echo
echo "  == device nodes =="
ls -l /dev/dxg /dev/kfd 2>&1 | sed 's/^/    /'

echo
echo "  == what torch's HIP library cannot find =="
LIB=$(find "$E" -name 'libamdhip64.so' 2>/dev/null | head -1)
echo "    $LIB"
ldd "$LIB" 2>/dev/null | awk '/not found/ {printf "    MISSING %s\n", $1}'
echo "    (no MISSING lines above means every direct dependency resolves)"

echo
echo "  == the HSA runtime torch ships =="
HSA=$(find "$E" -name 'libhsa-runtime64.so.1' 2>/dev/null | head -1)
echo "    $HSA"
ldd "$HSA" 2>/dev/null | awk '/not found/ {printf "    MISSING %s\n", $1}'
ldd "$HSA" 2>/dev/null | awk '/dxcore|drm|numa|elf/ {printf "    %s\n", $0}'

echo
echo "  == system packages the working distro has and this one may not =="
for p in libnuma1 libdrm2 libelf1 libzstd1 pciutils kmod; do
  if dpkg -s "$p" >/dev/null 2>&1; then echo "    present  $p"; else echo "    ABSENT   $p"; fi
done

echo
echo "  == HSA topology as seen through dxcore =="
"$E/bin/python" -c "
import ctypes, os
os.environ['AMD_LOG_LEVEL'] = '4'
try:
    lib = ctypes.CDLL('$HSA')
    rc = lib.hsa_init()
    print('    hsa_init returned', rc, '(0 is success)')
except Exception as error:
    print('    could not load the HSA runtime:', error)
" 2>&1 | tail -20
