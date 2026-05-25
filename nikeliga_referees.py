"""
nikeliga_referees.py — stiahne rozhodcov z PDF obsadenia SFZ (futbalsfz.sk)

Závislosť:
    pip install pdfplumber

Použitie:
    python nikeliga_referees.py ./data
    python nikeliga_referees.py ./data --dry-run
"""

import argparse
import io
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

import pdfplumber
from bs4 import BeautifulSoup
from http_utils import SESSION

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# Sekcie iných súťaží — ich výskyt ukončí parsovanie Niké ligy
OTHER_LEAGUES = [
    "monacobet", "tipos", "3lz,", "3ls,", "3lv,", "i.lsd", "slovnaft",
]


def _season_url() -> str:
    today = date.today()
    year = today.year if today.month >= 8 else today.year - 1
    return f"https://futbalsfz.sk/sezona-{year}/{year + 1}/"


def _norm(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in nfkd if not unicodedata.combining(c))
    return s.lower().strip()


def _team_match(short: str, full: str) -> bool:
    """Vráti True ak skrátený názov z PDF zodpovedá plnému názvu tímu."""
    ns, nf = _norm(short), _norm(full)
    # Priama zhoda alebo podreťazec
    if ns in nf or nf in ns:
        return True
    # "D.Streda" → "dunajska streda"
    ns_nodot = ns.replace(".", " ").replace("  ", " ").strip()
    if ns_nodot in nf:
        return True
    # Porovnaj slová (každé slovo z PDF musí byť v plnom názve)
    words = [w for w in ns_nodot.split() if len(w) > 2]
    return bool(words) and all(w in nf for w in words)


def fetch_latest_pdf_url(season_url: str | None = None) -> str | None:
    """Vráti URL najnovšieho 'obsadenie' PDF z futbalsfz.sk."""
    url = season_url or _season_url()
    resp = SESSION.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    pdf_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            href = "https://" + href.lstrip("/")
        if ("obsadenie-rozhodcov-a-delegatov-sfz-c-" in href
                and "zmeny" not in href
                and href.endswith(".pdf")):
            pdf_links.append(href)

    if not pdf_links:
        return None

    def _seq(h: str) -> int:
        m = re.search(r"-c-(\d+)\.pdf$", h)
        return int(m.group(1)) if m else 0

    return max(pdf_links, key=_seq)


def parse_pdf_referees(pdf_bytes: bytes, debug: bool = False) -> list[dict]:
    """
    Parsuje PDF obsadenia SFZ a vráti zoznam záznamov pre Niké ligu.
    Každý záznam: {home_short, away_short, referee, section}
    """
    results = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages):
            lines = _reconstruct_lines(page)
            if debug:
                print(f"\n--- Strana {page_num + 1} ({len(lines)} riadkov) ---")
                for ln in lines:
                    print(f"  {repr(ln)}")
            _parse_lines(lines, results, debug=debug)

    return results


def _reconstruct_lines(page) -> list[str]:
    """
    Rekonštruuje riadky textu z pozícií slov (extract_words).
    Robustnejšie ako extract_text() pri viacstĺpcovom layoute.
    """
    words = page.extract_words(x_tolerance=4, y_tolerance=4, keep_blank_chars=False)
    if not words:
        return []

    # Zoskup slová podľa y-koordinátu (top) s toleranciou ±4pt
    rows: dict[int, list] = {}
    for w in words:
        y = int(round(w["top"]))
        placed = False
        for ey in list(rows.keys()):
            if abs(ey - y) <= 4:
                rows[ey].append(w)
                placed = True
                break
        if not placed:
            rows[y] = [w]

    lines = []
    for y in sorted(rows.keys()):
        row_words = sorted(rows[y], key=lambda w: w["x0"])
        lines.append(" ".join(w["text"] for w in row_words))

    return lines


