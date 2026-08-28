#!/usr/bin/env bash
# What the installed wheel actually ships under runtime/serving.
P=/opt/om/venv/lib/python3.12/site-packages/openmycelium
echo "  $P"
ls -1 "$P/runtime/serving/" 2>/dev/null | sed 's/^/    /'
echo "  --- cli ---"
ls -1 "$P/runtime/cli/" 2>/dev/null | sed 's/^/    /'
