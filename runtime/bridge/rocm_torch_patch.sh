#!/usr/bin/env bash
# PROVISIONAL WORKAROUND -- NOT A SUPPORTED INSTALLATION STEP.
#
# PyTorch ROCm wheels bundle their own libhsa-runtime64 in torch/lib. On
# ROCm-for-WSL (DXG path) the bundled runtime does not enumerate the GPU:
# torch reports device_count 0, is_available False, arch list empty, even
# though rocminfo and raw HIP both work.
#
# Replacing it with the system runtime makes torch see the GPU. It also creates
# a MIXED-RUNTIME CONFIGURATION that neither AMD nor the PyTorch project
# supports: a wheel built against ROCm 7.0 loading a ROCm 7.2 HSA runtime.
#
# Risks, stated plainly:
#   * HSA is a C ABI with no strong version guarantee across minor releases.
#     A silent behavioural difference is possible and would not announce itself.
#   * Any `pip install --upgrade torch` overwrites the replacement and silently
#     restores the broken state. Re-run this script after every torch update.
#   * Numerical results from this configuration should not be treated as
#     production evidence without independent confirmation on a supported stack.
#
# The script therefore records hashes of both libraries, keeps a restorable
# backup, refuses on a large version gap unless forced, and runs a real kernel
# before declaring success.
#
#   ./rocm_torch_patch.sh apply   [venv] [rocm_root]
#   ./rocm_torch_patch.sh status  [venv] [rocm_root]
#   ./rocm_torch_patch.sh restore [venv]
set -uo pipefail

ACTION="${1:-status}"
VENV="${2:-/opt/rocmenv}"
ROCM="${3:-/opt/rocm}"
LIBDIR="$VENV/lib/python3.12/site-packages/torch/lib"
MANIFEST="$LIBDIR/.om_hsa_patch.json"
PY="$VENV/bin/python"

die() { echo "ERROR: $*" >&2; exit 1; }
hash_of() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }

[ -d "$LIBDIR" ] || die "no torch lib directory at $LIBDIR"

torch_rocm_version() {
    "$PY" -c 'import torch; print(torch.version.hip or "none")' 2>/dev/null | head -1
}
system_rocm_version() {
    cat "$ROCM/.info/version" 2>/dev/null | cut -d- -f1 || echo unknown
}

# ---------------------------------------------------------------- ABI check
abi_check() {
    local wheel_hip system_rocm wheel_major system_major wheel_minor system_minor
    wheel_hip=$(torch_rocm_version)
    system_rocm=$(system_rocm_version)
    echo "  wheel HIP version : $wheel_hip"
    echo "  system ROCm       : $system_rocm"

    wheel_major=${wheel_hip%%.*}
    system_major=${system_rocm%%.*}
    if [ "$wheel_major" != "$system_major" ]; then
        echo "  ABI verdict       : REFUSE (major version differs: $wheel_major vs $system_major)"
        return 1
    fi
    wheel_minor=$(echo "$wheel_hip" | cut -d. -f2)
    system_minor=$(echo "$system_rocm" | cut -d. -f2)
    if [ "$wheel_minor" != "$system_minor" ]; then
        echo "  ABI verdict       : MIXED MINOR ($wheel_major.$wheel_minor vs $system_major.$system_minor) -- unsupported but permitted"
        return 0
    fi
    echo "  ABI verdict       : matched"
    return 0
}

# ---------------------------------------------------------------- smoke test
smoke_test() {
    "$PY" - <<'PY' 2>&1 | grep -vE 'NumPy|conversion_method|Warning: Resource'
import sys
try:
    import torch
except Exception as e:
    print("SMOKE FAIL import:", e); sys.exit(1)
if not torch.cuda.is_available():
    print("SMOKE FAIL: no device visible"); sys.exit(1)
name = torch.cuda.get_device_name(0)
archs = torch.cuda.get_arch_list()
# A real kernel, not just enumeration: enumeration succeeded before the
# patch on some configurations while compute did not.
x = torch.arange(4096, dtype=torch.float32, device="cuda")
got = float((x * 3 + 1).sum().item())
want = float(sum(i * 3 + 1 for i in range(4096)))
mm = (torch.ones(256, 256, device="cuda") @ torch.ones(256, 256, device="cuda")).sum().item()
if got != want or mm != 256 * 256 * 256:
    print(f"SMOKE FAIL numerics: reduce {got} vs {want}, matmul {mm}"); sys.exit(1)
print(f"SMOKE PASS device={name} archs={len(archs)} reduce+matmul verified")
PY
    return ${PIPESTATUS[0]}
}

