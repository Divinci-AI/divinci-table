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
    index = identify_any.index = getattr(identify_any, "index", None) or {norm(n): n for n in catalog}
    keys = list(index)
    best = {}
    for l in lines:
        nl = norm(l)
        if len(nl) < 3:
            continue
        for k in get_close_matches(nl, keys, n=3, cutoff=0.75):
            name = index[k]
            best[name] = max(best.get(name, 0.0), line_score(l, name))
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
        idx = find_cards_in_text.idx = (catalog, exact, by_letter)
    _, exact, by_letter = idx
    words = norm(text).split()
    found, i = [], 0
    while i < len(words):
        hit = None
        for n in range(min(max_words, len(words) - i), 0, -1):
            w = " ".join(words[i:i + n])
            if w in exact:
                hit = (exact[w], n)
                break
            if n >= 2:
                m = get_close_matches(w, by_letter.get(w[:1], []), n=1, cutoff=fuzzy_cutoff)
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
    _, exact, by_letter = idx
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
