"""Identify a card from OCR'd lines by choosing among the names that are still possible.

We know the AI's decklist, so this is a choice over the cards left in its library, not open-ended
reading: a line only has to be close to one of ~100 known names, and a name the library no longer
holds cannot be chosen.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher


def norm(s: str) -> str:
    s = s.lower().replace("’", "'")
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def line_score(line: str, name: str) -> float:
    """Similarity of one OCR line to one card name. The name line on a card often carries extra
    OCR noise from the mana-cost symbols, so a name found INSIDE a longer line also counts."""
    a, b = norm(line), norm(name)
    if not a or not b:
        return 0.0
    whole = SequenceMatcher(None, a, b).ratio()
    part = 0.0
    if len(a) > len(b):
        # best window of the line with the name's length
        for i in range(0, len(a) - len(b) + 1):
            part = max(part, SequenceMatcher(None, a[i:i + len(b)], b).ratio())
    return max(whole, part * 0.97)


def identify(lines: list[str], candidates: list[str], accept=0.82, margin=0.06):
    """Return (name, score, runner_up_score) or (None, best, second).

    `accept`: minimum similarity. `margin`: the winner must beat the next DIFFERENT name by this
    much, so two similar names never resolve by a coin flip."""
    best = {}
    for name in set(candidates):
        best[name] = max((line_score(l, name) for l in lines), default=0.0)
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    if not ranked:
        return None, 0.0, 0.0
    top, s1 = ranked[0]
    s2 = ranked[1][1] if len(ranked) > 1 else 0.0
    if s1 >= accept and s1 - s2 >= margin:
        return top, s1, s2
    return None, s1, s2


def parse_decklist(text: str) -> list[str]:
    """Moxfield/Archidekt-style export: '1 Lightning Bolt', '1x Sol Ring', 'Sol Ring'.
    Section headers and set codes like '(M10) 146' are ignored. Returns one entry per copy."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//")) or line.endswith(":"):
            continue
        m = re.match(r"^(\d+)\s*x?\s+(.+)$", line)
        n, name = (int(m.group(1)), m.group(2)) if m else (1, line)
        name = re.sub(r"\s*\([A-Za-z0-9]+\).*$", "", name).strip()   # drop "(SET) 123"
        name = re.sub(r"\s*\*[A-Z]+\*\s*$", "", name).strip()          # drop "*F*" foil tags
        out += [name] * n
    return out


def identify_any(lines: list[str], catalog: list[str], accept=0.86):
    """Test mode: any Magic card, from Scryfall's full name catalog (~30k). Cheap prefilter with
    difflib.get_close_matches per line, then the same line_score as the deck matcher. Stricter
    `accept`, since a big candidate set makes near-misses likelier."""
    from difflib import get_close_matches
    cache = getattr(identify_any, "cache", None)
    if cache is None or cache[0] is not catalog:            # one index per catalog (it used to keep the first)
        # "_____ Goblin" (Unfinity) normalises to plain "goblin": a Goblin TOKEN read as that card.
        # Two names that normalise alike ("Rampant Growth" / "Rampant, Growth"): the card people play wins.
        try:
            import oracle
            rank = lambda n: oracle.rank(n) or 10 ** 6
        except Exception:
            rank = lambda n: 0
        index = {}
        for n in catalog:
            if "_" in n:
                continue
            k = norm(n)
            if k not in index or rank(n) < rank(index[k]):
                index[k] = n
        for n in catalog:                                   # a double-faced card shows ONE face at a time
            if " // " in n and "_" not in n:
                for face in n.split(" // "):
                    index.setdefault(norm(face), n)
        identify_any.cache = cache = (catalog, index, list(index))
    _, index, keys = cache
    best = {}
    for l in lines:
        nl = norm(l)
        if len(nl) < 3:
            continue
        for k in get_close_matches(nl, keys, n=3, cutoff=0.75):
            name = index[k]
            # score against the key that matched (a DFC face), not the full "A // B" name
            best[name] = max(best.get(name, 0.0), line_score(l, k))
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    if ranked and ranked[0][1] >= accept and (len(ranked) < 2 or ranked[0][1] - ranked[1][1] >= 0.04):
        return ranked[0][0], ranked[0][1], ranked[1][1] if len(ranked) > 1 else 0.0
    return None, ranked[0][1] if ranked else 0.0, 0.0


