# Launch notes from the 2026-11-13 retro

For the host. Nothing here has been applied to the live launchers: `play_four.sh`, `play_round.sh` and the live copy of
`match_when_ready.sh` live outside this repo, next to the running games, and were only READ to write this (those three files and
nothing else: not `table_tools.py`, not `tunnel_watch.py`, nothing under `live/`). The diffs below are recommendations to apply by
hand between games. Each was checked with `bash -n` on a scratch copy and nothing more: they have not been run.

## 1. What went wrong

1. **The remote player's draft page died when the match started.** Both play scripts end their set-up with
   `python3 "$TT" tunnels-close --live "$L"`, which (by its own comment) closes every tunnel that has a pidfile in `live/tunnels/`.
   The draft server's tunnel has its pidfile in the same folder, so it was closed with the rest. The remote player's draft page is
   served through that tunnel: the page went dead, the "Join the match" button with it, and the seat link had to be passed by hand,
   twice.
2. **The seat link changed at every match start.** Each start closed the old quick tunnel and opened a new one, so the remote
   player got a new host every time, and the host pasted it into chat four times.
3. **A four-player table was started when the players wanted 1v1.** `match_when_ready.sh` has no default format (it refuses to run
   without `--four` or two pairings: checked, that part was already right), but it never said which format it was about to start, and
   then it waits, unattended, for the last "I'm ready".

## 2. The diffs

### play_four.sh

Close only this table's own tunnel, keep it when it is still up, and send the remote player the link (section 3).

```diff
--- a/play_four.sh
+++ b/play_four.sh
@@ -81,12 +81,23 @@
 
 # 6. S2: close the tunnels these scripts opened before (pidfiles only); open Sam's, to this table only; his link (key after '#')
 #    to a 0600 file and the clipboard, and tunnel_watch.py rewrites it if the tunnel's host changes mid-game
-python3 "$TT" tunnels-close --live "$L"
-TL="$L/tunnels/$PORT.log"
-: > "$TL"; chmod 600 "$TL"
-screen -dmS "trekgame-$PORT" zsh -c "echo \$\$ > '$L/tunnels/$PORT.pid'; exec cloudflared tunnel --no-autoupdate --url $U >> '$TL' 2>&1"
-for i in $(seq 1 30); do grep -q 'https://[a-z0-9-]*\.trycloudflare\.com' "$TL" && break; sleep 1; done
+#    2026-11-13: NOT `tunnels-close` (it closes every pidfile in tunnels/, the draft server's tunnel too, and the remote player's draft
+#    page with its "Join the match" button dies with it). Only THIS table's own tunnel is touched, and it is KEPT when it is still up:
+#    a tunnel points at the port, not at the process, so it survives a --force restart and Sam keeps the same host.
+TL="$L/tunnels/$PORT.log"; TP="$L/tunnels/$PORT.pid"
+tunnel_up() { [ -s "$TP" ] && ps -p "$(cat "$TP")" -o command= 2>/dev/null | grep -q cloudflared && grep -q 'https://[a-z0-9-]*\.trycloudflare\.com' "$TL" 2>/dev/null; }
+if tunnel_up; then
+  echo "keeping the tunnel to :$PORT (same host for Sam; only his key is new)"
+else
+  if [ -s "$TP" ]; then p=$(cat "$TP"); ps -p "$p" -o command= 2>/dev/null | grep -q cloudflared && kill "$p"; rm -f "$TP"; fi
+  : > "$TL"; chmod 600 "$TL"
+  screen -dmS "trekgame-$PORT" zsh -c "echo \$\$ > '$TP'; exec cloudflared tunnel --no-autoupdate --url $U >> '$TL' 2>&1"
+  for i in $(seq 1 30); do grep -q 'https://[a-z0-9-]*\.trycloudflare\.com' "$TL" && break; sleep 1; done
+fi
 python3 tunnel_watch.py --live "$L" --port "$PORT" --seat Sam --log "$TL" --once || echo "WARNING: no tunnel host for Sam yet: see $TL"
+# D2: send Sam the link (read from the 0600 file; never printed). A failure is a note, never a stop: the link is also on his draft page.
+python3 "$CODE/scripts/notify_link.py" --to Sam --link-file "$L/sam-link.txt" --what "your seat link for the four-player game" --log-file "$L/notify.log" \
+  || echo "NOTE: Sam was not sent the link automatically (notify_link exit $?): it is on his draft page (Join the match) and in the clipboard"
 if [ "${TUNNEL_WATCH:-1}" = 1 ]; then
   nohup python3 -u tunnel_watch.py --live "$L" --port "$PORT" --seat Sam --log "$TL" >> "$L/tunnels/watch.log" 2>&1 &
   echo $! > "$L/tunnels/watch.pid"
```

