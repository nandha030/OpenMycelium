#!/usr/bin/env sh
# Measure the image pull's actual rate from the Docker VM's own counters,
# without touching the pull itself.
before=$(grep eth0 /proc/net/dev | tr -s ' ' | cut -d' ' -f3)
sleep 40
after=$(grep eth0 /proc/net/dev | tr -s ' ' | cut -d' ' -f3)
delta=$((after - before))
echo "  received so far : $((after / 1048576)) MiB"
echo "  over 40 s       : $((delta / 1024)) KiB"
echo "  rate            : $((delta / 40 / 1024)) KiB/s"