def find_cards_in_text(text: str, catalog: list[str], fuzzy_cutoff=0.88, max_words=6) -> list[str]:
    """Card names mentioned in a sentence ('Lightning Bolt targeting your Hellrider' →
    ['Lightning Bolt', 'Hellrider']). Exact n-gram lookup first; fuzzy only for multi-word windows
    and only against names sharing the window's first letter, so 35k names stay fast. Longest
    match wins and consumes its words, so 'Sheoldred, the Apocalypse' is not also 'Apocalypse'."""
    from difflib import get_close_matches
    idx = getattr(find_cards_in_text, "idx", None)
    if idx is None or idx[0] is not catalog:
        exact = {norm(n): n for n in catalog}
        by_letter: dict[str, list[str]] = {}
        for k in exact:
            by_letter.setdefault(k[:1], []).append(k)
        squashed = {k.replace(" ", ""): n for k, n in exact.items() if " " in k}
        idx = find_cards_in_text.idx = (catalog, exact, by_letter, squashed)
    _, exact, by_letter, squashed = idx
    words = norm(text).split()
    found, i = [], 0
    while i < len(words):
        hit = None
        for n in range(min(max_words, len(words) - i), 0, -1):
            w = " ".join(words[i:i + n])
            if w in exact:
                hit = (exact[w], n)
                break
            if w.replace(" ", "") in squashed and n <= 2:      # "Doomblade" for Doom Blade
                hit = (squashed[w.replace(" ", "")], n)
                break
            if n >= 2:
                # longer windows can afford a looser match: "swords to plosures" is still Swords to Plowshares
                cut = fuzzy_cutoff if n == 2 else min(fuzzy_cutoff, 0.84)
                m = get_close_matches(w, by_letter.get(w[:1], []), n=1, cutoff=cut)
                if m:
                    hit = (exact[m[0]], n)
                    break
        if hit and len(norm(hit[0])) >= 4:     # skip tiny names ('Ow', 'Fog') matching filler words
            found.append(hit[0])
            i += hit[1]
        else:
            i += 1
    return found


def near_card_candidates(text: str, catalog: list[str], cutoff=0.72, n=4) -> list[str]:
    """Card names CLOSE to a multi-word window of the sentence — for when speech-to-text mangled a
    name ("Lanour Elves"). Candidates only: a model or a person must pick among them."""
    from difflib import get_close_matches
    idx = getattr(find_cards_in_text, "idx", None)
    if idx is None or idx[0] is not catalog:
        find_cards_in_text("", catalog)
        idx = find_cards_in_text.idx
    _, exact, by_letter, _sq = idx
    words = norm(text).split()
    scored = {}
    for size in (2, 3, 4):
        for i in range(0, max(0, len(words) - size + 1)):
            w = " ".join(words[i:i + size])
            pool = [k for k in exact if abs(len(k) - len(w)) <= 4]
            for m in get_close_matches(w, pool, n=n, cutoff=cutoff):
                from difflib import SequenceMatcher
                scored[exact[m]] = max(scored.get(exact[m], 0), SequenceMatcher(None, w, m).ratio())
    return [k for k, _ in sorted(scored.items(), key=lambda kv: -kv[1])[:n]]


# ── matching a name by how it SOUNDS (speech-to-text spells names phonetically) ─────────────────
_ACTION = re.compile(r"^\W*(?:(?:and|so|then|okay|ok|now|um|uh)\W+)*(?:i\W+|high\W*|by\W+|un|my\W+)?"
                     r"(?:cast|kast|past|passed|cased|caste|test|task|tasks|class|cost|casting|play|plays|playing|flash|flashing)\b\W*"
                     r"(?:in\W+|out\W+|a\W+|an\W+|the\W+|my\W+)?", re.I)


_GLUED = re.compile(r"^\W*(?:(?:i|high|eye|aye|hi|by|my)\W+)?(\w{0,4}?(?:cast|kast|past|chas|cas|tas))(\w*)", re.I)
_NUM_WORDS = {"2": "to", "4": "for", "8": "ate", "1": "one"}


