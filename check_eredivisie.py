import json, glob, sys
sys.stdout.reconfigure(encoding="utf-8")

files = sorted(glob.glob("data/eredivisie/*.json"))
print(f"Celkom suborov: {len(files)}")

missing_round, missing_ref, no_stats, no_offsides = 0, 0, 0, 0
for f in files:
    d = json.load(open(f, encoding="utf-8"))
    h = d.get("home") or {}
    if not d.get("round"):       missing_round += 1
    if not d.get("referee"):     missing_ref   += 1
    if not h.get("shots"):       no_stats      += 1
    if not h.get("offsides"):    no_offsides   += 1

print(f"Bez kola (round):    {missing_round}")
print(f"Bez rozhodcu:        {missing_ref}")
print(f"Bez statistik shots: {no_stats}")
print(f"Bez offsides:        {no_offsides}")

# Priklad
d = json.load(open(files[10], encoding="utf-8"))
h = d.get("home", {})
print(f"\nPriklad: {d.get('home_team')} vs {d.get('away_team')}")
print(f"  round={d.get('round')}, date={d.get('date')}, status={d.get('status')}")
def _s(obj, k):
    v = obj.get(k)
    if isinstance(v, dict): return v.get("total")
    return v

print(f"  shots_home={_s(h,'shots')}, corners={_s(h,'corners')}, fouls={_s(h,'fouls')}")
print(f"  possession={_s(h,'possession')}, yellow={_s(h,'yellow_cards')}")

