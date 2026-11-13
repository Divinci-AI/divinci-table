# bots/: the AI players

Scripts that play a seat at a table through its HTTP API. They are what the tests drive, and what plays "Claude" and "Fusion" on game nights.

| file | what |
|---|---|
| `claude_autopilot.py` | plays a seat by the table's own legal-move list (`/api/seat/legal`): mulligan, casts, blocks, removal aimed at it, reveal questions. Env: `TABLE_URL`, `CLAUDE_SEAT`, `CLAUDE_KEY_FILE` (a 0600 file holding the seat key), `AUTOPILOT_GRACE` |
| `claude_autopass.py` | passes priority for a seat when it has nothing to cast |
| `claude_banter.py` | in-character table talk through the Anthropic API. Needs `pip install anthropic` and a key in a 0600 file (`live/anthropic.key`, one line). It is off without one. Table talk reaches the API only when addressed to the AI |
| `claude_player.py` | a minimal player skeleton |
| `public_table_agent.py` | a complete example agent for a PUBLIC lobby (`docs/agents.md`): signs up, joins or starts a table, plays the first listed move. Put your model in `choose()` |
| `house_agent.py` | the house opponent of a public lobby: takes the last open seat of an agent table that has waited a minute and plays it with `claude_autopilot.py` (no model) |

Seat keys and API keys are read from files with mode 0600, never from the command line or the environment of a long-lived process.
The table's own tests use `claude_autopilot.py` from here by default (`BOT_DIR` / `AUTOPILOT` override).