def name_phrase(text: str) -> str | None:
    """The words after the spoken action, as Whisper spells it: "I cast X", "High-cast X",
    "Uncast X", "I past X", "I task X", "Eyecast X" — and "Archastristic Study", where "I cast"
    melted into the name itself. None when the line doesn't open with an action."""
    m = _ACTION.match(text)
    if m:
        rest = text[m.end():]
    else:
        g = _GLUED.match(text)                    # "Eyecast Sorgs…", "Archastristic Study"
        if not g or len(text.split()) > 7:
            return None
        rest = g.group(2) + text[g.end():]
    rest = re.split(r"[.;!?]|\b(?:on|targeting|and then|and i|with|from)\b", rest, maxsplit=1, flags=re.I)[0]
    rest = re.sub(r"\b[2481]\b", lambda d: _NUM_WORDS[d.group(0)], rest.replace(",", " "))
    rest = re.sub(r"\s+", " ", rest).strip()
    if re.fullmatch(r"(?:(?:a|an|the|my|our|your|his|her|their|another|some|one|two|three)\s+)*(?:spell|land|creature|"
                    r"card|commander|thing|something|it|that|this|one|instant|sorcery|artifact|enchantment|token|"
                    r"planeswalker)s?", rest, re.I):
        return None                                    # "I cast my commander", "I play a land": no name said
    return rest if len(rest) >= 3 else None


def sound_key(t: str) -> str:
    t = re.sub(r"[^a-z ]", "", t.lower())
    words = []
    for w in t.split():
        w = re.sub(r"s$", "", w)
        for a, b in (("ph", "f"), ("ck", "k"), ("qu", "kw"), ("wh", "w"), ("gh", "g"), ("kn", "n"), ("gn", "n"),
                     ("c", "k"), ("q", "k"), ("x", "ks"), ("z", "s"), ("th", "t"), ("dh", "d")):
            w = w.replace(a, b)
        w = re.sub(r"(?<=[aeiouy])[wy]", "", w)
        w = re.sub(r"([a-z])\1+", r"\1", w)
        w = re.sub(r"(?<!^)[aeiouy]+", "a", w)
        words.append(w)
    return "".join(words)


_SOUND_IDX = None


def sound_candidates(phrase: str, catalog: list[str], n: int = 5) -> list[tuple[str, float]]:
    """Card names that SOUND like the phrase, best first, with a 0–1 score. Front faces only;
    names of 1–6 words. ~40 ms over 35k names (length prefilter, then difflib)."""
    global _SOUND_IDX
    from difflib import SequenceMatcher
    if _SOUND_IDX is None or _SOUND_IDX[0] is not catalog:
        idx: dict[int, list[tuple[str, str]]] = {}
        for c in catalog:
            front = c.split(" // ")[0]
            k = sound_key(front)
            if k:
                idx.setdefault(len(k), []).append((k, c))
        _SOUND_IDX = (catalog, idx)
    idx = _SOUND_IDX[1]
    q = sound_key(phrase)
    if len(q) < 3:
        return []
    out = []
    lo, hi = int(len(q) * 0.7), int(len(q) * 1.35) + 1
    sm = SequenceMatcher(autojunk=False)
    sm.set_seq2(q)
    for L in range(lo, hi + 1):
        for k, c in idx.get(L, ()):
            sm.set_seq1(k)
            if sm.real_quick_ratio() < 0.6 or sm.quick_ratio() < 0.6:
                continue
            r = sm.ratio()
            if r >= 0.6:
                out.append((c, r))
    out.sort(key=lambda x: -x[1])
    return out[:n]


_FILLER = {"a", "an", "the", "of", "my", "this", "that", "some", "another"}
WEAKEST: dict[str, float] = {}      # per candidate: its worst-matching word (last spoken_card call)


