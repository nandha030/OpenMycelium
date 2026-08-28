set -u
cd /mnt/c/Users/User/Documents/Open_Mycelium
export PYTHONPATH=runtime/serving:runtime/scheduler:runtime/fabric:runtime/cli:runtime/mccl/src
PY=/opt/hetenv/bin/python
REPO=HuggingFaceTB/SmolLM2-135M-Instruct
NAME=SmolLM2-135M-Instruct
rm -rf /opt/models/$NAME* 2>/dev/null || true
echo "=== 1. pull ==="
$PY runtime/cli/models.py pull $REPO
echo "=== 2. verify ==="
$PY runtime/cli/models.py verify $NAME 2>&1 | tail -6
echo "=== 3. what landed on disk ==="
find /opt/models/$NAME -type f | sed 's|.*/||' | sort | tr '\n' ' '; echo
echo "=== 4. no code or pickle fetched ==="
find /opt/models/$NAME -name '*.py' -o -name '*.bin' -o -name '*.pt' | wc -l
echo "=== 5. resume after truncation ==="
SH=/opt/models/$NAME/model.safetensors
BEFORE=$(stat -c%s $SH)
truncate -s $((BEFORE/2)) $SH
echo "  truncated to $(stat -c%s $SH) of $BEFORE"
$PY runtime/cli/models.py verify $NAME 2>&1 | grep -E 'TRUNCATED|FAIL|PASS' | head -3
$PY -c "
import sys; sys.path.insert(0,'runtime/cli')
from puller import download
got = download('$REPO','main','model.safetensors','$SH',$BEFORE)
print('  resumed to', got, 'of', $BEFORE)
"
$PY runtime/cli/models.py verify $NAME 2>&1 | grep -E 'PASS|FAIL' | head -2
echo "=== 6. idempotent re-pull ==="
$PY runtime/cli/models.py pull $REPO 2>&1 | tail -2
echo "=== 7. interrupted pull leaves no usable model ==="
ls -d /opt/models/*.partial 2>/dev/null | wc -l
$PY runtime/cli/models.py list
