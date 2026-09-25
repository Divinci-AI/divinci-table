# Jev / djev at a four-player Commander table

**Question:** can a System One model (TypeSafe `jev-latest`, and later the self-hosted
DiffusionGemma-Jev "djev") play a four-player Commander game, one typed judgment at a time?

**Shape:** code owns every rule; the model only *selects*. At each decision the engine lists
the legal options (≤26, the djev-run label limit) and sends one request whose questions are
Choices: the main-phase action, a spell's target, one question per attacker (which opponent,
or hold back), one per blocker. Attack and block requests also carry a speculative Score per
opponent, "how much of a threat is X to me winning", which drives nothing and exists so a
human can read the model's table sense in the replay.

## Files

| file | what |
|---|---|
| `cards.py` | card pool (real names, simplified text) + 40-card mono-colour decks |
| `engine.py` | the rules; `Game.view()` is exactly what a model sees |
| `agents.py` | `random`, `heuristic`, and `JevAgent` for `jev` (TypeSafe) or `djev` (djev-run URL) |
| `run.py` | play N games with any seat line-up |
| `paired.py` | the fair comparison (below) |
| `build_viewer.py` + `viewer-template.html` | self-contained replay page from game logs |
| `deploy-djev.sh` | private Cloud Run GPU deploy of djev — **not run, blocked on quota** |
| `games/` | full game logs — NOT in git; in R2 `divinci-experiments/commander/`, fetch with `../scripts/fetch-games.sh` |
| `results/` | per-run summaries (`*.out`) and per-pair rows (`*.jsonl`) — small, committed |

## Run

```bash
python3 run.py --seats heuristic,random,heuristic,random --games 40 --rotate --no-save   # offline
TYPESAFE_API_KEY=… python3 paired.py --agent jev --seeds 8
DJEV_URL=https://… DJEV_AUTH=gcloud python3 paired.py --agent djev --seeds 8          # once deployed
python3 build_viewer.py games/paired-jev-s1000-seat3.json … -o viewer.html
```

Both Jev backends take the identical body. The subset that works on both: `questions` as a dict,
choice `criteria` as `{key: description}`, score `criteria` as a list, `instructions` as a
plain STRING (djev-run calls `.strip()` on it), ≤26 options. `JEV_MAX_CALLS` (default 1500)
is our own spend cap; TypeSafe keys have none. Any unusable answer falls back to the
heuristic for that question and is COUNTED (`fallbacks=`), never hidden.

## Findings

### 1. The first table was a coin with one side: black won 88%

Before any agent comparison, 400 heuristic mirror games gave black (Sheoldred) 88% of wins,
and 70% even with four RANDOM players — a deck effect, not a seat or policy one (black won
75–89% from every seat position). No single card explained it (removing any one left black
at 67–78%); stripping black to vanilla creatures dropped it to 1%, i.e. several effects
compound. A 32-way sweep picked the fix: Sheoldred's drain cut to −1 per opponent draw with no
self-lifegain (the +2 per own draw alone moved black ~25%→43%), Gray Merchant and Damnation out.
Green was then the loser (~6%) because it had nothing that blocks fliers and every table's
fliers dogpiled it out by round ~7; Thornweald Archer and Silklash Spider (reach) fix that.

Now (400 games each): heuristic tables U/B/R/G = 17/34/40/10 %; random tables 2/5/25/68 %.
**Balance depends on who is playing** — a format fair for one policy is unfair for another —
so the comparison below does not rely on balance at all.

### 2. Why the comparison is paired

`paired.py` plays each (seed, seat) twice: once with the model in that seat, once with the
heuristic, same shuffles and the same three heuristic opponents. Deck and seat strength
cancel; only the decision-maker differs.

**Result, `jev-latest` vs heuristic (2026-09-24, 8 seeds × 4 seats = 32 paired seat-games):**

| | jev-latest | heuristic |
|---|---|---|
| wins | 6/32 | 8/32 |
| won where the other lost (discordant pairs) | 4 | 6 |
| survived longer in the pair | 13 | 7 (12 tied) |
| by deck: U / B / R / G wins | 1 / 0 / **5** / 0 | 1 / **3** / 2 / 2 |

**No measurable difference in winning.** 4 vs 6 discordant pairs is a coin flip (two-sided
sign test p≈0.75); 13 vs 7 on survival leans Jev's way but p≈0.26 — suggestive, not shown.
The deck split is the interesting part and is also underpowered: Jev did best with the
aggressive red deck (5/8 vs 2/8) and worst with black (0/8 vs 3/8), consistent with a player
that attacks readily and blocks badly — the same pattern as the first hand-read game, where it
chump-blocked a 4/5 deathtouch with its own commander.

1,030 calls, **0 fallbacks** (every answer was a legal option), p50 352 ms / p95 470 ms
laptop→api.typesafe.ai, 1.36 M input tokens (~1.3 k/call, ≈$0.06 total at $0.042/M).
Resolving a real edge needs ~10× the games; the paired design makes that cheap to run.

### 3. djev is blocked on GPU quota, not on code

The Colab route failed on 2026-09-23 (G4/H100 refused on this tier; see
an internal notebook). djev-run's Cloud Run route needs an RTX PRO 6000 (Blackwell,
for the NVFP4 weights). Quota on our GCP project, read 2026-09-24:

| quota | us-central1 |
|---|---|
| `nvidia_rtx_pro_6000_gpu_allocation_no_zonal_redundancy` | **0** (0 in every region) |
| `nvidia_l4_gpu_allocation_no_zonal_redundancy` | 3 — no use: Ada cannot run NVFP4 |