def _wstrict(a: str, b: str) -> float:
    """Per-word check for the WEAKEST word: close spelling, or the same sounds starting with the same
    sound ("Codimus" ~ "Kodama's": k-d-m). "relts" ~ "vault" share a vowel-collapsed key but not a
    start — that is how "mana relts" became Mana Vault."""
    from difflib import SequenceMatcher
    sp = SequenceMatcher(None, a, b).ratio()
    ka, kb = sound_key(a), sound_key(b)
    so = SequenceMatcher(None, ka, kb).ratio() if ka[:1] == kb[:1] else 0.0
    return max(sp, so)


def _wsim(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return max(SequenceMatcher(None, a, b).ratio(), SequenceMatcher(None, sound_key(a), sound_key(b)).ratio())


def _aligned(phrase_words: list[str], card_words: list[str]) -> float:
    """Card words matched in order to one or two consecutive heard words; average similarity,
    with a small penalty for heard words left over."""
    best, _aligned.min_word = 0.0, 0.0
    for start in range(len(phrase_words)):
        i, sims, strict = start, [], []
        for w in card_words:
            cand = []
            for k in (1, 2):
                if i + k <= len(phrase_words):
                    cand.append((_wsim(w, "".join(phrase_words[i:i + k])), k))
            if not cand:
                sims.append(0.0)
                continue
            s, k = max(cand)
            sims.append(s)
            strict.append(_wstrict(w, "".join(phrase_words[i:i + k])))
            i += k
        used = i - start
        extra = max(0, len(phrase_words) - used - start)
        weights = [max(3, len(w)) for w in card_words]      # "Kodama's" identifies the card more than "Reach"
        score = sum(s * w for s, w in zip(sims, weights)) / sum(weights) - 0.04 * extra - 0.02 * start
        if score > best:
            best, _aligned.min_word = score, min(strict) if strict else 0.0
    return best


_NOT_CASTABLE = re.compile(r"\b(Plane|Phenomenon|Scheme|Vanguard|Dungeon|Emblem|Token|Card|Conspiracy|Attraction|Contraption|Stickers)\b")


def _type(c: str) -> str:
    try:
        import oracle
        return (oracle.card(c) or {}).get("type") or ""
    except Exception:
        return ""


def spoken_card(phrase: str, catalog: list[str], n: int = 5) -> list[tuple[str, float]]:
    """The card a mis-transcribed name most likely was: phonetic + spelling prefilter over the whole
    catalog, then word-by-word alignment. Scores ~0.9+ are near-certain; 0.75–0.9 want a second
    opinion (Gemma picks among the candidates, with a 'none of these')."""
    from difflib import SequenceMatcher, get_close_matches
    idx = getattr(find_cards_in_text, "idx", None)
    if idx is None or idx[0] is not catalog:
        find_cards_in_text("", catalog)
        idx = find_cards_in_text.idx
    exact = idx[1]
    p = norm(phrase)
    pool = {c for c, _ in sound_candidates(phrase, catalog, n=40)}
    pool |= {exact[k] for k in get_close_matches(p, list(exact), n=40, cutoff=0.5)} if len(p) >= 4 else set()
    pw = [w for w in p.split() if w not in _FILLER] or p.split()
    import math
    try:
        import oracle
        rank = oracle.rank
    except Exception:                                   # no Oracle db: sound alone
        rank = lambda c: None
    scored = []
    for c in pool:
        parts = c.split(" // ")
        if len(parts) == 2 and parts[0] == parts[1]:
            continue                                    # "Forest // Forest" (a double-faced basic)
        if _NOT_CASTABLE.search(_type(c)):
            continue                                    # "Llanowar" is a Plane, not a card anyone casts
        cw = [w for w in norm(parts[0]).split() if w not in _FILLER] or norm(c).split()
        r = rank(c)
        # also the whole name squashed together: "Lenorels" is Llanowar Elves said as one word
        squashed = SequenceMatcher(None, sound_key("".join(pw)), sound_key("".join(cw))).ratio() - 0.03
        # The table's prior, like a player's: a half-heard name is far more likely Sol Ring (EDHREC
        # rank 1) than Soul Read (15921). Worth up to ~0.12 of similarity.
        prior = 0.5 if r is None else max(0.0, 1 - math.log10(r) / math.log10(40000))
        a = _aligned(pw, cw)
        weakest = _aligned.min_word if a >= squashed else squashed        # squashed = one-word match
        WEAKEST[c] = weakest
        scored.append((c, max(a, squashed) + 0.12 * (prior - 0.5)))
    scored.sort(key=lambda x: -x[1])
    return scored[:n]


BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}
MIN_WORD = 0.55                     # tests/hearing_names.py: 36/49 recovered, 0 wrong (0.5 let "mana relts" become Mana Vault)


