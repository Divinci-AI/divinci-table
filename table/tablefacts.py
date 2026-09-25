"""Table facts the CODE owns: things said out loud that change public state, and questions whose
answer is public state. None of this is a judgment call, so none of it goes to a model.

  parse_life("Ghalta attacks Claude for twelve.", players)    → [("Claude", -12)]
  public_question("Claude, what's your life total?")          → "life"
  removal_target("I cast Swords on Claude's Ellivere.", ...)  → "Ellivere of the Wild Court"
"""
from __future__ import annotations

import re

_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                     "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}


def words_to_numbers(text: str) -> str:
    """'thirty-five' → '35', 'twelve' → '12' (Whisper writes numbers either way)."""
    def one(m):
        parts = re.split(r"[\s-]+", m.group(0).lower())
        n = 0
        for p in parts:
            n += _TENS.get(p, 0) + _UNITS.get(p, 0)
        return str(n)
    words = "|".join(sorted(list(_TENS) + list(_UNITS), key=len, reverse=True))
    return re.sub(rf"\b(?:{words})(?:[\s-](?:{words}))?\b", one, text, flags=re.I)


def normalize(text: str, players: list[str]) -> str:
    """What speech-to-text does to table talk: curly apostrophes, "SAMS at 22" for "Sam's at 22",
    "Sam Gaines 3" for "Sam gains 3", numbers as words."""
    t = text.replace("\u2019", "'").replace("\u2018", "'")
    for p in players:
        if len(p) >= 4:                              # "Claudeville-7" → "Claude 7" (Whisper glues words on)
            t = re.sub(rf"\b{re.escape(p)}(?!'s\b|s\b)[a-z]+-?(?=\s|\d|$)", f"{p} ", t, flags=re.I)
        t = re.sub(rf"\b{re.escape(p)}s\b", f"{p}'s", t, flags=re.I)
        t = re.sub(rf"\b{re.escape(p)}\b", p, t, flags=re.I)
    t = re.sub(r"\bgaines\b", "gains", t, flags=re.I)
    t = re.sub(r"\b(?:r|ah|eye|aye)[- ](take|took|lose|gain)\b", r"I \1", t, flags=re.I)   # "R-Take 2" (Karen's "I")
    t = words_to_numbers(t)
    # "I'm Ad 31", "Sam's et 12": Whisper's spelling of "at" before a number
    return re.sub(r"(\b(?:I'm|I am|'s|is|are|you're)\s+)(?:ad|add|et|it|app|and)\s+(\d+)", r"\1at \2", t, flags=re.I)


I_UNRESOLVED = "?"                                   # "I" said by a voice the table doesn't know


def _who(name: str, players: list[str], nick: dict[str, str]) -> str | None:
    n = name.strip().lower()
    for p in players:
        if n == p.lower():
            return p
    return nick.get(n)


