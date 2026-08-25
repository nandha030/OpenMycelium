#!/usr/bin/env bash
S=/mnt/c/Users/User/Documents/Open_Mycelium
mkdir -p /opt/bin
gcc "$S/runtime/bridge/cross_vendor_bridge.c" -o /opt/bin/bridge -O2 -Wall -ldl 2>&1 | head -20
[ -x /opt/bin/bridge ] && echo "BUILD OK" || echo "BUILD FAILED"