def recognise_spoken(text: str, catalog: list[str], picker=None) -> tuple[list[str], dict]:
    """The card(s) a play names, even misheard. Exact n-gram match first; if that finds nothing, or
    only a one-word card inside a longer name ("Rift" in "Psychonic Rift"), the name phrase is
    matched by sound (spoken_card) and accepted by CODE only when a player would be sure too:

      · a close match to a card people actually play (EDHREC rank ≤ 500) or a basic land, or
      · a near-perfect match to anything (≥ 0.95, clear of the runner-up).

    Measured on every mishearing seen in this project's runs (tests/hearing_names.py): each correct
    recovery was a popular card or a basic; each wrong one was obscure ("Plague Mare" for Legion
    Warboss). Gemma is NOT asked: e2b picked "Ballroom" for "I play Boris" (Forest). `picker` is
    kept for callers that want a second opinion on what code rejects."""
    import oracle
    found = find_cards_in_text(text, catalog)
    phrase = name_phrase(text)
    played = None
    info = {"phrase": phrase}
    def played(c):
        r = oracle.rank(c)
        return c in BASICS or (r is not None and r <= 500)
    t = " " + norm(text) + " "
    # a FUZZY hit ("spells last" → Spell Blast) must be a card people play; exact words are trusted
    found = [c for c in found if (" " + norm(c) + " ") in t or played(c)]
    if phrase:                                    # "I cast about ten spells" is not the card Cast Out
        found = [c for c in found if norm(c) in norm(phrase) or not norm(text).startswith(("i " + norm(c).split()[0]))]
    if not phrase:
        return found, info
    partial = found and len(found) == 1 and len(norm(found[0]).split()) == 1 and len(norm(phrase).split()) >= 2 \
        and norm(found[0]) != norm(phrase)
    in_phrase = [c for c in found if norm(c.split(" // ")[0]) in norm(phrase)]
    if found and in_phrase and not partial:
        return found, info
    # the words after "I cast" name no card we found — "I cast Swords to Plurshers on Claude's Kor
    # Spiritdancer" found only the TARGET; the spell itself must still be resolved from its phrase
    others = [c for c in found if c not in in_phrase]
    cands = [(c, sc) for c, sc in spoken_card(phrase, catalog) if not c.startswith("A-")]   # Alchemy rebalances
    info["candidates"] = [(c, round(sc, 2)) for c, sc in cands[:4]]
    if not cands:
        return others, info
    top, s1 = cands[0]
    s2 = cands[1][1] if len(cands) > 1 else 0.0
    r1 = oracle.rank(top)
    if (s1 >= 0.95 and s1 - s2 >= 0.1) or (s1 >= 0.9 and s1 - s2 >= 0.08 and r1 is not None and r1 <= 5000):
        info["how"] = "sound"                      # "Legion Warbus" 0.93 → Legion Warboss (rank 1799)
        return [top] + others, info
    # like a player: among names that sound about as close as the best, the one people PLAY wins
    # ("Sol Ray": Sorry 0.89, Sol Ring 0.87 → Sol Ring); two played cards too close to call → ask
    pc = [(c, sc) for c, sc in cands if played(c)]
    # …and every word of it must actually match something heard: "mana relts" is not Mana Vault
    # just because "mana" is right and Mana Vault is popular (a WRONG card, in the noisy far-field goal)
    if pc and pc[0][1] >= 0.78 and pc[0][1] >= s1 - 0.04 and (len(pc) < 2 or pc[0][1] - pc[1][1] >= 0.02) \
            and WEAKEST.get(pc[0][0], 1.0) >= MIN_WORD:
        info["how"] = "sound+prior"
        return [pc[0][0]] + others, info
    return others, info                           # not sure of the spell: better unsaid than a wrong card
