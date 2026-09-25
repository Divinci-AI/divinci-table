"""Card pool for the djev Commander prototype.

Real card names, SIMPLIFIED rules text: every spell resolves at sorcery speed (there is no
stack, so no instants/counterspells), and each card does only the part the engine models.
`text` is exactly what the model is shown, so it must describe what the ENGINE does, not
what the printed card does.
"""

# kind: land | creature | sorcery | artifact
# cost: (generic, coloured pips of the deck colour)
# kw: keywords the combat code understands: flying, reach, deathtouch, lifelink, haste,
#     vigilance, trample
CARDS = {
    # ── lands / rocks ──────────────────────────────────────────────────────────
    "Island":   dict(kind="land", colour="U", text="Tap: add U."),
    "Swamp":    dict(kind="land", colour="B", text="Tap: add B."),
    "Mountain": dict(kind="land", colour="R", text="Tap: add R."),
    "Forest":   dict(kind="land", colour="G", text="Tap: add G."),
    "Sol Ring": dict(kind="artifact", cost=(1, 0), mana=2, mana_colour="C",
                     text="Artifact. Tap: add 2 colourless mana."),
    "Arcane Signet": dict(kind="artifact", cost=(2, 0), mana=1, mana_colour="deck",
                          text="Artifact. Tap: add 1 mana of your commander's colour."),

    # ── blue ───────────────────────────────────────────────────────────────────
    "Talrand, Sky Summoner": dict(kind="creature", cost=(2, 2), pt=(2, 2), kw=[], legendary=True,
        text="Whenever you cast a sorcery, create a 2/2 blue Drake token with flying."),
    "Air Elemental": dict(kind="creature", cost=(3, 2), pt=(4, 4), kw=["flying"], text="Flying."),
    "Mulldrifter": dict(kind="creature", cost=(4, 1), pt=(2, 2), kw=["flying"], etb="draw2",
        text="Flying. When it enters, draw 2 cards."),
    "Man-o'-War": dict(kind="creature", cost=(2, 1), pt=(2, 2), kw=[], etb="bounce_creature",
        text="When it enters, return target creature to its owner's hand."),
    "Divination": dict(kind="sorcery", cost=(2, 1), effect="draw2", text="Draw 2 cards."),
    "Ponder": dict(kind="sorcery", cost=(0, 1), effect="draw1", text="Draw a card."),
    "Cyclonic Rift": dict(kind="sorcery", cost=(1, 1), effect="bounce_opp_nonland",
        text="Return target nonland permanent an opponent controls to its owner's hand."),

    # ── black ──────────────────────────────────────────────────────────────────
    "Sheoldred, the Apocalypse": dict(kind="creature", cost=(2, 2), pt=(4, 5), kw=["deathtouch"],
        legendary=True,
        text="Deathtouch. Whenever an opponent draws a card, they lose 1 life."),
    "Vampire Nighthawk": dict(kind="creature", cost=(1, 2), pt=(2, 3),
        kw=["flying", "deathtouch", "lifelink"], text="Flying, deathtouch, lifelink."),
    "Gray Merchant of Asphodel": dict(kind="creature", cost=(3, 2), pt=(2, 4), kw=[], etb="gary",
        text="When it enters, each opponent loses life equal to your devotion to black (black "
             "pips among permanents you control); you gain the total lost."),
    "Murder": dict(kind="sorcery", cost=(1, 2), effect="destroy_creature",
        text="Destroy target creature."),
    "Night's Whisper": dict(kind="sorcery", cost=(1, 1), effect="draw2_lose2",
        text="Draw 2 cards and lose 2 life."),
    "Damnation": dict(kind="sorcery", cost=(2, 2), effect="wrath", text="Destroy all creatures."),

    # ── red ────────────────────────────────────────────────────────────────────
    "Krenko, Tin Street Kingpin": dict(kind="creature", cost=(2, 1), pt=(1, 2), kw=[],
        legendary=True, attack="krenko",
        text="Whenever it attacks, put a +1/+1 counter on it, then create 1/1 Goblin tokens "
             "equal to its power."),
    "Lightning Bolt": dict(kind="sorcery", cost=(0, 1), effect="bolt",
        text="Deal 3 damage to any target (a creature or an opponent)."),
    "Goblin Guide": dict(kind="creature", cost=(0, 1), pt=(2, 2), kw=["haste"], text="Haste."),
    "Goblin Instigator": dict(kind="creature", cost=(1, 1), pt=(1, 1), kw=[], etb="goblin_token",
        text="When it enters, create a 1/1 Goblin token."),
    "Hellrider": dict(kind="creature", cost=(2, 2), pt=(3, 3), kw=["haste"], static="hellrider",
        text="Haste. Whenever a creature you control attacks, Hellrider deals 1 damage to the "
             "player it attacks."),
    "Shivan Dragon": dict(kind="creature", cost=(4, 2), pt=(5, 5), kw=["flying"], text="Flying."),
    "Blasphemous Act": dict(kind="sorcery", cost=(8, 1), effect="act", cost_mod="per_creature",
        text="Costs 1 less for each creature on the battlefield. Deals 13 damage to each creature."),

    # ── green ──────────────────────────────────────────────────────────────────
    "Ghalta, Primal Hunger": dict(kind="creature", cost=(10, 2), pt=(12, 12), kw=["trample"],
        legendary=True, cost_mod="own_power",
        text="Trample. Costs X less, where X is the total power of creatures you control."),
    "Llanowar Elves": dict(kind="creature", cost=(0, 1), pt=(1, 1), kw=[], mana=1, mana_colour="deck",
        text="Tap: add G (not the turn it enters)."),
    "Cultivate": dict(kind="sorcery", cost=(2, 1), effect="cultivate",
        text="Put a basic land from your library onto the battlefield tapped and another into "
             "your hand."),
    "Rampant Growth": dict(kind="sorcery", cost=(1, 1), effect="ramp1",
        text="Put a basic land from your library onto the battlefield tapped."),
    "Harmonize": dict(kind="sorcery", cost=(2, 2), effect="draw3", text="Draw 3 cards."),
    "Thragtusk": dict(kind="creature", cost=(4, 1), pt=(5, 3), kw=[], etb="gain5",
        text="When it enters, you gain 5 life."),
    "Craw Wurm": dict(kind="creature", cost=(4, 2), pt=(6, 4), kw=[], text="Vanilla 6/4."),
    "Thornweald Archer": dict(kind="creature", cost=(1, 1), pt=(2, 1), kw=["reach", "deathtouch"],
        text="Reach, deathtouch."),
    "Silklash Spider": dict(kind="creature", cost=(3, 2), pt=(2, 7), kw=["reach"], text="Reach."),
    "Beast Within": dict(kind="sorcery", cost=(2, 1), effect="beast_within",
        text="Destroy target permanent. Its controller creates a 3/3 Beast token."),
    "Craterhoof Behemoth": dict(kind="creature", cost=(5, 3), pt=(5, 5), kw=["haste"], etb="hoof",
        text="Haste. When it enters, creatures you control get +X/+X and trample until end of "
             "turn, where X is the number of creatures you control."),

    # ── tokens (never in a library) ────────────────────────────────────────────
    "Drake": dict(kind="creature", token=True, pt=(2, 2), kw=["flying"], text="Token. Flying."),
    "Goblin": dict(kind="creature", token=True, pt=(1, 1), kw=[], text="Token."),
    "Beast": dict(kind="creature", token=True, pt=(3, 3), kw=[], text="Token."),
}

