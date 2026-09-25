"""Router regression set: 14 hand-labelled lines, including traps (a player MENTIONED but not
addressed). Needs Ollama with gemma4:e2b. Fails if any AI answers when it shouldn't, if the WRONG
AI answers, or if more than one line routes wrongly. Hand-written and tuned against, so it is a
floor, not an accuracy estimate — add real table talk to CASES as it's collected.

  ~/.venvs/table/bin/python table/tests/route_eval.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.pop("TYPESAFE_API_KEY", None)
import voice
AI=[{"name":"Talrand","commander":"Talrand, Sky Summoner","has_deck":True},{"name":"Krenko","commander":"Krenko, Tin Street Kingpin"}]
HU=[{"name":"Sam","commander":"Sheoldred, the Apocalypse"},{"name":"Michael","commander":"Ghalta, Primal Hunger"}]
# (utterance, acceptable kinds, acceptable addressees, should an AI speak? who)
CASES=[
 ("Talrand, who are you attacking this turn?", {"question"}, {"Talrand"}, "Talrand"),
 ("Krenko, truce this turn?", {"deal"}, {"Krenko"}, "Krenko"),
 ("Hey Krenko, what's in your graveyard?", {"question"}, {"Krenko"}, "Krenko"),
 ("I cast Lightning Bolt on Krenko's goblin.", {"play"}, {"nobody in particular","the whole table"}, None),
 ("Talrand attacks me every single turn, it's so annoying.", {"chatter"}, {"nobody in particular","the whole table"}, None),
 ("What does trample do again?", {"question","rules"}, {"the whole table","nobody in particular"}, None),
 ("Sam, is it your turn?", {"question"}, {"the whole table","nobody in particular"}, None),
 ("Talrand, if you don't attack me I'll leave your drakes alone.", {"deal"}, {"Talrand"}, "Talrand"),
 ("I pass the turn.", {"play"}, {"nobody in particular","the whole table"}, None),
 ("Anyone need a drink?", {"chatter","question"}, {"the whole table","nobody in particular"}, None),
 ("Krenko, how many cards are in your hand?", {"question"}, {"Krenko"}, "Krenko"),
 ("I attack Talrand with Ghalta.", {"play"}, {"nobody in particular","the whole table"}, None),
 ("Talrand, want to team up against Sam?", {"deal"}, {"Talrand"}, "Talrand"),
 ("Can someone pass the dice?", {"chatter","question"}, {"the whole table","nobody in particular"}, None),
 ("Sam, if you leave me alone this turn I won't attack you.", {"deal","question","chatter","play"}, {"the whole table","nobody in particular"}, None),
 ("Michael, want to team up against Talrand?", {"deal","question","chatter"}, {"the whole table","nobody in particular"}, None),
 ("Talrand, your turn.", {"turn"}, {"Talrand"}, "Talrand"),
 ("OK Talrand, go ahead, you're up.", {"turn"}, {"Talrand"}, "Talrand"),
 ("Talrand, what cards are in your hand?", {"question"}, {"Talrand"}, "Talrand"),
 ("Talrand, who are you attacking this turn?", {"question"}, {"Talrand"}, "Talrand"),
 ("Talrand, is it your turn?", {"question"}, {"Talrand"}, "Talrand"),
 ("It's my turn, I draw.", {"play","chatter"}, {"nobody in particular","the whole table"}, None),
 ("Yes, deal!", {"deal","chatter","question","play"}, {"Talrand","Krenko","the whole table","nobody in particular"}, "ANY"),
]
voice.route_local("warm up", [], AI, HU)
ok=spk=0; ms=[]
for text, kinds, addrs, speaker in CASES:
    r=voice.route_local(text, [], AI, HU); ms.append(r["ms"])
    sp,_=voice.decide_reply(r,["Talrand","Krenko"])
    good=r["kind"] in kinds and r["addressee"] in addrs
    right_speaker = (speaker == "ANY") or (sp == speaker)   # "Yes, deal!" alone is ambiguous: any outcome is fine
    ok+=good; spk+=right_speaker
    print(f"{'✓' if good else '✗'}{'✓' if right_speaker else '✗'} {r['kind']:8} → {r['addressee']:20} speaks: {sp or '-':8} | {text}")
print(f"route correct {ok}/{len(CASES)} | right AI speaks (or silence) {spk}/{len(CASES)} | router p50 {sorted(ms)[len(ms)//2]} ms, max {max(ms)} ms")
sys.exit(0 if spk == len(CASES) and ok >= len(CASES) - 1 else 1)
