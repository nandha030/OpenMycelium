#!/usr/bin/env bash
UCX=/opt/ucx-both; ROCM=/opt/rocm-7.2.0; ROOT=/opt/cudaroot
export LD_LIBRARY_PATH=$UCX/lib:$ROOT/lib64:$ROCM/lib:/usr/lib/wsl/lib
export UCX_MEMTYPE_CACHE=n UCX_HANDLE_ERRORS=bt
ulimit -c unlimited
S=/mnt/c/Users/User/Documents/Open_Mycelium/scripts/repro

echo "############ CONTROL: is hipMalloc memory CPU-accessible here? ############"
gcc "$S/hip_hostaccess_probe.c" -o /opt/bin/hostaccess -I$ROCM/include -L$ROCM/lib -lamdhip64 2>&1 | head -5
/opt/bin/hostaccess

echo
echo "############ backtrace: UCX_TLS=tcp, hipMalloc ############"
UCX_TLS=tcp UCX_LOG_LEVEL=error timeout 40 /opt/bin/repro --mem hipmalloc --size 4096 2>&1 | grep -E 'posting|progress|uct_|ucp_|signal|address|RESULT' | head -14

echo
echo "############ backtrace: UCX_TLS=rocm_copy,tcp, hipMalloc ############"
UCX_TLS=rocm_copy,tcp UCX_LOG_LEVEL=error timeout 40 /opt/bin/repro --mem hipmalloc --size 4096 2>&1 | grep -E 'posting|progress|uct_|ucp_|signal|address|RESULT' | head -14