def parse_life(text: str, players: list[str], nick: dict[str, str] | None = None,
               addressed: str | None = None, speaker: str | None = None) -> list[tuple[str, int, str]]:
    """Life changes said out loud, as (player, amount, "delta"|"set").

    players: every name at the table. nick: extra spoken names → player (commander nicknames
    map to their OWNER: "Ghalta" → "Michael"; used only for "X attacks/hits Y", where it's the target
    that matters). addressed: the AI a sentence opened by naming ("Claude, you take five").
    "I" is never resolved: without knowing who is speaking, "I'm at 35" is ambiguous, so it is left
    for the table's life buttons (a named statement — "Michael's at 35" — works)."""
    nick = {k.lower(): v for k, v in (nick or {}).items()}
    t = normalize(text, players)
    names = "|".join(re.escape(p) for p in sorted(players, key=len, reverse=True))
    out: list[tuple[str, int, str]] = []
    if not names:
        return out
    you = addressed if addressed else None

    def name_or_you(s):
        if you and s.strip().lower() in ("you", "yourself"):
            return you
        return _who(s, players, {})

    subj = rf"({names}|you)"
    # Damage that LANDED: "<attacker> hits <player> for N", "deals N (damage) to <player>". An ATTACK is
    # not damage yet — the defender may block — so "attacks X for N" is parse_attack's, not this.
    for m in re.finditer(rf"\b(?:hits?|deals? damage to)\s+{subj}\s+for\s+(\d+)", t, re.I):
        who = name_or_you(m.group(1))
        if who:
            out.append((who, -int(m.group(2)), "delta"))
    for m in re.finditer(rf"\bdeals?\s+(\d+)(?:\s+damage)?\s+to\s+{subj}\b", t, re.I):
        who = name_or_you(m.group(2))
        if who:
            out.append((who, -int(m.group(1)), "delta"))
    # "<player> takes/loses N", "<player> gains N (life)"
    for m in re.finditer(rf"\b{subj}(?:'s)?\s+(?:take|takes|took|lose|loses|lost)\s+(\d+)", t, re.I):
        who = name_or_you(m.group(1))
        if who:
            out.append((who, -int(m.group(2)), "delta"))
    for m in re.finditer(rf"\b{subj}\s+(?:gain|gains|gained)\s+(\d+)", t, re.I):  # after normalize()
        who = name_or_you(m.group(1))
        if who:
            out.append((who, int(m.group(2)), "delta"))
    # "<player> is at N", "<player>'s at N", "you're at N" (to the addressed AI), "put <player> at N"
    for m in re.finditer(rf"\b({names})(?:'s|\s+is|\s+goes\s+to|\s+drops\s+to|\s+(?:is\s+)?now)\s+(?:at\s+|down\s+to\s+)?(\d+)\b", t, re.I):
        who = _who(m.group(1), players, {})
        if who and not re.match(r"\s*(?:life|damage|cards?|turn)", t[m.end():], re.I):
            out.append((who, int(m.group(2)), "set"))
    if you:
        for m in re.finditer(r"\byou(?:'re| are)\s+(?:at|on|down to)\s+(\d+)\b", t, re.I):
            out.append((you, int(m.group(1)), "set"))
    # "I": only with a speaker the table recognised by voice (speakers.py). Unknown → I_UNRESOLVED.
    me = speaker if speaker in players else I_UNRESOLVED
    for m in re.finditer(r"\bI(?:'m| am)\s+(?:at|on|down to|now at)\s+(\d+)\b", t, re.I):
        out.append((me, int(m.group(1)), "set"))
    for m in re.finditer(r"\bI(?:'ll| will)?\s+(?:take|took|lose|lost)\s+(\d+)\b", t, re.I):
        out.append((me, -int(m.group(1)), "delta"))
    for m in re.finditer(r"\bI\s+(?:gain|gained)\s+(\d+)\b", t, re.I):
        out.append((me, int(m.group(1)), "delta"))
    seen, uniq = set(), []
    for c in out:                                     # one statement, one change
        if c[0] not in seen:
            seen.add(c[0])
            uniq.append(c)
    return [c for c in uniq if 0 < abs(c[1]) <= 200 or c[2] == "set"]


def bare_name(text: str, players: list[str]) -> str | None:
    """"Jess." / "It's Jess." / "Jess here." — the answer to "Who's that?"."""
    m = re.match(r"^\W*(?:it's|it is|that's|that was|me,?|this is)?\s*([A-Za-z][A-Za-z'-]+)(?:\s+here)?\W*$", text.replace("\u2019", "'"), re.I)
    if not m:
        return None
    return next((p for p in players if p.lower() == m.group(1).lower()), None)


def enrollment(text: str, players: list[str]) -> str | None:
    """'This is Michael.' / 'Hi, I'm Sam, playing Krenko.' / 'My name is Michael.' → the player."""
    m = re.match(r"^\s*(?:(?:hi|hey|hello|okay|ok)[,!.]?\s+)?(?:everyone[,!.]?\s+)?(?:this is|it's|i'm|i am|my name is|call me)"
                 r"\s+([A-Za-z][A-Za-z'-]+)", text.replace("\u2019", "'"), re.I)
    if not m:
        return None
    return next((p for p in players if p.lower() == m.group(1).lower()), None)


