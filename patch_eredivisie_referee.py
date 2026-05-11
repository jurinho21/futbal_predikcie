"""
patch_eredivisie_referee.py — Doplní referee do existujúcich JSON súborov Eredivisie.
Fetchuje len tie zápasy, kde referee je prázdny reťazec.
"""

import sys
import json
import time
import requests
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DATA_DIR   = Path(__file__).parent / "data" / "eredivisie"
MATCH_BASE = (
    "https://eredivisie.nl/cache/site/EredivisieNL/json/matches"
    "/aouykkl1rt7zo06sg0kbzkbh0/{oid}.json"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":    "application/json",
    "Referer":   "https://eredivisie.eu/",
}


def get_referee(officials: list) -> str:
    for o in officials or []:
        if isinstance(o, dict) and o.get("type") == "Main":
            return f"{o.get('given_name', '')} {o.get('family_name', '')}".strip()
    return ""


files = sorted(DATA_DIR.glob("*.json"))
to_patch = [
    p for p in files
    if json.loads(p.read_text(encoding="utf-8")).get("referee") == ""
]

print(f"Súborov bez sudcu: {len(to_patch)} / {len(files)}")

ok = fail = 0
for i, path in enumerate(to_patch, 1):
    oid = path.stem
    try:
        raw      = requests.get(MATCH_BASE.format(oid=oid), headers=HEADERS, timeout=20).json()
        referee  = get_referee(raw.get("officials"))
        saved    = json.loads(path.read_text(encoding="utf-8"))
        saved["referee"] = referee
        path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{i}/{len(to_patch)}] ✓  {saved.get('home_team')} vs {saved.get('away_team')}  → {referee or '(prázdny)'}")
        ok += 1
    except Exception as exc:
        print(f"[{i}/{len(to_patch)}] ✗  {oid} — {exc}")
        fail += 1

    if i < len(to_patch):
        time.sleep(1.0)

print(f"\nHotovo: {ok} opravených, {fail} chýb")
