import json, pathlib

json_dir = pathlib.Path("data/json")
total = has_stats = has_fouls = has_sot = has_corners = has_lineup = 0

for f in json_dir.glob("*.json"):
    d = json.loads(f.read_text(encoding="utf-8"))
    total += 1
    stats = d.get("stats", {}).get("total", {})
    if stats:
        has_stats += 1
    if stats.get("fouls"):
        has_fouls += 1
    if stats.get("shots_on_target"):
        has_sot += 1
    if stats.get("corners"):
        has_corners += 1
    lineups = d.get("lineups", {})
    if lineups.get("home") or lineups.get("away"):
        has_lineup += 1

print("Celkovo zapasov:", total)
print("Ma statistiky:  ", has_stats)
print("Ma fauly:       ", has_fouls)
print("Ma SoT:         ", has_sot)
print("Ma rohy:        ", has_corners)
print("Ma zostavy:     ", has_lineup)

sample = sorted(json_dir.glob("*.json"))[5]
d = json.loads(sample.read_text(encoding="utf-8"))
meta = d.get("meta", {})
print("\nUkazka:", meta.get("home_team"), "vs", meta.get("away_team"))
stats = d.get("stats", {}).get("total", {})
for k in ["fouls", "shots_on_target", "corners", "xg"]:
    v = stats.get(k)
    print(f"  {k}: {v}")
print("  Eventy:", len(d.get("events", [])))
print("  Hracov domaci:", len(d.get("lineups", {}).get("home", [])))
print("  Rozhodca:", meta.get("referee"))
if d.get("events"):
    print("  Prvy event:", d["events"][0])