### play_round.sh

The same: only the two table ports' tunnels are touched; the remote player's is kept when it is up. In a rehearsal (`DRYRUN=1`) nothing
is sent.

```diff
--- a/play_round.sh
+++ b/play_round.sh
@@ -137,20 +137,33 @@
 done
 
 # S2: close every tunnel these scripts opened before (pidfiles only), then open the one the remote player needs
-python3 "$TT" tunnels-close --live "$L"
+# 2026-11-13: NOT `tunnels-close` (it closes every pidfile in tunnels/, the draft server's tunnel too, and the remote player's draft page
+# with its "Join the match" button dies with it). Only the tunnels to THESE two table ports are touched: the one to the table the remote
+# player is NOT at is closed; the one to his table is KEPT when it is still up (a tunnel points at the port, so it survives a --force
+# restart and he keeps the same host), and opened otherwise.
+tunnel_up() { [ -s "$L/tunnels/$1.pid" ] && ps -p "$(cat "$L/tunnels/$1.pid")" -o command= 2>/dev/null | grep -q cloudflared && grep -q 'https://[a-z0-9-]*\.trycloudflare\.com' "$L/tunnels/$1.log" 2>/dev/null; }
+tunnel_close() { f="$L/tunnels/$1.pid"; [ -s "$f" ] || return 0; p=$(cat "$f"); ps -p "$p" -o command= 2>/dev/null | grep -q cloudflared && kill "$p"; rm -f "$f"; }
+for port in "$P0" "$P1"; do [ "$port" = "$REMOTE_PORT" ] || tunnel_close "$port"; done
 if [ -n "$REMOTE_PORT" ]; then
   TL="$L/tunnels/$REMOTE_PORT.log"
-  : > "$TL"; chmod 600 "$TL"
   if [ "$DRY" = 1 ]; then
+    : > "$TL"; chmod 600 "$TL"
     echo "DRYRUN: would open a quick tunnel to :$REMOTE_PORT for $REMOTE"
     echo "INF |  https://dry-run-host.trycloudflare.com  |" >> "$TL"   # a stand-in host, so the link writer runs
+  elif tunnel_up "$REMOTE_PORT"; then
+    echo "keeping the tunnel to :$REMOTE_PORT (same host for $REMOTE; only the key is new)"
   else
+    tunnel_close "$REMOTE_PORT"
+    : > "$TL"; chmod 600 "$TL"
     # exec: the pid written is cloudflared's own, so the pidfile names exactly the process to close next round
     screen -dmS "trekgame-$REMOTE_PORT" zsh -c "echo \$\$ > '$L/tunnels/$REMOTE_PORT.pid'; exec cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$REMOTE_PORT >> '$TL' 2>&1"
   fi
   for i in $(seq 1 30); do grep -q 'https://[a-z0-9-]*\.trycloudflare\.com' "$TL" && break; sleep 1; done
   python3 tunnel_watch.py --live "$L" --port "$REMOTE_PORT" --seat "$REMOTE" --log "$TL" --once \
     || echo "WARNING: no tunnel host for $REMOTE yet: see $TL"
+  # D2: send the remote player the link (read from the 0600 file; never printed). A failure is a note, never a stop.
+  [ "$DRY" = 1 ] || python3 "$CODE/scripts/notify_link.py" --to "$REMOTE" --link-file "$L/$(low "$REMOTE")-link.txt" --what "your seat link for this round" --log-file "$L/notify.log" \
+    || echo "NOTE: $REMOTE was not sent the link automatically (notify_link exit $?): it is on the draft page (Join the match) and in the clipboard"
   if [ "$DRY" != 1 ] && [ "${TUNNEL_WATCH:-1}" = 1 ]; then
     nohup python3 -u tunnel_watch.py --live "$L" --port "$REMOTE_PORT" --seat "$REMOTE" --log "$TL" >> "$L/tunnels/watch.log" 2>&1 &
     echo $! > "$L/tunnels/watch.pid"
```

Three things to know about these two diffs:

