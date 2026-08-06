#!/usr/bin/env sh
set -eu
command -v python3 >/dev/null || { echo 'Python 3.10+ is required.' >&2; exit 1; }
echo 'Starting OpenMycelium at http://127.0.0.1:8080'
exec python3 ./openmycelium.py serve
