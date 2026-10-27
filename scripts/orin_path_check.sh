#!/usr/bin/env bash
# Pre-launch gate for an Orin run: is the path from this Mac to the Orin steady enough for a many-hour ssh tunnel?
#   scripts/orin_path_check.sh [HOST] [COUNT] [MAX_AVG_MS] [MAX_STDDEV_MS]     exit 0 = go, 1 = hold (nothing is opened or changed)
# Two round-1/round-2 tunnels died on ~65-100 ms pings with heavy jitter; the same path read ~7 ms when it held.
set -u
host="${1:-192.168.2.89}"; n="${2:-20}"; max_avg="${3:-15}"; max_sd="${4:-15}"
out=$(ping -c "$n" -i 0.3 -t $((n + 10)) "$host" 2>&1); rc=$?
loss=$(printf '%s' "$out" | sed -n 's/.* \([0-9.]*\)% packet loss.*/\1/p')
stats=$(printf '%s' "$out" | sed -n 's|.* = \([0-9.]*\)/\([0-9.]*\)/\([0-9.]*\)/\([0-9.]*\) ms|\2 \4|p')
avg=${stats% *}; sd=${stats#* }
if [ -z "$loss" ] || [ -z "$avg" ]; then echo "HOLD: no ping statistics for $host (rc=$rc)"; exit 1; fi
echo "path to $host: avg ${avg} ms, stddev ${sd} ms, loss ${loss}% over $n pings (limits: avg<$max_avg, stddev<$max_sd, loss 0)"
awk -v a="$avg" -v s="$sd" -v l="$loss" -v ma="$max_avg" -v ms="$max_sd" 'BEGIN{exit !(l+0==0 && a+0<ma+0 && s+0<ms+0)}' && { echo "GO"; exit 0; }
echo "HOLD"; exit 1
