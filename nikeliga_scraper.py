"""
nikeliga_scraper.py — parser jedného zápasu z nikeliga.sk
Použitie:
    python nikeliga_scraper.py https://www.nikeliga.sk/zapas/2614-kfc-tre
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

STAT_MAP = {
    "Očakávané góly": "xg",
    "Držanie lopty v %": "possession",
    "Strely": "shots",
    "Strely na bránu": "shots_on_target",
    "Presnosť streľby v %": "shot_accuracy",
    "Strely mimo bránku": "shots_off_target",
    "Zblokované strely": "shots_blocked",
    "Rohové kopy": "corners",
    "Ofsajdy": "offsides",
    "Žlté karty": "yellow_cards",
    "Červené karty": "red_cards",
    "Prihrávky": "passes",
    "Presnosť prihrávok v %": "pass_accuracy",
    "Vyhraté súboje": "duels_won",
    "Fauly": "fouls",
    "Zákroky brankára": "saves",
}


def fetch_html(url: str) -> str:
    resp = SESSION.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def parse_match(html: str, url: str = "") -> dict:
    soup = BeautifulSoup(html, "html.parser")
    return {
        "url": url,
        "meta": _parse_meta(soup),
        "events": _parse_events(soup),
        "stats": _parse_stats(soup),
        "lineups": _parse_lineups(soup),
    }


def _parse_meta(soup: BeautifulSoup) -> dict:
    meta = {}

    # Tímy
    home_el = soup.select_one(".game__scoreboard__team--home .game__scoreboard__name span.hidden-xs")
    away_el = soup.select_one(".game__scoreboard__team--away .game__scoreboard__name span.hidden-xs")
    if home_el:
        meta["home_team"] = home_el.get_text(strip=True)
    if away_el:
        meta["away_team"] = away_el.get_text(strip=True)

    # Skóre
    score_el = soup.select_one(".game__scoreboard__fulltime")
    if score_el:
        m = re.search(r"(\d+)\s*:\s*(\d+)", score_el.get_text())
        if m:
            meta["home_score"] = int(m.group(1))
            meta["away_score"] = int(m.group(2))

    # Polčas
    ht_el = soup.select_one(".game__scoreboard__halftime")
    if ht_el:
        m = re.search(r"(\d+)\s*:\s*(\d+)", ht_el.get_text())
        if m:
            meta["home_score_ht"] = int(m.group(1))
            meta["away_score_ht"] = int(m.group(2))

    # Dátum a štadión
    date_el = soup.select_one(".game__scoreboard__date.hidden-xs")
    if date_el:
        meta["date"] = date_el.get_text(strip=True)

    stadium_el = soup.select_one(".game__scoreboard__stadium")
    if stadium_el:
        meta["stadium"] = stadium_el.get_text(strip=True)

    # Kolo — prvý .box-header element
    round_el = soup.select_one(".container-flow.box-header")
    if round_el:
        meta["round"] = round_el.get_text(strip=True)

    # Návštevnosť a rozhodca
    additional = soup.select_one(".game__additional")
    if additional:
        for div in additional.select("div"):
            text = div.get_text(strip=True)
            if "divákov" in text:
                nums = re.findall(r"\d+", text.replace("\xa0", "").replace(" ", ""))
                if nums:
                    meta["attendance"] = int(nums[0])
            elif "Rozhodcovia" in text or "Rozhodca" in text:
                full = re.sub(r"Rozhodcovia?:\s*", "", text).strip()
                meta["referee"] = full.split(",")[0].strip()

    return meta


def _parse_events(soup: BeautifulSoup) -> list:
    events = []

    # Góly
    goals_section = soup.select_one(".game__goals")
    if goals_section:
        cols = goals_section.select(".col-xs-12.col-sm-6")
        for i, col in enumerate(cols):
            side = "home" if i == 0 else "away"
            for item in col.select(".game__goals__item"):
                text = item.get_text(" ", strip=True)
                minute_m = re.search(r"\((\d+)[+'”\"]?\)", text)
                player_el = item.select_one("a")
                own_goal = "vl" in text.lower() or "(vl)" in text.lower()
                events.append({
                    "type": "own_goal" if own_goal else "goal",
                    "team": side,
                    "player": player_el.get_text(" ", strip=True) if player_el else "",
                    "minute": int(minute_m.group(1)) if minute_m else None,
                })

    # Karty
    cards_section = soup.select_one(".game__cards")
    if cards_section:
        cols = cards_section.select(".col-xs-12.col-sm-6")
        for i, col in enumerate(cols):
            side = "home" if i == 0 else "away"
            for item in col.select(".game__cards__item"):
                text = item.get_text(" ", strip=True)
                minute_m = re.search(r"\((\d+)[^)]*\)", text)
                player_el = item.select_one("a")
                icon = item.select_one(".ico-card")
                card_type = "yellow_card"
                if icon:
                    cls = " ".join(icon.get("class", []))
                    if "red" in cls:
                        card_type = "red_card"
                events.append({
                    "type": card_type,
                    "team": side,
                    "player": player_el.get_text(" ", strip=True) if player_el else "",
                    "minute": int(minute_m.group(1)) if minute_m else None,
                })

    return events


def _parse_stats(soup: BeautifulSoup) -> dict:
    stats = {}
    period_map = {
        "stage_3": "total",
        "stage_1": "first_half",
        "stage_2": "second_half",
    }

    for stage_id, period_key in period_map.items():
        tab = soup.select_one(f"#{stage_id} .hidden-xs")
        if not tab:
            continue
        period_stats = {}
        for container in tab.select(".stats-container"):
            name_el = container.select_one(".stats-name")
            home_el = container.select_one(".stats.right .value")
            away_el = container.select_one(".stats:not(.right) .value")
            if not (name_el and home_el and away_el):
                continue
            raw_name = name_el.get_text(strip=True)
            key = STAT_MAP.get(raw_name, re.sub(r"\s+", "_", raw_name.lower()))
            period_stats[key] = {
                "home": _to_num(home_el.get_text(strip=True)),
                "away": _to_num(away_el.get_text(strip=True)),
            }
        if period_stats:
            stats[period_key] = period_stats

    return stats


def _parse_lineups(soup: BeautifulSoup) -> dict:
    lineups = {"home": [], "away": []}
    roster = soup.select_one(".game__roster")
    if not roster:
        return lineups

    cols = roster.select(".col-xs-12.col-sm-6")
    sides = ["home", "away"]

    for i, col in enumerate(cols[:2]):
        side = sides[i]
        table = col.select_one("table")
        if not table:
            continue

        starter = True
        for row in table.select("tr"):
            cells = row.select("td")
            # separator medzi základnou a náhradníkmi
            if len(cells) == 1 and cells[0].get("colspan"):
                starter = False
                continue
            if len(cells) < 4:
                continue

            sub_td = cells[0]
            sub_text = sub_td.get_text(strip=True)
            sub_minute = None
            sub_type = None
            if sub_text:
                sub_m = re.search(r"(\d+)", sub_text)
                if sub_m:
                    sub_minute = int(sub_m.group(1))
                if "sub-out" in " ".join(sub_td.select_one("i").get("class", []) if sub_td.select_one("i") else []):
                    sub_type = "out"
                elif "sub-in" in " ".join(sub_td.select_one("i").get("class", []) if sub_td.select_one("i") else []):
                    sub_type = "in"

            number = _to_num(cells[1].get_text(strip=True))
            position = cells[2].get_text(strip=True)
            name_el = cells[3].select_one("a")
            name = name_el.get_text(strip=True) if name_el else cells[3].get_text(strip=True)

            goals = 1 if cells[4].select_one(".ico-ball") else 0
            assists = 1 if cells[5].select_one(".ico-assist") else 0
            yellow = 1 if cells[6].select_one(".ico-card.yellow") else 0
            red = 1 if cells[7].select_one(".ico-card.red") else 0
            shots = _to_num(cells[8].get_text(strip=True)) if len(cells) > 8 else None
            passes = _to_num(cells[9].get_text(strip=True)) if len(cells) > 9 else None
            fouls = _to_num(cells[10].get_text(strip=True)) if len(cells) > 10 else None

            lineups[side].append({
                "name": name,
                "number": number,
                "position": position,
                "starter": starter,
                "sub_minute": sub_minute,
                "sub_type": sub_type,
                "goals": goals,
                "assists": assists,
                "yellow_cards": yellow,
                "red_cards": red,
                "shots": shots,
                "passes": passes,
                "fouls": fouls,
            })

    return lineups


def _to_num(s: str):
    s = str(s).strip().replace(",", ".")
    try:
        v = float(s)
        return int(v) if v == int(v) else v
    except (ValueError, OverflowError):
        return s if s else None


def main():
    if len(sys.argv) < 2:
        print("Použitie: python nikeliga_scraper.py <url_zapasu>")
        sys.exit(1)
    url = sys.argv[1]
    html = fetch_html(url)
    data = parse_match(html, url)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
