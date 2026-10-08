# Playtest 2: from the first in-person test to a smooth second one, as a goal

*Run with `/goal docs/PLAYTEST-2-GOAL.md` (from the divinci-table repo). Written 2026-10-07, the night of the first in-person
test of the deployed table (Michael and Sam in a room, Claude as a pilot seat over the API). Tick a box only when its check has
been run and its output read.*

## What happened (evidence, not impressions)

Room `39y7if6d`, Commander, three seats (Claude on a virtual Kaust deck, Michael and Sam with physical decks), 22:00 to 23:15 PT.

| When | What | Cost |
|---|---|---|
| 22:03-22:06 | Michael taps his seat in Brave on an iPhone: "nothing happens". The log shows **seven successful claims** (#3-#9). The page never remembered the seat, so the picker kept coming back | ~3 min, a refresh fixed it |
| 22:06-22:22 | Claude's first turn **stalled at upkeep for ~16 minutes**: each step of an AI's turn needs both people to pass priority, and the join link lands on `/stage`, which has no pass control on a phone. They found `/me` by luck | ~16 min |
| 22:33-22:51 | Claude's second turn **stalled 18 minutes at upkeep** waiting for one person to pass | ~18 min |
| all evening | Sam's photos (22:30, 22:31, 22:59) arrived **black, all exactly 31,027 bytes**. Michael's (22:55) was fine, 834 KB | every photo from Sam useless |
| 22:56 | "I play a planes and cast mindless automation" recorded only Mindless Automaton; "played an island" was classed as chatter, "play a swamp" worked | board state drifts from the table |
| 22:33, 22:52 | The table spoke filler lines for Claude ("Hold on.", "Hmm, let me think.") while it was waiting on **people**, which read as "Claude is doing something" | confusion |
| all evening | No spoken output at all in the cloud (the voices on the laptop were macOS `say`) | the end-of-turn summary was never heard |
| 23:09 | **A deploy restarted the live room's server.** The saved game came back (turn 3, same phase), but the visible log history before 23:11 and every photo are gone: the snapshot stores the event counter, not the events, and photos live on the container's disk | log and photos lost mid-game |
| 23:09 | I had said "the old room keeps its old version until it ends". **That was wrong**; the rollout replaces running containers | an incorrect claim, now corrected |
| earlier | Python's default User-Agent gets a Cloudflare 403 (error 1010) from the cloud site; the deploy gate refused for hours on disk space and other agents' builds | tooling |

Request-volume check on my own changes (found while reviewing, not in the game): the overlay polled `/api/phase` every second and the voice loop `/api/events` every 1.2 s **per device on top of the page's own polling**, which is roughly 9 requests a second per three-person room and could exhaust a Workers free plan in a day.

## Rules for every milestone

1. **Every friction point gets a failing test first, then the fix.** The test names the evidence above.
2. **Three layers:** a unit/server test, a real-browser end-to-end test, and for anything touching identity, uploads or the network a security test. A fix with only one layer is not done.
3. **No deploy while a game is running.** A rollout restarts live rooms (see 23:09). Deploys wait for the table to say it is idle, or the room is told.
4. **Humans own their own life totals and hit points.** No automation, timer or helper ever changes them or answers for a person except by passing priority, and every such pass is in the log.
5. **Defaults that save a person time must be visible and one tap to undo.** If the table passes for you, the page says so.
6. **Public repo:** no secrets, no personal details, no room ids of other people's games.
7. Nothing deployed without Michael's explicit instruction; commits locally first.

---

# P0: fix before tomorrow's game

## F1: nobody could pass priority on a phone, and every step needed a pass
- [x] Timer: `--human-pass-secs` (cloud rooms 30 s) passes for a person who does not pass in time, logged as a timeout; the active player is never timed out; a hold pauses it. *Done and tested (`table/tests/pass_timer_test.py`, two real servers).*
- [x] Urgency overlay: red glow, countdown, beeps, Pass button on any page, tabs along the top. *Done and tested in a real browser (`table/tests/pass_ui_e2e.cjs`, 24 checks).*
- [ ] **Quiet defaults:** in a room with an AI or pilot seat, people **auto-pass** the non-combat steps of the AI's turn unless they opt in to stopping at every step (`--autopass-default others-no-combat`, set by the cloud entrypoint; the phone's auto-pass button shows the real state and one tap turns stopping back on). An AI's turn becomes ~4 passes per person instead of ~11.
- [ ] **Phones land on `/me`** (the stage page redirects a phone-sized screen to `/me` unless `?stay=1`), so the first screen has the pass button.
- [ ] **Check (server):** a pilot's `begin` completes with no human input under the default; combat steps still wait and then time out; an explicit "stop at every step" is honoured. **Check (browser):** on a 390 px screen the join link ends on `/me`; the auto-pass button reads "on" and one tap flips it.