# -------------------------------------------------------------------- status
show_status() {
    echo "=== ROCm torch HSA patch status ==="
    echo "  venv              : $VENV"
    if [ -f "$MANIFEST" ]; then
        echo "  patched           : YES"
        "$PY" -c "import json;d=json.load(open('$MANIFEST'));[print(f'  {k:<18}: {v}') for k,v in d.items()]" 2>/dev/null \
            || cat "$MANIFEST"
        local current
        current=$(hash_of "$LIBDIR/libhsa-runtime64.so")
        local recorded
        recorded=$("$PY" -c "import json;print(json.load(open('$MANIFEST')).get('replacement_sha256',''))" 2>/dev/null)
        if [ "$current" = "$recorded" ]; then
            echo "  integrity         : intact"
        else
            echo "  integrity         : DRIFTED -- torch was likely upgraded; re-run 'apply'"
        fi
    else
        echo "  patched           : no"
    fi
    echo
    abi_check
}

case "$ACTION" in
apply)
    echo "=== PROVISIONAL: patching bundled HSA runtime ==="
    echo "This creates an unsupported mixed-runtime configuration. See header."
    echo
    abi_check || die "refusing: ROCm major version mismatch"

    SRC=$(readlink -f "$ROCM/lib/libhsa-runtime64.so.1" 2>/dev/null)
    [ -f "$SRC" ] || die "no system HSA runtime at $ROCM/lib/libhsa-runtime64.so.1"
    TARGET="$LIBDIR/libhsa-runtime64.so"
    [ -f "$TARGET" ] || die "no bundled runtime at $TARGET"

    ORIG_HASH=$(hash_of "$TARGET")
    NEW_HASH=$(hash_of "$SRC")
    echo
    echo "  original (bundled): $ORIG_HASH"
    echo "  replacement (sys) : $NEW_HASH"
    if [ "$ORIG_HASH" = "$NEW_HASH" ]; then
        echo "  already identical; nothing to do"; exit 0
    fi

    for f in "$LIBDIR"/libhsa-runtime64.so*; do
        case "$f" in *.orig) continue;; esac
        [ -e "$f.orig" ] || cp -P "$f" "$f.orig"
    done
    cp -f "$SRC" "$LIBDIR/libhsa-runtime64.so"
    [ -e "$LIBDIR/libhsa-runtime64.so.1" ] && cp -f "$SRC" "$LIBDIR/libhsa-runtime64.so.1"

    cat > "$MANIFEST" <<JSON
{
  "provisional": true,
  "supported": false,
  "reason": "PyTorch ROCm wheel bundles an HSA runtime that does not enumerate the GPU on the WSL DXG path",
  "original_sha256": "$ORIG_HASH",
  "replacement_sha256": "$NEW_HASH",
  "replacement_source": "$SRC",
  "wheel_hip_version": "$(torch_rocm_version)",
  "system_rocm_version": "$(system_rocm_version)",
  "applied_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "warning": "pip install --upgrade torch will silently overwrite this and restore the broken state; re-run apply"
}
JSON
    echo
    echo "=== smoke test ==="
    if smoke_test; then
        echo
        echo "PATCH APPLIED (provisional). Restore with: $0 restore $VENV"
    else
        echo "smoke test failed; restoring"
        for f in "$LIBDIR"/libhsa-runtime64.so*.orig; do
            cp -f "$f" "${f%.orig}"
        done
        rm -f "$MANIFEST"
        die "patch did not produce a working device; original restored"
    fi
    ;;
restore)
    echo "=== restoring bundled runtime ==="
    found=0
    for f in "$LIBDIR"/libhsa-runtime64.so*.orig; do
        [ -e "$f" ] || continue
        cp -f "$f" "${f%.orig}"
        echo "  restored ${f%.orig}"
        found=1
    done
    [ $found -eq 1 ] || die "no backup found"
    rm -f "$MANIFEST"
    echo "  torch is back to its shipped configuration (GPU will not be visible on WSL)"
    ;;
status)
    show_status
    ;;
*)
    echo "usage: $0 {apply|restore|status} [venv] [rocm_root]"; exit 2
    ;;
esac
