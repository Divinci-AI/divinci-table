# Fusion player: a Divinci release that picks moves

> IDs below are placeholders. The table reads the real release ID from `FUSION_RELEASE_ID` or the
> gitignored `table/.cache/fusion.json`; the API key comes from your secret manager.

Fusion is a Divinci release (a whitelabel assistant) that chooses one move per decision for the
Ellivere deck. The table server sends it the game state and a numbered list of legal options. It
replies with the number of its choice and one line to say at the table.

**For now it uses retrieval only (RAG).** Nothing has been fine-tuned yet; the fine-tune would not
have finished by 2026-10-01. The release's description says this.

## Where it lives

| | |
|---|---|
| Environment | **production** (`https://api.divinci.app`) |
| Workspace (whitelabel) | `<WORKSPACE_ID>` (a Divinci workspace) |
| Release | `<RELEASE_ID>`, "Fusion — MTG Commander player (Ellivere)", slug `fusion-mtg-commander-ellivere`, status **draft** |
| Model | `gemini-3-5-flash`, falling back to `@cf/google/gemma-4-26b-a4b-it` and then `@cf/moonshotai/kimi-k2.6` |
| RAG vector | `<VECTOR_ID>` (qdrant, `gemini-embedding-001@1536`, 37 chunks, at most 8 chunks per reply) |
| API key | workspace key `<API_KEY_ID>`, "fusion-mtg-player (divinci-table)", permissions `transcript:write,transcript:read` |

The release is a draft, and that's on purpose. `/api/v1/chat/completions` takes a draft release
when its `releaseId` is in the request body, so Fusion never has to be published or show up next
to the Docs releases. It is separate from the customer-facing Docs releases (v1/v2/v3) and their
vectors; none of them were changed.

The RAG vector holds three documents:

- `fusion-mtg-commander-rules-summary.md`: Commander rules in our own words (turn structure,
  priority and the stack, combat, mana, Auras, Roles, tokens, commander tax and damage). This is
  not the Comprehensive Rules text.
- `fusion-mtg-ellivere-deck-cards.md`: name, cost, type, P/T and rules text for every card in
  `decks/ellivere.json`.
- `fusion-mtg-ellivere-strategy.md`: the deck's plan: what to cast first, when to attack and
  block, how to rank threats, and how to use the board wipes.

## The API key

- **Infisical secret:** `DIVINCI_FUSION_API_KEY` (project `$INFISICAL_WORKSPACE_ID`, env `prod`,
  path `/`)
- **Env var the client reads:** `DIVINCI_FUSION_API_KEY`

Run the client under Infisical so the key never touches a file or the terminal:

```bash
infisical run --projectId="$INFISICAL_WORKSPACE_ID" --env=prod --path=/ -- /usr/bin/python3 your_script.py
```

Use `/usr/bin/python3`. The python.org build can fail TLS against `*.divinci.app` with
`CERTIFICATE_VERIFY_FAILED`. Never use an `infisical secrets` listing: it prints values.

## Request shape

```
POST https://api.divinci.app/api/v1/chat/completions
Authorization: Bearer $DIVINCI_FUSION_API_KEY
Content-Type: application/json

{"messages": [{"role": "user", "content": "<STATE + OPTIONS prompt>"}],
 "releaseId": "<RELEASE_ID>"}
```

The response is OpenAI-shaped. The move is in `choices[0].message.content`, as a JSON string:

```json
{"id": "...", "object": "chat.completion", "transcriptId": "...",
 "choices": [{"index": 0, "message": {"role": "assistant",
   "content": "{\"choice\": 2, \"say\": \"...\"}"}, "finish_reason": "stop"}]}
```

Only the last message in `messages` is used. Each call starts a new transcript, so every decision
prompt has to carry the whole state it needs. To keep table talk consistent across a turn, pass a
`transcriptId` from an earlier reply.

### Prompt format

The system prompt is stored on the release. Each user message should contain:

```
STATE:
{ ...JSON: life totals, your hand with card text, your permanents with #ids, mana,
  opponents' announced boards, commander damage, turn, phase... }
OPTIONS:
1. cast "Timely Ward" --on Ellivere
2. cast "Setessan Champion"
3. end
<optional one-line question, e.g. "Which spell do you cast?">
```

`brain_view()` in `table/player.py` already returns most of what STATE needs (it includes the
private hand, which is fine here, since the server→Divinci call is private). The OPTIONS lines
should be literal `tablectl` actions, so the server can run the chosen line directly.

The reply contract is `{"choice": <int>, "say": "<one short sentence>"}`. The prompt forbids `say`
from naming cards still in the hand. Treat that as a soft guarantee and keep the server's own
`say` filter (the one `tablectl say` applies) in front of the speaker.

### Python (stdlib only)

```python
import json, os, urllib.error, urllib.request

API = "https://api.divinci.app"
RELEASE_ID = "<RELEASE_ID>"

def ask_fusion(prompt: str, timeout: float = 60.0) -> dict:
    body = json.dumps({"messages": [{"role": "user", "content": prompt}],
                       "releaseId": RELEASE_ID}).encode()
    req = urllib.request.Request(API + "/api/v1/chat/completions", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + os.environ["DIVINCI_FUSION_API_KEY"],
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, text = resp.status, resp.read().decode()       # response text first
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode()
    try:
        data = json.loads(text)                                    # guarded parse #1: the envelope
    except json.JSONDecodeError:
        raise RuntimeError(f"non-JSON response (HTTP {status}): {text[:200]}")
    if status >= 400:
        raise RuntimeError(f"HTTP {status}: {json.dumps(data)[:300]}")
    content = data["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):                                  # tolerate a fenced reply
        content = content.strip("`").removeprefix("json").strip()
    try:
        move = json.loads(content)                                 # guarded parse #2: the move
    except json.JSONDecodeError:
        raise RuntimeError(f"reply was not JSON: {content[:200]}")
    if not isinstance(move.get("choice"), int):
        raise RuntimeError(f"reply has no integer choice: {move}")
    return move   # {"choice": 2, "say": "..."}
```

The caller should also check that `choice` is in range for the options it sent. On any error
(timeout, bad JSON, out of range), fall back to a safe default: the `end` / no-block option.

## Latency

These are end-to-end wall-clock times from this laptop, measured 2026-10-01, with retrieval taking
about 0.7 s of each:

| path | per decision |
|---|---|
| API key, `/api/v1/chat/completions` (6 calls) | **6.7–9.8 s**, median about 8 s |
| OAuth CLI route (3 calls) | 10–14 s |

That is longer than the table's ~3 s "One moment." filler, so expect the filler on most decisions.
Use a 30–60 s client timeout.

## Test decisions (2026-10-01)

1. **Turn 5, 5 mana:** the options were Timely Ward on Ellivere, Setessan Champion, Timely Ward on
   Paradise Druid, or pass. It chose **2, Setessan Champion** all three times, which fits the
   strategy guide's "card-draw engine early".
2. **Attack:** Ellivere was 9/9 with flying, Michael was at 14 with 8 commander damage and no
   flyers, and Sam had a flying Goose. It chose **1, attack Michael** all three times.
3. **Ghalta (12/12 trample) attacks at 34 life:** it took the hit (**4**) once and chump-blocked
   with the Saproling (**1**) twice. Both are reasonable. It never blocked with Ellivere, which
   would have died under Daybreak Coronet's first strike.

Every reply parsed as JSON, and no `say` named a card still in the hand. One early `say`
("prepare for some enchantment magic") hinted at the hand without naming a card.