_PUBLIC = [
    ("life", r"\b(life( total)?|how much life|how many life|what are you at|you at)\b"),
    ("hand", r"\bhow many cards\b.*\bhand\b|\bhand size\b|\bcards in (your )?hand\b.*\bhow many\b"),
    ("library", r"\b(library|deck)\b.*\b(how many|left|size)\b|\bhow many cards\b.*\b(library|deck)\b"),
    ("graveyard", r"\b(graveyard|yard)\b"),
    ("board", r"\b(on the battlefield|on your board|board|in play|what do you (have|control)|your permanents|what('s| is) out)\b"),
    ("tax", r"\bcommander tax\b|\bhow much (does|would) (ellivere|your commander) cost\b"),
    ("mana", r"\b(how much mana|mana (open|up|available)|untapped)\b"),
]


def parse_attack(text: str, ai_name: str) -> dict | None:
    """An attack declared AT the AI: "Ghalta attacks Claude for twelve", "I swing at Claude with
    Ghalta", "attacking you for 6, trample" (said to the AI). Returns {"amount": N or None,
    "trample": bool}; the caller finds the attacking card among the cards heard."""
    t = normalize(text, [ai_name])
    tgt = rf"(?:{re.escape(ai_name)}|you)"
    t = re.sub(r",\s*", " ", t)                        # "Bahimoth, Attacks, Claude, for 8" (Whisper's commas)
    t = re.sub(r"\b(?:a\s+|of\s+)?(?:tax|taxes|attax|attacked)(?=\s)", "attacks", t, flags=re.I)   # "Sage of Tax Claude for 3"
    roman = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12}
    t = re.sub(rf"\b({re.escape(ai_name)}|you)\s+(XII|XI|X|IX|VIII|VII|VI|V|IV|III|II)\b\.?",     # "attacks Claude V." = for five
               lambda m: f"{m.group(1)} for {roman[m.group(2)]}", t)
    m = re.search(rf"\b(?:attacks?|attacking|swings?|swinging|going)\s+(?:at\s+|into\s+|for\s+)?{tgt}\b(?:[^.]*?\bfor\s+(\d+))?", t, re.I)
    if not m:
        return None
    # "if you don't attack me, I won't attack you" is a deal, not an attack
    before = t[max(0, m.start() - 30):m.start()].lower()
    if re.search(r"\b(don'?t|won'?t|not|never|if|unless|will|would|should|could|might|going to|gonna|wanna|thinking)\s*(?:\w+\s+){0,2}$", before) \
            or "?" in t[m.start():]:
        return None
    if re.search(rf"\b(?:attacks?|attacking|swings?)\s+(?:at\s+)?you\b", t, re.I) and not re.match(
            rf"\s*(?:hey\s+|ok(?:ay)?\s+)?{re.escape(ai_name)}\b", text, re.I) and ai_name.lower() not in t.lower():
        return None                                       # "you" only counts when the line is to the AI
    after = t[m.end():]
    n = m.group(1) or (re.search(r"\bfor\s+(\d+)", after) or re.match(r"[\s,-]*(\d+)\b", after) or [None, None])[1]
    return {"amount": int(n) if n else None, "trample": bool(re.search(r"\btrampl", t, re.I))}


def card_question(text: str, cards_heard: list[str], last_card: str | None, own_commander: str | None) -> str | None:
    """'What does Rhystic Study do?', 'what does that card do?', 'what does Ellivere do?', 'how does
    your commander work?' → the card whose rules text answers it. Rules text is public."""
    t = text.lower()
    if not re.search(r"\b(what does|what do(?:es)? .* do|how does|explain|what's .* do|read (?:me )?|what is)\b", t) \
            or not re.search(r"\b(do|does|work|works|say|says|explain|read)\b", t):
        return None
    if re.search(r"\b(that|this|the) card\b|\bit do\b", t) and last_card:
        return last_card
    if own_commander and re.search(r"\b(your commander|" + re.escape(own_commander.split(",")[0].split(" of ")[0].lower()) + r")\b", t):
        return own_commander
    return cards_heard[0] if cards_heard else None


