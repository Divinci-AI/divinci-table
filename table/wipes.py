"""Which of a seat's permanents a board wipe hits, read from the spell's own text. Pure: it takes a list of permanents (anything with
`.is_(type)`), so it is unit-testable without the table server.   "Destroy all creatures", "Destroy all artifacts, creatures, and
enchantments", "Return all nonland permanents to their owners' hands", "Exile all permanents"."""
import re

TYPES = ("artifact", "creature", "enchantment", "planeswalker", "land")


def victims(battlefield, text: str):
    """The permanents the wipe `text` hits, or None when the text does not say by type (the caller then refuses: do it by hand)."""
    t = (text or "").lower()
    if "all creatures" in t or "each creature" in t:
        return [p for p in battlefield if p.is_("Creature")]
    m = re.search(r"(?:destroy|exile|return) all (?:other )?([^.]*)", t)
    if not m:
        return None
    clause = re.split(r" to their owners|\bthat\b|\bfrom\b|\byou\b", m.group(1))[0]
    if re.search(r"\bnon(?!land)\w+", clause):
        return None                                    # "noncreature permanents" etc.: not a plain type, so not guessed
    if "nonland permanents" in clause:
        return [p for p in battlefield if not p.is_("Land")]
    if "permanents" in clause:
        return list(battlefield)
    kinds = [k for k in TYPES if re.search(rf"\b{k}s?\b", clause)]
    if not kinds:
        return None
    return [p for p in battlefield if any(p.is_(k.capitalize()) for k in kinds)]
