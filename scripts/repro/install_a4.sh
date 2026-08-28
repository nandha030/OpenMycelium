#!/usr/bin/env bash
# Install 0.2.0a4 and confirm the fixes are actually in the installed files.
set -uo pipefail
WHEEL=/mnt/c/Users/User/Documents/Open_Mycelium/dist/openmycelium-0.2.0a4-py3-none-any.whl
OM=/opt/om/venv
P="$OM/lib/python3.12/site-packages/openmycelium/runtime/cli"

echo "  == stop the console that is serving the old code =="
# It is the user's foreground process in PowerShell; the fix cannot take effect
# while it is running, and restarting it is one command.
pkill -f "openmycelium console" 2>/dev/null && echo "    stopped the running console" \
  || echo "    no console was running"
sleep 2

echo
echo "  == install =="
"$OM/bin/pip" install --force-reinstall --no-deps "$WHEEL" 2>&1 | tail -2 | sed 's/^/    /'

echo
echo "  == what is installed now =="
echo "    version:    $("$OM/bin/openmycelium" version 2>/dev/null | sed -n '2p' | tr -s ' ')"
echo "    console.py: $(grep -c 'BrokenPipeError' "$P/console.py") broken-pipe guards"
echo "    console.py: $(grep -c 'class ConsoleServer' "$P/console.py") quiet server class"
echo "    console.py: $(grep -c 'favicon' "$P/console.py") favicon route"
echo "    service:    $(grep -c 'READINESS_TTL' "$P/console_service.py") cache references"
echo "    app.js:     $(grep -c 'pollReadiness' "$P/console_assets/app.js") non-overlapping poll"
if [ -f "$P/console_assets/openmycelium-logo.png" ]; then
  echo "    logo:       $(stat -c%s "$P/console_assets/openmycelium-logo.png") bytes"
else
  echo "    logo:       MISSING"
fi