def _parse_lines(lines: list[str], results: list, debug: bool = False):
    in_nike = False
    current_section = ""

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        ll = _norm(stripped)

        # Detekcia sekcie Niké liga
        if "nik" in ll and "liga" in ll:
            in_nike = True
            current_section = stripped
            if debug:
                print(f"  [SEKCIA] {stripped}")
            continue

        # Koniec Niké liga sekcie
        if in_nike and any(kw in ll for kw in OTHER_LEAGUES):
            in_nike = False
            if debug:
                print(f"  [KONIEC NIKÉ] {stripped}")
            continue

        if not in_nike:
            continue

        entry = _parse_match_line(stripped)
        if entry:
            entry["section"] = current_section
            results.append(entry)
            if debug:
                print(f"  [ZÁPAS] {entry['home_short']} - {entry['away_short']}: {entry['referee']}")
        elif debug:
            print(f"  [SKIP]  {stripped}")


def _parse_match_line(line: str) -> dict | None:
    """
    Vzory:
      "9.5.  o 18,00   Michalovce-D.Streda    Choreň,Zemko,Halíček,..."
      "o 20,30   Podbrezová-Slovan   Benedik,Bobko D.,..."
      "               Ružomberok-Trenčín      Dzivjak,Hancko,..."   ← bez času!
    """
    # Odstráň voliteľný dátum na začiatku
    s = re.sub(r"^\d{1,2}\.\s*\d{1,2}\.\s*", "", line).strip()

    # Skús najprv so časom "o HH,MM"
    m = re.match(r"^o\s+\d{1,2}[,\.]\d{2}\s+(.*)", s)
    if m:
        rest = m.group(1).strip()
    else:
        # Riadok bez času — platný len ak vyzerá ako "Tím-Tím Rozhodca,..."
        # Musí obsahovať "-" a aspoň jednu čiarku a začínať veľkým písmenom
        if not s or not s[0].isupper() or "-" not in s or "," not in s:
            return None
        # Nesmie to byť nadpis sekcie (obsahuje "kolo", "liga", "cup" atď.)
        sl = s.lower()
        if any(kw in sl for kw in ("liga", "kolo", "cup", "pohár", "nadstavb")):
            return None
        rest = s
    # rest = "HomeTeam-AwayTeam  FirstOfficial,SecondOfficial,..."

    # Nájdi prvú čiarku — oddeľuje tímovú časť od zoznamu oficíalov
    comma_idx = rest.find(",")
    if comma_idx == -1:
        return None

    before_comma = rest[:comma_idx]          # "Michalovce-D.Streda   Choreň"
    space_idx = before_comma.rfind(" ")
    if space_idx == -1:
        return None

    teams_str = before_comma[:space_idx].strip()   # "Michalovce-D.Streda"
    referee = before_comma[space_idx:].strip()      # "Choreň"

    # Rozdeľ tímy — prvý výskyt "-" oddeľuje domáceho od hosťa
    dash_idx = teams_str.find("-")
    if dash_idx == -1:
        return None

    home_short = teams_str[:dash_idx].strip()
    away_short = teams_str[dash_idx + 1:].strip()

    if not home_short or not away_short or not referee:
        return None

    return {
        "home_short": home_short,
        "away_short": away_short,
        "referee": referee,
    }


# ---------------------------------------------------------------------------
# PÁROAVANIE A AKTUALIZÁCIA JSON SÚBOROV
# ---------------------------------------------------------------------------