## F2: seats: claim, remember, recover, and share a seat across devices
- [ ] **No reload loop when the browser will not keep storage:** claiming works in the current tab, the seat is kept for the tab (sessionStorage fallback), and the page says plainly when the browser is not remembering it (private mode, Shields).
- [ ] **Seat code:** a claimed seat shows a short code (6 characters, no look-alikes). Typing it on another device (or in another browser) adds that device. Codes are stored hashed, rate-limited, and die when the seat is released.
- [ ] **Key sprawl:** a device that re-claims reuses its key; at most 8 keys per seat; the log shows one "claimed" per device, not per tap.
- [ ] **Check (server):** code accepted once per device, wrong codes locked out after 5 a minute, keys capped. **Check (browser):** localStorage disabled (Brave-style): claim succeeds, no loop, the warning shows; two browser contexts share a seat by code. **Security:** a seat cannot be taken by a stranger with only the room link once claimed; the code never appears in the public event log; guessing is rate-limited.

## F3: the log and the photos must survive a restart
- [ ] The snapshot carries the event log (last 1,500 events) and restores it with continuing ids, so a restart or a sleep does not blank the log.
- [ ] Photos survive too: stored in R2 under the room by the Worker (not on the container's disk), served from there. *(Larger: see P1; until then the plan is to put them in the snapshot object.)*
- [ ] **Check (server):** start, chat, snapshot, restart with `--restore`: `/api/events?since=0` returns the earlier events and ids continue. **Check (e2e):** a deploy-style restart mid-game leaves the log and the phase intact.

## F4: photos: black, private, and read
- [x] Black photos fixed at the source (the page was shrinking them through a canvas that Brave blanks); a photo that fits is sent untouched; a blank shrink is refused. *Tested in a real browser (`table/tests/photo_upload_e2e.cjs`).*
- [ ] **Metadata stripped server-side** (a regression from the fix above: untouched camera files carry GPS, device and time data into a public room). Orientation and colour profile kept. *In progress (`table/tests/photo_privacy_test.py`).*
- [ ] **Photos become log lines:** a vision model (Workers AI on the Worker, or the pilot) writes a clearly labelled "from the photo" line of public board information, never touching a life total; a black or unreadable photo says so in the log.
- [ ] **Check:** EXIF GPS/Make/Model absent from stored bytes; a black photo yields "couldn't read this photo" in the log; the photo line lists cards and never a life change.

## F5: do not make the game spam the network
- [ ] One shared poller per page (the overlay, nav bar and voice read one state); visible 2 s, hidden 6 s; `/api/events` long-polls (`?wait=20`) on the Commander server as it already does for D&D.
- [ ] **Check (e2e):** an idle three-device room makes fewer than 40 requests a minute per device from the new scripts; hidden tabs fewer. **Check (server):** `/api/events?wait=` holds until an event or the cap.

## F6: stop the table talking for the AI while it waits on people
- [ ] No filler lines ("Hold on.") for a pilot seat or while the table is waiting on a person's pass.
- [ ] **Check (server):** a pilot seat gets no filler; an AI seat still does when it is the one that is slow.

## F7: the test baseline is red
- [ ] `phase_test.py` fails 14 checks and the sensory regression scores ~34/117 on the committed code (verified on a clean worktree, with and without Ollama). Find the root cause (a default changed from the older behaviour, or a real regression), fix the stale tests or the server, and make `run_all.sh quick` green or explain every red.
- [ ] **Check:** the suites' before and after output in the log below.

## F8: deploys must not restart a live game by surprise
- [ ] `scripts/cloud_deploy.sh` reads the public lobby and refuses while a room is listed, unless `--allow-live-rooms` (rooms are listed for six hours from creation).
- [ ] After a rollout it waits for the container to be ready and runs the smoke check against a fresh room.
- [ ] **Check:** the lobby parser is unit tested against a real lobby page; the script exits non-zero with a room listed and zero with none or with the flag.

---

# Security review (each with a test that fails if it regresses)

| # | Risk found or reasoned | Control | Test |
|---|---|---|---|
| S1 | Photo metadata (GPS, device, time) published to a public room | Strip server-side; keep orientation and ICC | `photo_privacy_test.py` (in progress) |
| S2 | A stranger with the room link claims an open seat first | Seat codes; per-seat claim once; release needs the key or code | F2 server + browser tests |
| S3 | Claim spam mints a new key per tap; unbounded key growth | Reuse the device's key; cap 8; rate limit | F2 server test |
| S4 | Seat codes guessed | 6 symbols from 32, hashed, 5 wrong a minute then a lockout, per seat and per address | F2 server test |
| S5 | Polling storms, cost and availability | F5 budget; long poll; per-address rate limits already on the lobby | F5 e2e + server tests |
| S6 | Stored XSS through chat text, captions, names into the log, nav bar or overlay | Escape at render; names validated; add a browser test that posts `<img src=x onerror=...>` and `"><script>` and asserts nothing runs on `/log`, `/me`, `/stage`, `/board` | new `xss_e2e.cjs` |
| S7 | No Content-Security-Policy on the table pages | Report-Only first, then enforce; inline scripts need a nonce plan | header test (report-only present) |
| S8 | The room cookie is readable by page scripts | `HttpOnly` (pages do not read it); `Secure`, `SameSite=Lax` stay | policy test on the Worker |
| S9 | Anyone can toggle hold or step timeouts for everyone | Hold auto-releases after 3 minutes and is logged with who | server test |
| S10 | The pilot's seat key on a shared machine | File mode 0600, never printed (already tested: `pilot_ctl_test.py`); rotate by releasing and re-claiming | exists |
| S11 | Debug hooks (`window.__urgent`, `window.__tablebar`) expose page state | They hold no secrets; keep behind a `?debug=1` check | e2e |
| S12 | Photos in a public room are fetchable by guessable names (time plus 32 bits) | Names from 128 random bits; `Cache-Control: private, no-store` | server test |

---

# P1: next, after the game is smooth

- [ ] Photos in R2 and read by Workers AI (F4/F3 together), with a cost cap per room.
- [ ] Push notifications to a closed phone browser (service worker, Web Push, a VAPID key as a Worker secret: needs Michael's decision on storing the key).
- [ ] Voice quality: Divinci or Workers AI text-to-speech for AI seats instead of the browser's voices; one voice per seat.
- [ ] Spoken-line grammar: "played an island" and "I play a planes" (plains) are plays; common mishearings of basic lands and the deck's own cards map to the card.
- [ ] The research hooks (journals, identity, game-over) reachable by a pilot in a cloud room (under `/api/brain/`).
- [ ] An operator runbook for a game night (below) and a **dress-rehearsal test** that plays a scripted three-seat game against the deployed site before anyone sits down.

## A game night, as a checklist (to run, not to hope)

1. Before: `table/tests/game_night_rehearsal.cjs` against the live site (creates and abandons one room); read its summary.
2. Create the room; send each person their link; each person taps their seat and the **🔊** and **🔔** buttons once.
3. During: a pilot's helper passes priority only when it holds no instant; the table's own timer covers the rest.
4. Never deploy during play. If you must, pause first and expect the log of the paused room to be restored from its snapshot.
5. After: bundle the game (`table/bundle.py`), run the surveys, and write down what was slow.

## Log

*(Add dated notes as boxes close.)*
