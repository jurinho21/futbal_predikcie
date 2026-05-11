import requests, json, re, sys, time
sys.stdout.reconfigure(encoding="utf-8")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
BASE = "https://www.proleague.be"
ROUND_ID = "2e222cc4-f3f9-4030-9356-26ed1250fe70"

html = requests.get(BASE + "/fr/jpl-calendar", headers=HEADERS, timeout=20).text
hash_ = re.search(r'/_next/static/([^/]+)/_buildManifest', html).group(1)

def get_md(round_id, extra_params=""):
    url = (f"{BASE}/_next/data/{hash_}/fr/jpl-calendar.json"
           f"?roundId={round_id}&params=fr&params=jpl-calendar{extra_params}")
    r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=20)
    if r.status_code != 200:
        return None
    return r.json()["pageProps"]["data"]["page"]["grids"][0]["areas"][0]["modules"][0]["data"]

md = get_md(ROUND_ID)
gameweeks = md["gameweeks"]
print(f"Celkom {len(gameweeks)} gameweeks")

# Skus J1 cez params
gw1 = gameweeks[0]
gw1_id = gw1["id"]

# Skus rozne varianty
variants = [
    f"&params={gw1_id}",
    f"&gameweekId={gw1_id}&params={gw1_id}",
]
for v in variants:
    md2 = get_md(ROUND_ID, v)
    matches = md2.get("matches", []) if md2 else []
    if isinstance(matches, dict): matches = matches.get("data", [])
    slug = matches[0].get("slug", "")[:50] if matches else "-"
    print(f"variant '{v[:40]}': {len(matches)} matches | {slug}")
    time.sleep(0.3)

# Ak nic nefunguje - BFS approach: zisti kolko slugov pokryje crawl z J30
print("\n--- BFS test: slugy z J30 matchov ---")
j30_slugs = [m.get("slug") for m in md.get("matches", []) if m.get("slug")]
print(f"Start: {len(j30_slugs)} J30 slugov")
all_slugs = set(j30_slugs)
for slug in j30_slugs[:3]:  # Test len 3
    url2 = f"{BASE}/fr/matchs/{slug}"
    html2 = requests.get(url2, headers=HEADERS, timeout=20).text
    m2 = re.search(r'__NEXT_DATA__[^>]+>(.*?)</script>', html2, re.DOTALL)
    text2 = m2.group(1) if m2 else ""
    new = set(re.findall(r'saison-2025-2026-jupiler-pro-league-\d+-[a-z0-9-]+-vs-[a-z0-9-]+', text2))
    added = new - all_slugs
    all_slugs |= new
    print(f"  {slug[:50]}: +{len(added)} novych (total: {len(all_slugs)})")
    time.sleep(0.5)
