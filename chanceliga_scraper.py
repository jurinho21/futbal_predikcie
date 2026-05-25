"""
chanceliga_scraper.py — parser jedného zápasu z chanceliga.cz
Použitie:
    python chanceliga_scraper.py https://www.chanceliga.cz/zapas/1234-slovan-sparta
"""

import sys
import json
import re
import logging
import requests
from bs4 import BeautifulSoup
from http_utils import SESSION

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# Czech stat names → canonical keys
STAT_MAP = {
    "xG":                    "xg",
    "Držení míče":           "possession",
    "Střely/na branku":      "shots_combo",        # "7/2" → shots + shots_on_target
    "Přihrávky/úspěšnost":  "passes_combo",        # "185/68,8%" → passes + pass_accuracy
    "Střely celkem":         "shots",
    "Střely na branku":      "shots_on_target",
    "Střely mimo branku":    "shots_off_target",
    "Rohy":                  "corners",
    "Ofsajdy":               "offsides",
    "Žluté karty":           "yellow_cards",
    "Červené karty":         "red_cards",
    "Fauly":                 "fouls",
    "Zákroky brankáře":      "saves",
    "Přihrávky":             "passes",
    "Přesnost přihrávek":    "pass_accuracy",
    "Vyhrané souboje":       "duels_won",
}


def fetch_html(url: str) -> str:
    resp = SESSION.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def parse_match(html: str, url: str = "") -> dict:
    soup = BeautifulSoup(html, "html.parser")
    events = _parse_events(soup)
    stats = _parse_stats(soup, events)
    return {
        "url": url,
        "meta": _parse_meta(soup),
        "events": events,
        "stats": stats,
        "lineups": _parse_lineups(soup),
    }


def _parse_meta(soup: BeautifulSoup) -> dict:
    meta = {}

    # Tímy — prvý a druhý link v .game__scorebox__team-name.hidden-xs
    team_els = soup.select(".game__scorebox__team .game__scorebox__team-name.hidden-xs a")
    if len(team_els) >= 2:
        meta["home_team"] = team_els[0].get_text(strip=True)
        meta["away_team"] = team_els[1].get_text(strip=True)

    # Skóre
    score_el = soup.select_one(".game__scorebox__score")
    if score_el:
        m = re.search(r"(\d+)\s*:\s*(\d+)", score_el.get_text())
        if m:
            meta["home_score"] = int(m.group(1))
            meta["away_score"] = int(m.group(2))

    # Polčas  "(1:0)"
    ht_el = soup.select_one(".game__scorebox__score-halftime")
    if ht_el:
        m = re.search(r"(\d+)\s*:\s*(\d+)", ht_el.get_text())
        if m:
            meta["home_score_ht"] = int(m.group(1))
            meta["away_score_ht"] = int(m.group(2))

    # Dátum a kolo z .game__header
    # Text: "1. kolo, Skupina o titul | sobota 02/05/2026, 18:00 | Stadión U Nisy |"
    header_el = soup.select_one(".game__header")
    if header_el:
        full_text = header_el.get_text(" ", strip=True)
        meta["round"] = full_text
        date_m = re.search(r"(\d{2}/\d{2}/\d{4}),?\s*(\d{2}:\d{2})", full_text)
        if date_m:
            meta["date"] = f"{date_m.group(1)} {date_m.group(2)}"
        # Štadión — text za druhým |
        parts = full_text.split("|")
        if len(parts) >= 3:
            stadium = parts[2].strip()
            if stadium:
                meta["stadium"] = stadium

    # Rozhodca
    ref_section = soup.select_one(".game__info-referees")
    if ref_section:
        ref_link = ref_section.select_one("a[href*='/rozhodci/']")
        if ref_link:
            meta["referee"] = ref_link.get_text(strip=True)

    # Návštevnosť — rôzne možné selectormi
    for sel in (".game__info-attendance", ".game__additional", ".game__info"):
        att_el = soup.select_one(sel)
        if att_el:
            text = att_el.get_text(" ", strip=True)
            if "divák" in text.lower() or "návštěva" in text.lower():
                nums = re.findall(r"\d+", text.replace("\xa0", "").replace(" ", ""))
                if nums:
                    meta["attendance"] = int(nums[0])
                    break

    return meta


def _parse_combo(val: str) -> tuple:
    """Parses '7/2' → (7, 2); '7' → (7, None)."""
    parts = val.split("/")
    if len(parts) == 2:
        return (_to_num(parts[0].strip()), _to_num(parts[1].strip()))
    return (_to_num(val), None)


