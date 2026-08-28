#!/usr/bin/env bash
# Does the pip-installed ROCm torch actually carry an HSA runtime?
#
# The diagnostic found none in the fresh environment, while the working
# environment has one in torch/lib with a .orig backup beside it -- the
# signature of a file that was put there after installation, not by pip.
E=${1:-/var/lib/openmycelium/state/env/rocm}
LIBDIR="$E/lib/python3.12/site-packages/torch/lib"
echo "  == $LIBDIR =="
ls "$LIBDIR" 2>/dev/null | grep -iE 'hsa|amdhip|rocm|comgr' | sed 's/^/    /' \
  || echo "    directory not found"
echo
echo "  count of libhsa* files: $(ls "$LIBDIR" 2>/dev/null | grep -c '^libhsa')"
echo
echo "  == where libamdhip64 looks for its HSA runtime =="
H="$LIBDIR/libamdhip64.so"
if [ -e "$H" ]; then
  ldd "$H" 2>/dev/null | grep -iE 'hsa|not found' | sed 's/^/    /'
else
  echo "    libamdhip64.so absent from torch/lib"
fi
echo
echo "  == system ROCm =="
if [ -d /opt/rocm ]; then echo "    /opt/rocm present"; else echo "    /opt/rocm ABSENT"; fi
ls /usr/lib/x86_64-linux-gnu/libhsa-runtime64.so.1 2>/dev/null | sed 's/^/    /' \
  || echo "    no system libhsa-runtime64.so.1"
