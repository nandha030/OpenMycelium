echo "== pip cache (grows if pip is pulling whole wheels just to read metadata) =="
du -sh /root/.cache/pip 2>/dev/null || echo "   none yet"
echo "== pip processes =="
pgrep -fa "pip install" | cut -c1-120 | sed 's/^/   /' || echo "   none"
echo "== interface counters =="
awk '/eth0/ {printf "   eth0 rx %.2f GiB  tx %.3f GiB\n", $2/1073741824, $10/1073741824}' /proc/net/dev
