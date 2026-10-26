#!/usr/bin/env bash
# The dogfood loop on the real table: one game at a time, the oracle on each, a report at the end.
#
#   scripts/table_loop.sh --out DIR [--backend fake|orin] [--hours 8] [--games N] [--seats 2] [--turns-per-seat 10]
#   touch DIR/STOP        stop cleanly (the running game is stopped within seconds; no stray server or port is left)
#
# fake  (default) a random-legal pilot on loopback: a table fuzzer, no Orin, no network.
# orin            gpt-oss-20b through an ALREADY OPEN ssh -L tunnel; needs --orin-clear and JETSON_BASE. Message the `orin`
#                 session first and wait for "clear"; this script never opens a tunnel.
# Guards: STOP file, hours, games, disk floor (12 GiB), memory free %, swap-out rate, per-game timeout, 3 failed games in a row.
# `caffeinate -i` keeps the Mac awake for the loop's lifetime only.
set -u
cd "$(dirname "$0")/.."
exec caffeinate -i python3 harness/table_loop.py "$@"