Everything else is ready: the game talks to djev by setting `DJEV_URL`. `deploy-djev.sh`
deviates from upstream on purpose — private (`--no-allow-unauthenticated`; upstream's server
has no auth and `allowed-origins *`), image pinned by digest, read-only weights, labels,
max 1 instance, $0 idle (~$3/hr while warm).

## Not modelled

The stack and instant speed (all spells are sorceries), mulligans, activated abilities beyond
mana, damage-assignment order, politics/deals, the 100-card singleton rule.

### 4. open-alternative-jev (`so1`) + Qwen3.5-4B on this Mac: far worse than Jev

`so1` (github.com/ikermoel/open-alternative-jev, pinned in `so1.pin`) reads typed answers out of
any ChatML model in one forward pass. `so1_server.py` wraps it as `/v1/systemone` so the harness
is unchanged (`--seats so1,…`, `SO1_URL`). Qwen3.5-4B, bf16, MPS, M4 Pro — 2026-09-24.

| same 23 (seed, seat) pairs | jev-latest | so1 + Qwen3.5-4B |
|---|---|---|
| wins | 5/23 | 0/23 |
| survived longer than the heuristic in that seat | 9 (shorter 4) | 2 (shorter 18) |
| head-to-head, who lasted longer | **20** | 1 (2 tied) |
| p50 / p95 per decision | 352 / 470 ms (internet) | 2.3 / 4.2 s (local) |

**Why:** it treats playing a land as optional. 187 of its 247 main-phase decisions were a pass, and
in 165 of those the only alternative was "play a land" (it passed at 0.53–0.68; Jev plays the land
at 0.99). No mana → no board → no defence. Not a position bias: reordering the options leaves its
choice unchanged. It also almost never blocks (3 blocks vs Jev's 116 over comparable games).
Takeaway for the table: rules everyone agrees on ("play a land if you can") belong in code, for
every seat, so the model is judged on judgment. Their benchmark put this model at 59% vs Jev's
73% on typed-decisions; here the gap is much larger. The 27B (their headline model) is untested
and only fits the Jetson.

**Run notes, so nobody re-learns them:**
- 9 of the 32 planned pairs did not run: the MPS server grew until system free memory hit 8%
  and swap 14.8/15.4 GB, and the watchdog (`run-so1-guarded.sh`, floor 12–15 %) killed it. The
  harness would then have silently fallen back to the heuristic; every game file records its
  fallbacks, and all 23 counted games have 0. Always check `fallbacks=` before trusting a run.
- Loading Qwen3.5 through so1's default path SEGFAULTED twice (so1 tries `AutoModelForCausalLM`
  first); passing `model_class=AutoModelForImageTextToText` loaded it once and crashed once more
  under the wrapper — intermittent, cause not found.

### 5. Rerun with a land rule for every seat (`paired.py --auto-land`): the gap is judgment

`Game(auto_land=True)` plays a land at the start of each main phase for EVERY seat when one is in
hand. Heuristic-only win rates are identical with and without it (it always led with a land), and
jev-latest's result did not move (6/32 both times), so the rule is neutral for anyone who already
played lands. Same 32 pairs, 0 fallbacks on both sides, 2026-09-24:

| land rule on | jev-latest | so1 + Qwen3.5-4B |
|---|---|---|
| wins | 6/32 | 2/32 |
| survived longer than the heuristic in that seat | 11 (shorter 8) | 7 (shorter 21) |
| head-to-head, who lasted longer | **26** | 3 (3 tied) |
| passed while a castable spell was on offer | 28 % | **66 %** |
| p50 / p95 per decision | 364 / 499 ms | 2.9 / 6–8 s |

The land fix removed one symptom, not the cause: the 4B model is generally passive (passes at
p≈0.57 — unsure, not confidently wrong). Jev's edge here is real game judgment, and the land rule
stays on for all future comparisons.

Server memory: after adding `torch.mps.empty_cache()` per request, the Metal driver held a flat
9.1 GB over 525 requests (it had grown unbounded before). The Mac was still at ~18 % free with it
loaded, because this laptop's baseline is ~14 GB swap + ~11 GB compressed — so a 9 GB model is
feasible but leaves no headroom. Loading: through `run-so1-guarded.sh`, Qwen3.5 segfaulted 3/3
without `python -X faulthandler` and loaded 2/2 with it; cause unknown, the flag stays.

## Next (not started — Colab compute deliberately held back, 2026-09-24)

1. **so1 + Qwen3.6-27B at the table, on a Colab A100.** Already proven to load there (8-bit, 40 GB)
   and to match jev-latest on the flagger/collateral replay (an internal notebook, "Results").
   Needs one Colab runner (modelled on our internal so1 Colab runner) that runs
   `so1_server.py` on CUDA plus `paired.py --agent so1 --auto-land --seeds 8` ON the VM — no
   tunnel, no TypeSafe key on the VM. Est. 1–1.5 A100-hours. Bar to beat: jev-latest 6/32 wins,
   11 longer / 8 shorter survival (section 5).
2. **djev INT4 on a Colab A100.** `cyankiwi/diffusiongemma-26B-A4B-it-AWQ-INT4` (17.2 GB,
   compressed-tensors W4A16 → Marlin, sm_80+) sidesteps the G4/H100 refusal that blocked djev.
   Untested: whether the pinned vLLM diffusion commit loads that checkpoint.
3. **Jetson AGX Orin 64 GB** when it arrives: the same two, via the same one-command harness.
4. The table itself: PWA + table server (see the conversation plan: scan pad, HTTPS/tunnel,
   server-side OCR against the decklist).