# 40-card decks (commander + 39), duplicates allowed — a prototype, not a singleton 100.
_SPELLS = {
    "U": ["Air Elemental", "Mulldrifter", "Man-o'-War", "Divination", "Ponder", "Cyclonic Rift"],
    # Balance (measured 2026-09-24, 400 mirror games): with Gray Merchant + Damnation + the printed
    # 2-life Sheoldred drain, black won 88% of heuristic mirrors and 70% of random ones. Both
    # stay defined in CARDS; they are just out of the decks.
    "B": ["Vampire Nighthawk", "Murder", "Night's Whisper"],
    "R": ["Lightning Bolt", "Goblin Guide", "Goblin Instigator", "Hellrider", "Shivan Dragon",
          "Blasphemous Act"],
    # Green had no answer to fliers and was dogpiled out by ~round 7; reach creatures fix that.
    "G": ["Llanowar Elves", "Cultivate", "Thornweald Archer", "Silklash Spider", "Thragtusk",
          "Craw Wurm", "Beast Within", "Craterhoof Behemoth"],
}
BASIC = {"U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
COMMANDERS = {
    "U": "Talrand, Sky Summoner",
    "B": "Sheoldred, the Apocalypse",
    "R": "Krenko, Tin Street Kingpin",
    "G": "Ghalta, Primal Hunger",
}


def build_deck(colour: str) -> list[str]:
    """16 basics + Sol Ring + Arcane Signet + 21 spells cycled from the colour's pool."""
    pool = _SPELLS[colour]
    spells = [pool[i % len(pool)] for i in range(21)]
    return [BASIC[colour]] * 16 + ["Sol Ring", "Arcane Signet"] + spells