* They stop calling `tunnels-close` altogether, so a tunnel opened for a port that neither script is starting now is left alone. That
  is the point (the draft's tunnel is such a tunnel), but it also means a stale tunnel to an old test port stays up until it is closed
  by hand. `python3 table_tools.py tunnels-close` still exists for that; just do not run it while a draft page is in use.
* `tunnel_up` trusts the pidfile only if that pid is a `cloudflared` process AND the tunnel's log already names a host. A tunnel
  that is up but whose log was truncated is treated as down and reopened (a new host, as today): never worse than today.
* A kept tunnel keeps its HOST. The seat KEY is still new after a restart (a new table issues new keys), so the link file is
  rewritten and the link still has to reach the player: that is what "Join the match" and section 3 are for.

A smaller alternative, if you would rather keep `tunnels-close`: give the draft's tunnel a pidfile OUTSIDE `live/tunnels/` (for example
`live/draft-tunnel.pid`), so the sweep cannot see it. I could not check how the draft tunnel is started (that script was not one I was
allowed to read), so I have not written that diff.

### match_when_ready.sh

It now says the format at the top and again when it starts the tables. This one IS applied in the repo copy (`draft/match_when_ready.sh`,
which was byte-identical to the live copy when this was written) and tested in rehearsal mode (`draft/test_match_format.py`); the live
copy needs the same change:

```diff
--- a/draft/match_when_ready.sh
+++ b/draft/match_when_ready.sh
@@ -100,6 +100,13 @@ START=$(date +%s)
 if [ "$FOUR" = 1 ]; then TABLES=""; for s in $SEATLIST; do TABLES="$TABLES $s=$P0"; done; else TABLES="${1%%:*}=$P0 ${1##*:}=$P0 ${2%%:*}=$P1 ${2##*:}=$P1"; fi
 export TABLES
 
+# 2026-11-13 retro: the host started ONE four-player table when the players had agreed on 1v1. This script has no default format (it wants
+# --four or two pairings), but it also never SAID which it was about to start, and it then waits, unattended, for the last "I'm ready".
+# Say it at the top (while ^C costs nothing) and again in the line that starts the tables.
+if [ "$FOUR" = 1 ]; then PLAN="ONE four-player table (free-for-all): $(echo $SEATLIST | sed 's/ /, /g') on :$P0"
+else PLAN="TWO 1v1 tables: ${1%%:*} vs ${1##*:} on :$P0, and ${2%%:*} vs ${2##*:} on :$P1"; fi
+[ "$PUBONLY" = 1 ] || echo "FORMAT: $PLAN. They start as soon as everyone is ready (^C now if that is not what was agreed)."
+
 if [ "$PUBONLY" = 0 ]; then
   # 1. withdraw an old match link, then wait for the humans (and ready the agents with their own tokens)
   python3 - <<'E' || exit $?
@@ -196,6 +203,7 @@ E
   fi
 
   # 3. the table(s)
+  echo "STARTING: $PLAN"
   if [ "$FOUR" = 1 ]; then
     "$PLAY_FOUR" ${FORCE:+$FORCE} --port "$P0"
   else
```

What it prints:

```
FORMAT: TWO 1v1 tables: Michael vs Sam on :8800, and Claude vs Fusion on :8801. They start as soon as everyone is ready (^C now if that is not what was agreed).
...
STARTING: TWO 1v1 tables: Michael vs Sam on :8800, and Claude vs Fusion on :8801
```

It does not ask "are you sure?": the script is left running while people build decks, and a prompt nobody is there to answer would
hold the match. If you want a hard stop instead, add `--yes` and refuse to start without it; say so and I will write it.

## 3. Sending the remote player the link: `scripts/notify_link.py`

New in this repo, not called by anything yet. The one line to add to each launcher, right after the link file is written
(`tunnel_watch.py ... --once` writes `live/<seat>-link.txt`); it is already in the two diffs above:

```bash
# play_four.sh
python3 "$CODE/scripts/notify_link.py" --to Sam --link-file "$L/sam-link.txt" --what "your seat link for the four-player game" --log-file "$L/notify.log" \
  || echo "NOTE: Sam was not sent the link automatically (notify_link exit $?): it is on his draft page (Join the match) and in the clipboard"

# play_round.sh
[ "$DRY" = 1 ] || python3 "$CODE/scripts/notify_link.py" --to "$REMOTE" --link-file "$L/$(low "$REMOTE")-link.txt" --what "your seat link for this round" --log-file "$L/notify.log" \
  || echo "NOTE: $REMOTE was not sent the link automatically (notify_link exit $?): it is on the draft page (Join the match) and in the clipboard"
```

