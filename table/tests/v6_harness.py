"""Shared set-up for the v6 tests: the retro_game4 harness table, with every Star Trek card the v6 tests name added to both decks."""
import os, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
import retro_game4_test as R  # noqa: E402

EXTRA = ["Saurian Explorer", "Wesley Crusher, Starfleet Cadet", "Dahar Master", "Khaaaaaaaaaaaannn!", "Worf, Honor-Bound Warrior", "Tactical Officer", "Cantankerous Captain",
         "Tenacious Drone", "Photosynthetic Physician", "Relentless Drednok", "Daniels, Temporal Agent", "Consider the Prime Directive", "D'Ghor Bloodletter",
         "Lore, Positronic Provocateur", "Federation Field Medic", "A Good Day to Die", "La'An Noonien-Singh, Security", "Una Chin-Riley, Number One", "Tal Shiar Operative",
         "Federation Outpost", "Tea, Earl Grey, Hot", "Locutus, the One Who Speaks", "Ghost of Illyria", "Starfleet Security", "Highly Illogical", "Constable Odo",
         "Culber and Stamets, Star-Crossed", "Astute Terraformer", "Suus Mahna Practitioner", "Rogue Artificial Intelligence", "Impossible Choice", "Make It So",
         "Test of Conviction", "Savvy Profiteer", "DOT-7 Repair Squad", "Pet Sehlat", "Subcommander T'Pol", "Federation Probe", "Dal R'El, Augment Orphan", "Cybernetic Specialist",
         "Commander Raffi Musiker", "Emergency Holographic Crew", "Dominion Assault Force", "Set Phasers to . . .", "Test of Identity", "Warp Speed", "Shields Up!", "Melkotian Adjudicator", "Seven of Nine", "Horta", "Mugato",
         "Pattern Buffer Preservation", "Rutherford, Upgraded Ensign", "First Contact", "Guidance Failure", "Khan, Despot by Design", "The Doctor, EMH", "Temporal Conqueror",
         "Automated Warfare System", "Cloak and Phaser", "Hemmer, Fixing What Is Broken", "Encounter the Unknown", "Plasma Cascade", "Humpback Whales", "Cetacean Ops", "Eject the Warp Core"]
for n in EXTRA:
    R.POOL.setdefault(n, 4)
R.DECK_MICHAEL = R.deck_file("Michael")
R.DECK_CLAUDE = R.deck_file("Claude")
check = R.check
Table = R.Table


def run(sections):
    only = os.environ.get("V6_ONLY")
    for title, fn in sections:
        if only and not any(k.lower() in title.lower() for k in only.split("|")):
            continue
        print("\n" + title, flush=True)
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — one section that throws is one failure
            import traceback
            traceback.print_exc()
            check(False, f"{title}: raised {type(e).__name__}", e)
    print(("\nFAILED: " + "; ".join(R.FAILS)) if R.FAILS else "\nall good", flush=True)
    sys.exit(1 if R.FAILS else 0)


def choices(t, seat):
    return t.state(seat).get("pending_choices") or []


def answer(t, seat, want=None, none=False):
    """Answer the seat's first waiting question: the option whose label contains `want` (else the first), or "no target"."""
    pend = choices(t, seat)
    assert pend, f"nothing is waiting on {seat}"
    it = pend[0]
    if none:
        return t.call("POST", "/api/brain/choose", {"seat": seat, "id": it["id"], "target": {}}, key=t.keys[seat])
    opts = it["slot"].get("options") or []
    o = (next((x for x in opts if want and str(x.get("player") or "").lower() == want.lower()), None)
         or next((x for x in opts if want and want.lower() in x["label"].lower()), None)) if want else (opts[0] if opts else None)
    assert o is not None, f"no option '{want}' among {[x['label'] for x in opts]}"
    return t.call("POST", "/api/brain/choose", {"seat": seat, "id": it["id"], "target": {k: v for k, v in o.items() if k != "label"}}, key=t.keys[seat])


def cast(t, seat, name, lands=None, **body):
    """Put the card in hand (set-up), then cast it through the seat's own key."""
    t.hand(seat, name)
    return t.act(seat, "cast", name=name, **body)


def says(t, since=0):
    return [e.get("text") or "" for e in t.events(since) if e.get("type") in ("say", "table-note")]