def say_card(name: str, text: str, limit: int = 2) -> str:
    """A card's rules text, as it would be read aloud: reminder text dropped, first sentences only."""
    t = re.sub(r"\([^)]*\)", "", text.split("\n//\n")[0])
    t = re.sub(r"\{T\}", "tap", t)
    t = re.sub(r"\{(\w)\}", r"\1", t).replace("\n", " ")
    parts = re.split(r"(?<=[.!])\s+", t.strip())
    out = " ".join(parts[:limit])
    return f"{name}: {out}" + (" …and more." if len(parts) > limit else "")


def public_question(text: str) -> str | None:
    """A question whose whole answer is public state. Checked in the order above; 'how many cards
    are in your hand' is PUBLIC (the count), unlike 'what cards are in your hand'."""
    t = re.sub(r"\bman(?:or|na|ner)\b", "mana", text.lower())     # "how much manor…" (Whisper)
    if not t.rstrip().endswith("?") and not re.search(r"\b(what|how|tell me|remind)\b", t):
        return None
    if re.search(r"\b(what|which) cards?\b.*\bhand\b", t):          # asking for the hand itself
        return None
    for kind, pat in _PUBLIC:
        if re.search(pat, t):
            return kind
    return None


def answer_public(kind: str, pub: dict, life_table: dict, name: str) -> str:
    if kind == "life":
        others = ", ".join(f"{k} {v}" for k, v in life_table.items() if k != name)
        return f"I'm at {pub['life']}." + (f" Table: {others}." if others else "")
    if kind == "hand":
        n = pub["hand"]
        return f"{n} card{'s' if n != 1 else ''} in hand."
    if kind == "library":
        return f"{pub['library']} cards left in my library."
    if kind == "graveyard":
        g = pub.get("graveyard") or []
        return ("In my graveyard: " + ", ".join(g) + ".") if g else "My graveyard is empty."
    if kind == "board":
        b = [re.sub(r"\s*\[(tapped|sick)\]", "", x) for x in pub.get("battlefield") or []]
        lands = pub.get("lands", 0)
        s = ("I have " + ", ".join(b) + ".") if b else "No creatures or enchantments yet."
        return s + f" And {lands} land{'s' if lands != 1 else ''}."
    if kind == "tax":
        where = "in the command zone" if pub.get("commander_in_zone") else "on the battlefield"
        return f"{pub['commander']} is {where}; commander tax is {pub['commander_tax']}."
    if kind == "mana":
        return f"I have {pub['untapped_mana']} mana untapped."
    return ""


def removal_target(text: str, ai_name: str, own: list[str]) -> str | None:
    """Which of the AI's permanents a spoken removal names: "…on Claude's Ellivere", "…targeting
    Starfield Mystic", "exile your Kor Spiritdancer" (said to the AI). Names may be shortened to
    their first word or comma part, as players do ("Ellivere")."""
    t = normalize(text, [ai_name]).lower()
    if not re.search(r"\b(on|targeting|target|at|destroy|destroys|exile|exiles|kill|kills|bounce|"
                     r"return|returns|sacrifice|your|" + re.escape(ai_name.lower()) + r"'?s)\b", t):
        return None
    aliases = []
    for full in own:
        base = full.lower()
        short = re.split(r",| of the | of ", base)[0].strip()
        aliases += [(base, full), (short, full)]
    aliases.sort(key=lambda x: -len(x[0]))
    owner = rf"(?:{re.escape(ai_name.lower())}(?:'?s)?|your)\s+"     # "on Claude Siona": the 's gets lost
    for alias, full in aliases:
        if re.search(rf"(?:{owner}|\b(?:on|targeting|target|destroy|exile|kill|bounce|return)\s+(?:the\s+)?)"
                     rf"{re.escape(alias)}\b", t):
            return full
    # misheard ("Claude's Tivetaker" for Tithe Taker): the words right after "Claude's"/"your"
    from difflib import SequenceMatcher
    m = re.search(rf"{owner}([\w' -]{{3,40}})", t)
    if m:
        words = re.findall(r"[a-z']+", m.group(1))
        best, score = None, 0.0
        for alias, full in aliases:
            n = len(alias.split())
            for k in {max(1, n - 1), n, n + 1}:
                cand = "".join(words[:k])
                r = SequenceMatcher(None, alias.replace(" ", ""), cand).ratio()
                if r > score:
                    best, score = full, r
        if score >= 0.75:
            return best
    return None