def _parse_stats(soup: BeautifulSoup, events: list) -> dict:
    stats = {}

    # Všetky štatistiky sú priamo v .game__stats (bez #stage_3 ako na nikeliga.sk)
    stats_el = soup.select_one(".game__stats")
    if stats_el:
        period_stats = {}
        for container in stats_el.select(".stats-container"):
            name_el = container.select_one(".stats-name")
            home_el = container.select_one(".stats.right .value")
            away_el = container.select_one(".stats:not(.right) .value")
            if not (name_el and home_el and away_el):
                continue
            raw_name = name_el.get_text(strip=True)
            key = STAT_MAP.get(raw_name, re.sub(r"\s+", "_", raw_name.lower()))
            home_raw = home_el.get_text(strip=True)
            away_raw = away_el.get_text(strip=True)

            if key == "shots_combo":
                # "7/2" → shots=7, shots_on_target=2
                home_s, home_sot = _parse_combo(home_raw)
                away_s, away_sot = _parse_combo(away_raw)
                period_stats["shots"] = {"home": home_s, "away": away_s}
                if home_sot is not None:
                    period_stats["shots_on_target"] = {"home": home_sot, "away": away_sot}
            elif key == "passes_combo":
                # "185/68,8%" → passes=185, pass_accuracy=68.8
                home_p, home_pa = _parse_combo(home_raw)
                away_p, away_pa = _parse_combo(away_raw)
                period_stats["passes"] = {"home": home_p, "away": away_p}
                if home_pa is not None:
                    period_stats["pass_accuracy"] = {"home": home_pa, "away": away_pa}
            else:
                period_stats[key] = {
                    "home": _to_num(home_raw),
                    "away": _to_num(away_raw),
                }
        if period_stats:
            stats["total"] = period_stats

    # Žlté/červené karty z eventov — na chanceliga.cz nie sú v tabuľke štatistík
    if events:
        target = stats.setdefault("total", {})
        if "yellow_cards" not in target:
            hy = sum(1 for e in events if e["type"] == "yellow_card" and e["team"] == "home")
            ay = sum(1 for e in events if e["type"] == "yellow_card" and e["team"] == "away")
            if hy or ay:
                target["yellow_cards"] = {"home": hy, "away": ay}
        if "red_cards" not in target:
            hr = sum(1 for e in events if e["type"] == "red_card" and e["team"] == "home")
            ar = sum(1 for e in events if e["type"] == "red_card" and e["team"] == "away")
            if hr or ar:
                target["red_cards"] = {"home": hr, "away": ar}
        if not target:
            del stats["total"]

    return stats


def _parse_events(soup: BeautifulSoup) -> list:
    events = []

    # Góly — každý .items-list.goal-modal je jedna strana (prvý=domáci, druhý=hostia)
    # Minúta a meno sú v <h4 class="modal-title">50' Ermin Mahmić</h4>
    goal_cols = soup.select(".items-list.goal-modal")
    for i, col in enumerate(goal_cols[:2]):
        side = "home" if i == 0 else "away"
        for title_el in col.select("h4.modal-title"):
            text = title_el.get_text(strip=True)  # "50' Ermin Mahmić"
            minute_m = re.search(r"(\d+)", text)
            name = re.sub(r"^\d+\+?\d*'\s*", "", text).strip()
            own_goal = bool(re.search(r"\bvlastní\b|\bautogól\b", text, re.IGNORECASE))
            events.append({
                "type": "own_goal" if own_goal else "goal",
                "team": side,
                "player": name,
                "minute": int(minute_m.group(1)) if minute_m else None,
            })

    # Karty — .items-list bez .goal-modal (prvý=domáci, druhý=hostia)
    # <li><i class="ico ico-card yellow"></i><a href="...">34' Ermin Mahmić</a></li>
    # Typy: yellow, red-after-yellow (druhá žltá → červená), red (priama červená)
    card_cols = [el for el in soup.select(".items-list") if "goal-modal" not in el.get("class", [])]
    for i, col in enumerate(card_cols[:2]):
        side = "home" if i == 0 else "away"
        for item in col.select("li"):
            icon = item.select_one("i.ico-card")
            if not icon:
                continue
            cls = " ".join(icon.get("class", []))
            card_type = "red_card" if "red" in cls else "yellow_card"
            link = item.select_one("a")
            if not link:
                continue
            text = link.get_text(strip=True)  # "34' Ermin Mahmić"
            minute_m = re.search(r"(\d+)", text)
            name = re.sub(r"^\d+\+?\d*'\s*", "", text).strip()
            events.append({
                "type": card_type,
                "team": side,
                "player": name,
                "minute": int(minute_m.group(1)) if minute_m else None,
            })

    return events


def _parse_lineups(soup: BeautifulSoup) -> dict:
    lineups = {"home": [], "away": []}
    roster = soup.select_one(".game__roster")
    if not roster:
        return lineups

    cols = roster.select(".col-xs-12.col-sm-6")
    for i, col in enumerate(cols[:2]):
        side = "home" if i == 0 else "away"
        table = col.select_one("table")
        if not table:
            continue

        starter = True
        for row in table.select("tr"):
            cells = row.select("td")
            if len(cells) == 1 and cells[0].get("colspan"):
                starter = False
                continue
            if len(cells) < 2:
                continue

            name_el = cells[1].select_one("a") if len(cells) > 1 else None
            name = name_el.get_text(strip=True) if name_el else cells[1].get_text(strip=True)
            number = _to_num(cells[0].get_text(strip=True))
            position = cells[2].get_text(strip=True) if len(cells) > 2 else ""

            yellow = sum(1 for td in cells for _ in td.select("i.ico-card.yellow"))
            red    = sum(1 for td in cells for _ in td.select("i.ico-card.red"))
            goals  = sum(1 for td in cells for _ in td.select(".ico-ball"))

            lineups[side].append({
                "name": name,
                "number": number,
                "position": position,
                "starter": starter,
                "goals": goals,
                "yellow_cards": yellow,
                "red_cards": red,
            })

    return lineups


def _to_num(s):
    if s is None:
        return None
    s = str(s).strip().rstrip("%").replace(",", ".")
    if not s:
        return None
    try:
        v = float(s)
        return int(v) if v == int(v) else v
    except (ValueError, OverflowError):
        return s


def main():
    if len(sys.argv) < 2:
        print("Použitie: python chanceliga_scraper.py <url_zapasu>")
        sys.exit(1)
    logging.basicConfig(level=logging.INFO)
    url = sys.argv[1]
    html = fetch_html(url)
    data = parse_match(html, url)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