(`$CODE` is the engine checkout the launchers already use; the script has to be in that checkout for the line to work.)

Set-up, once:

```bash
mkdir -p ~/.config/divinci-table && chmod 700 ~/.config/divinci-table
$EDITOR ~/.config/divinci-table/contacts.json      # {"Sam": {"imessage": "+1..."}}
chmod 600 ~/.config/divinci-table/contacts.json     # it refuses to run if anyone else can read it
python3 scripts/notify_link.py --to Sam --link-file <a 0600 file with a link> --what "a test" --dry-run
```

* The link is read from the file (which must be 0600), never from the command line; the script prints and logs only
  `sent to Sam via imessage (link host <host>)`.
* The first usable channel listed for the player is used.
  * `imessage`: needs the Messages app signed in on the host laptop, and macOS will ask ONCE to let the terminal (or whatever runs
    the launcher) control Messages; until that is allowed the send fails with exit 3. Do the dry run and one real test send before a
    stream, not during it.
  * `slack_webhook_file`: a 0600 file holding one incoming-webhook URL. A webhook posts to ONE channel and everybody in it can
    read the message. The link carries the seat key, so the channel must be private and hold only that player (or be a webhook made
    for a DM-style channel). Never a shared channel. The webhook URL is itself a secret.
  * `email`: a stub that says "not configured". There are no SMTP credentials on this laptop, and none were added.
* Exit codes: 0 sent, 1 usage or a file it refuses, 2 no contact or no usable channel, 3 the send failed. The launcher lines above
  turn any failure into a note and carry on: a link that could not be sent must never stop a match from starting.
* Not covered: `tunnel_watch.py` rewrites the link file when a quick tunnel's host changes MID-game. It should call the same line
  when it does (I could not read that script, so there is no diff for it). `match_when_ready.sh --follow` already re-publishes a
  changed link to the draft page.
* On a stream: the link is never on screen from this script, but the Messages app and Slack show it when they deliver it. Keep those
  windows out of the captured area.

## 4. The better fix: one address that does not change

The notifier treats the symptom. The cause is that the remote player's address changes. Two ways to make it stop, cheapest first:

**A. Keep the draft tunnel alive and use "Join the match" as the only door.** The draft page already shows each player their own link
(`GET /api/match` with their own draft token; `match_when_ready.sh` publishes it, `--follow` re-publishes when it changes). If the draft
tunnel stays up for the whole evening, the remote player keeps ONE tab open all night and presses one button per match; nobody sends
anything. It needs:
  * the play-script diffs above (so a match start no longer closes the draft's tunnel);
  * the draft server left running between matches, and `match_when_ready.sh` run with `--follow`;
  * nothing new to install. Weak point: the draft's own address is still a quick tunnel, so it changes if the draft server's tunnel
    is restarted (a laptop sleep, a network change). That one link then has to be sent once (the notifier can do it).

**B. A named Cloudflare tunnel with a fixed hostname** (for example `table.<your-domain>` and `draft.<your-domain>`). The address never
changes, across restarts and across evenings; the seat link differs only in its key. It needs:
  * a domain on a Cloudflare account, `cloudflared tunnel login` once on the host laptop, `cloudflared tunnel create`, and DNS routes
    for the hostnames (one-time, about ten minutes);
  * the table started with `TABLE_HOSTS` naming that hostname instead of `.trycloudflare.com`, and the draft server's link allowlist
    extended the same way (`DRAFT_LINK_HOST_SUFFIXES`: it only accepts loopback and known tunnel suffixes for a published link);
  * the launchers running `cloudflared tunnel run <name>` instead of `cloudflared tunnel --url ...`, and `tunnel_watch.py` no longer
    needed for that seat;
  * a decision about exposure: a fixed public hostname is findable in a way a random quick-tunnel name is not. The table already
    requires a seat key for every remote call and serves card art only to a valid key; keep it that way, and consider Cloudflare
    Access in front of the hostname if the stream makes the name public.

A does not need B, and B makes A sturdier. For the next stream: apply the play-script diffs, run `--follow`, set up the notifier as
the fallback, and rehearse one `--force` restart with the remote player's tab open before going live.
