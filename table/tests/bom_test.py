"""hardware/bom.json against the manual and the goal doc: every piece we order is accounted for.

- every part has a valid status, and every part still in the build is used by a step that exists;
- a part the manual uses appears in the manual by its exact name, so the parts table and the bom can't drift;
- every Amazon link in the manual or the goal doc is a part in the bom;
- every Amazon part in a cart is linked from a doc (no orphan purchases);
- removed parts never appear in the manual (nobody re-buys them from a stale page);
- the cart subtotals add up, and the manual states them.

No hardware, no network. `--self-test` breaks the bom in each of those ways and checks the test notices.
"""
import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOM = ROOT / "hardware" / "bom.json"
STATUSES = {"cart", "on-hand", "check", "to-print", "list-only", "removed"}
ASIN = re.compile(r"amazon\.com/dp/([A-Z0-9]{10})")


def headings(text: str) -> list[str]:
    return [l.lstrip("#").strip() for l in text.splitlines() if l.startswith("#")]


def has_step(text: str, step: str) -> bool:
    """`Step 4` matches '## Step 4 — …'; `G0` matches '## G0 — …'; `M0` matches '### M0 — …'."""
    return any(re.match(rf"{re.escape(step)}\b", h) for h in headings(text))


def problems(bom: dict, docs: dict[str, str]) -> list[str]:
    bad = []
    items = bom["items"]
    ids = [i.get("id") for i in items]
    for d in {x for x in ids if ids.count(x) > 1}:
        bad.append(f"duplicate id {d}")
    for i in items:
        iid = i.get("id", "?")
        st = i.get("status")
        if st not in STATUSES:
            bad.append(f"{iid}: unknown status {st!r}")
            continue
        if not i.get("name"):
            bad.append(f"{iid}: no name")
        if "price" in i and not isinstance(i["price"], (int, float)):
            bad.append(f"{iid}: price is not a number")
        if i.get("source") == "amazon" and not re.fullmatch(r"[A-Z0-9]{10}", i.get("asin", "")):
            bad.append(f"{iid}: Amazon part without a valid asin")
        if st == "removed":
            if not i.get("reason"):
                bad.append(f"{iid}: removed without a reason")
            if i.get("asin") and i["asin"] in docs["manual"]:
                bad.append(f"{iid}: removed, but the manual still links {i['asin']}")
            if i["name"] in docs["manual"]:
                bad.append(f"{iid}: removed, but the manual still names it")
            continue
        uses = i.get("used_in") or []
        if not uses:
            bad.append(f"{iid}: in the build but used by no step")
        for u in uses:
            doc = docs.get(u.get("doc"))
            if doc is None:
                bad.append(f"{iid}: unknown doc {u.get('doc')!r}")
                continue
            if not has_step(doc, u.get("step", "")):
                bad.append(f"{iid}: {u['doc']} has no heading {u.get('step')!r}")
            if u["doc"] == "manual" and i["name"] not in doc:
                bad.append(f"{iid}: the manual doesn't name it ({i['name']!r})")
            if u["doc"] != "manual" and i["name"] not in doc and (not i.get("asin") or i["asin"] not in doc):
                bad.append(f"{iid}: {u['doc']} mentions neither its name nor its asin")
        if st == "cart" and i.get("source") == "amazon" and not any(i["asin"] in t for t in docs.values()):
            bad.append(f"{iid}: in the Amazon cart but linked from no doc")

    known = {i.get("asin") for i in items}
    for name, text in docs.items():
        for a in sorted(set(ASIN.findall(text)) - known):
            bad.append(f"{name} links amazon.com/dp/{a}, which isn't in the bom")

    for cart, info in bom["carts"].items():
        total = round(sum(i["price"] for i in items if i.get("status") == "cart" and i.get("source") == cart), 2)
        if abs(total - info["subtotal"]) > 0.005:
            bad.append(f"{cart}: cart items add up to ${total:.2f}, bom says ${info['subtotal']:.2f}")
        if f"${info['subtotal']:.2f}" not in docs["manual"]:
            bad.append(f"{cart}: the manual doesn't state the ${info['subtotal']:.2f} subtotal")
    return bad


def load_docs(bom: dict) -> dict[str, str]:
    return {k: (ROOT / p).read_text() for k, p in bom["docs"].items()}


def self_test(bom: dict, docs: dict[str, str]) -> list[str]:
    """Each mutation must be caught; returns the ones that slipped through."""
    def item(b, iid):
        return next(i for i in b["items"] if i["id"] == iid)

    def m_status(b, d): item(b, "electromagnet")["status"] = "bought-maybe"
    def m_orphan_step(b, d): item(b, "diodes")["used_in"] = [{"doc": "manual", "step": "Step 42"}]
    def m_no_use(b, d): item(b, "springs")["used_in"] = []
    def m_renamed(b, d): item(b, "ball-head")["name"] = "Some other ball head"
    def m_unknown_link(b, d): d["manual"] += "\n[x](https://www.amazon.com/dp/B000000000)\n"
    def m_removed_in_manual(b, d): d["manual"] += "\nMG90S micro servos (2-pack)\n"
    def m_removed_asin(b, d): d["manual"] += "\n(https://www.amazon.com/dp/B071GL3XXQ)\n"
    def m_total(b, d): item(b, "steel-discs")["price"] = 21.79
    def m_dup(b, d): b["items"].append(copy.deepcopy(item(b, "zip-ties")))
    def m_orphan_cart(b, d):
        b["items"].append({"id": "ghost", "name": "Ghost part", "source": "amazon", "asin": "B0GHOST000",
                           "price": 0, "status": "cart", "used_in": [{"doc": "manual", "step": "Step 1"}]})
    def m_goal_step(b, d): item(b, "so101-kit")["used_in"] = [{"doc": "goal", "step": "G99"}]

    if problems(bom, docs):
        return ["(the real bom already fails, so the mutations prove nothing)"]
    slipped = []
    for m in (m_status, m_orphan_step, m_no_use, m_renamed, m_unknown_link, m_removed_in_manual,
              m_removed_asin, m_total, m_dup, m_orphan_cart, m_goal_step):
        b, d = copy.deepcopy(bom), dict(docs)
        m(b, d)
        if not problems(b, d):
            slipped.append(m.__name__)
    if problems(bom, docs):
        slipped.append("(a mutation leaked into the real bom)")
    return slipped


def main() -> int:
    bom = json.loads(BOM.read_text())
    docs = load_docs(bom)
    bad = problems(bom, docs)
    for b in bad:
        print("❌", b)
    live = [i for i in bom["items"] if i["status"] != "removed"]
    print(f"{len(live)} parts in the build, {len(bom['items']) - len(live)} removed; "
          + ", ".join(f"{c} ${v['subtotal']:.2f}" for c, v in bom["carts"].items()))
    if "--self-test" in sys.argv:
        slipped = self_test(bom, docs)
        print("self-test:", "all mutations caught" if not slipped else f"NOT caught: {slipped}")
        bad += slipped
    print("✅ bom ok" if not bad else f"❌ {len(bad)} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