def patch_referees(data_dir: Path, dry_run: bool = False) -> tuple[int, int]:
    """
    Stiahne najnovší PDF a aktualizuje rozhodcov v JSON súboroch pre nadchádzajúce zápasy.
    Vráti (aktualizovaných, nenájdených).
    """
    data_dir = Path(data_dir)
    json_dir = data_dir / "json"
    index_path = data_dir / "matches_index.json"

    if not index_path.exists():
        print("Chyba: matches_index.json neexistuje.")
        return 0, 0

    # --- Stiahni PDF ---
    print("Hľadám najnovší PDF obsadenia na futbalsfz.sk ...")
    pdf_url = fetch_latest_pdf_url()
    if not pdf_url:
        print("  Chyba: PDF nenájdené. Skontroluj https://futbalsfz.sk/sezona-*/")
        return 0, 0
    print(f"  {pdf_url}")

    resp = SESSION.get(pdf_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    # --- Parsuj PDF ---
    print("Parsujeme PDF ...")
    pdf_matches = parse_pdf_referees(resp.content, debug=False)
    if not pdf_matches:
        print("  Žiadne zápasy Niké ligy v PDF. Skontroluj formát.")
        return 0, 0

    print(f"  Nájdených {len(pdf_matches)} zápasov:")
    for pm in pdf_matches:
        print(f"    {pm['home_short']} - {pm['away_short']}: {pm['referee']}")

    # --- Načítaj nadchádzajúce JSON súbory ---
    with open(index_path, encoding="utf-8") as f:
        matches = json.load(f)

    upcoming = []
    for m in matches:
        jp = json_dir / f"{m['id']}.json"
        if not jp.exists():
            continue
        with open(jp, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("meta", {}).get("home_score") is None:
            upcoming.append({
                "id": m["id"],
                "home": d["meta"].get("home_team", ""),
                "away": d["meta"].get("away_team", ""),
                "path": jp,
                "data": d,
            })

    all_teams = list({u["home"] for u in upcoming} | {u["away"] for u in upcoming})

    # --- Páruj a aktualizuj ---
    updated = 0
    not_found = 0

    print(f"\n  Nadchádzajúce zápasy v JSON ({len(upcoming)}):")
    for u in upcoming:
        print(f"    {u['home']} vs {u['away']}  (id={u['id']})")

    for pm in pdf_matches:
        # Nájdi plné mená tímov
        home_full = next((t for t in all_teams if _team_match(pm["home_short"], t)), None)
        away_full = next((t for t in all_teams if _team_match(pm["away_short"], t)), None)

        if not home_full or not away_full:
            print(f"  NENÁJDENÝ TÍM: '{pm['home_short']}'→{home_full}  '{pm['away_short']}'→{away_full}")
            not_found += 1
            continue

        entry = next(
            (u for u in upcoming
             if _norm(u["home"]) == _norm(home_full)
             and _norm(u["away"]) == _norm(away_full)),
            None,
        )

        if not entry:
            print(f"  NENÁJDENÝ ZÁPAS: {home_full} vs {away_full} nie je v upcoming JSON")
            not_found += 1
            continue

        old_ref = entry["data"]["meta"].get("referee", "")
        new_ref = pm["referee"]

        if old_ref == new_ref:
            continue

        print(f"  {home_full} vs {away_full}: '{old_ref}' → '{new_ref}'")

        if not dry_run:
            entry["data"]["meta"]["referee"] = new_ref
            with open(entry["path"], "w", encoding="utf-8") as f:
                json.dump(entry["data"], f, ensure_ascii=False, indent=2)

        updated += 1

    return updated, not_found


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Aktualizuj rozhodcov z PDF obsadenia SFZ (futbalsfz.sk)"
    )
    parser.add_argument("data_dir", help="Cesta k data/ adresáru (napr. ./data)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Len zobraz čo by sa zmenilo, nemeň súbory"
    )
    parser.add_argument(
        "--pdf-url",
        help="Použi konkrétnu URL PDF namiesto automatického hľadania"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Vypíš všetky riadky z PDF vrátane preskočených"
    )
    args = parser.parse_args()

    if args.pdf_url:
        resp = SESSION.get(args.pdf_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        pdf_matches = parse_pdf_referees(resp.content, debug=args.debug)
        print(f"\nNájdených {len(pdf_matches)} zápasov Niké ligy v PDF:")
        for pm in pdf_matches:
            print(f"  {pm['home_short']} - {pm['away_short']}: {pm['referee']}")
        return

    updated, not_found = patch_referees(Path(args.data_dir), dry_run=args.dry_run)

    if args.dry_run:
        print(f"\nDRY RUN — aktualizovalo by sa: {updated}, nenájdených: {not_found}")
    else:
        print(f"\nHotovo. Aktualizovaných: {updated}, nenájdených: {not_found}")


if __name__ == "__main__":
    main()