# ── more table talk the code owns ─────────────────────────────────────────────────────────────
def is_hold(text: str, ai_name: str) -> bool:
    """"Wait, Claude, hold on." / "Hold on." / "Stop, stop." — stop talking, don't act."""
    t = normalize(text, [ai_name]).lower().strip()
    return bool(re.match(rf"^(?:{re.escape(ai_name.lower())}[,.!\s]+)?(?:(?:wait|whoa|woah|hold on|hang on|stop|shh+|quiet|one sec|one second)[,.!\s]*)+"
                         rf"(?:{re.escape(ai_name.lower())}[,.!\s]*)?(?:(?:hold on|wait|stop|hang on|a sec(?:ond)?)[,.!\s]*)*$", t))


def attack_total(text: str) -> int | None:
    """"…three goblins, that's nine", "…for nine total", "nine damage all together"."""
    t = words_to_numbers(text)
    m = re.search(r"\bthat'?s\s+(\d+)\b|\bfor\s+(\d+)\s+total\b|\b(\d+)\s+(?:damage\s+)?(?:all\s+together|altogether|in total|total)\b", t, re.I)
    return int(next(g for g in m.groups() if g)) if m else None


def is_counter(text: str) -> bool:
    """"In response, I cast Counterspell." / "I counter that." / "Negate your spell." """
    return bool(re.search(r"\b(in response|counter that|counter it|counters? (?:that|it|your)|i counter)\b", text, re.I))


def correction(text: str) -> str | None:
    """"No, I said Llanowar Elves." / "Sorry, I meant Sol Talisman." → the corrected words."""
    m = re.match(r"^\W*(?:no|nope|sorry|correction|wait)?\W*(?:i said|i meant|it's|it was|that was|i cast)\s+(.+)$", text, re.I)
    if m and re.match(r"^\W*(no|nope|sorry|correction|i meant|i said)\b", text, re.I):
        return m.group(1).strip()
    return None


def claims_ai_did(text: str, ai_name: str) -> bool:
    """Someone ELSE saying the AI played something ("Claude casts Wrath of God") — the AI knows its own
    plays; words at the table must not put cards on its board or wipe it."""
    t = normalize(text, [ai_name])
    return bool(re.search(rf"\b{re.escape(ai_name)}\s+(?:casts?|cast|played|plays|plays a|just cast)\b", t, re.I)) \
        and not re.match(rf"^\s*{re.escape(ai_name)}\s*[,:]", t)


def hand_probe(text: str, cards: list[str]) -> bool:
    """A question about whether a SPECIFIC card is in its hand — even "yes" or "no" leaks."""
    if not cards:
        return False
    return bool(re.search(r"\b(in your hand|do you have|are you holding|you holding|you('re| are) holding|"
                          r"got (?:a|an|the)?|holding onto|still have|told me you had)\b", text, re.I))


def cant_be_targeted(card: dict, tapped: bool, eff: str) -> str | None:
    """What the table can rule from Oracle text: hexproof/shroud (including "as long as it's
    untapped") stop a targeted spell; indestructible survives "destroy". Measured: Doom Blade on an
    untapped Paradise Druid was silently ignored — the AI should say WHY."""
    name = card.get("name", "It")
    t = (card.get("text") or "").lower()
    kws = [k.lower() for k in (card.get("keywords") or [])]
    if "shroud" in kws or re.search(r"\bhas shroud\b", t):
        return f"{name} has shroud — it can't be targeted."
    hexproof = "hexproof" in kws or re.search(r"^hexproof\b|\bhas hexproof\b", t, re.M)
    if hexproof and "as long as it's untapped" in t and tapped:
        hexproof = False
    if hexproof:
        return f"{name} has hexproof — it can't be targeted."
    if eff == "destroy" and ("indestructible" in kws or re.search(r"^indestructible\b|\bhas indestructible\b", t, re.M)):
        return f"{name} is indestructible — it stays."
    return None
