import ast, sys, json, os
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import player as _pl
src = (HERE / "server.py").read_text()
fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "aim_refusal")
g = {"teammates": lambda n: [], "VPS": {}, "COMBAT": {"pending": [], "hits": []}, "in_combat": lambda o, p: False, "__builtins__": __builtins__}
exec(compile(ast.Module([fn], []), "aim", "exec"), g)
class Owner:
    name = "Michael"
    def shown(self, p): return "a face-down 2/2" if p.face_down else p.name
    def stats(self, p): return (2, 2)
    def kws(self, p): return []
land = {"name": "Federation Outpost", "types": ["Land"], "subtypes": [], "manaValue": 0}
art = {"name": "Tricorder", "types": ["Artifact"], "subtypes": ["Equipment"], "manaValue": 1}
cre = {"name": "Horta", "types": ["Creature"], "subtypes": ["Horta"], "manaValue": 2}
for c in (land, art, cre):
    p = _pl.Perm(c, face_down=True)
    for slot in ({"kinds": ["Land"]}, {"kinds": ["Artifact"]}, {"kinds": ["Creature"]}, {"kinds": ["Creature"], "not_kinds": ["Artifact"]}):
        print(f"hidden={c['name']:<20} slot={json.dumps(slot):<48} aim_refusal -> {g['aim_refusal']('Claude', slot, Owner(), p)!r}")
# the assertion: what a slot says about a face-down card must not depend on the card under it
res = {}
for c in (land, art, cre):
    p = _pl.Perm(c, face_down=True)
    for slot in ({"kinds": ["Land"]}, {"kinds": ["Artifact"]}, {"kinds": ["Creature"]}, {"kinds": ["Creature"], "not_kinds": ["Artifact"]}, {"kinds": ["Creature"], "not_kinds": ["Creature"]}):
        res.setdefault(json.dumps(slot), set()).add(g["aim_refusal"]("Claude", slot, Owner(), p))
bad = {k: v for k, v in res.items() if len(v) > 1}
print("ORACLE" if bad else "NO ORACLE: aim_refusal answers the same for every face-down card", bad or "")
sys.exit(1 if bad else 0)
