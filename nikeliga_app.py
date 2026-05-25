"""
nikeliga_app.py — Streamlit app pre predikciu bočných trhov Niké ligy a Chance ligy
Spustenie: streamlit run nikeliga_app.py
"""

import json
import math
import os
import logging
from pathlib import Path

import streamlit as st
import pandas as pd

from nikeliga_model import load_data, predict_match, fair_odds, winner_probs
from nikeliga_tips import save_tips, settle_tips, load_tips, tips_summary
from config import (
    DECAY, MARKETS, MARKET_LABELS, MARKET_LINES, TEAM_LINES,
    X1X2_KEYS, X1X2_LABELS, TEAM_STAT_LABELS, EDGE_GREEN, EDGE_YELLOW,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

_LEAGUES = {
    "Niké liga 🇸🇰":          "data/nikeliga",
    "Chance liga 🇨🇿":        "data/chanceliga",
    "Eredivisie 🇳🇱":         "data/eredivisie",
    "Ligue 1 🇫🇷":            "data/ligue1",
    "Jupiler Pro League 🇧🇪": "data/proleague",
    "Premier League 🏴󠁧󠁢󠁥󠁮󠁧󠁿":   "data/premier_league",
    "Bundesliga 🇩🇪":          "data/bundesliga",
    "La Liga 🇪🇸":             "data/la_liga",
    "Serie A 🇮🇹":             "data/serie_a",
    "Primeira Liga 🇵🇹":       "data/primeira_liga",
}

_FOOTBALLDATA_DIRS = {"premier_league", "bundesliga", "la_liga", "serie_a", "primeira_liga"}


def _is_footballdata(data_dir_str: str) -> bool:
    return any(d in data_dir_str for d in _FOOTBALLDATA_DIRS)


# ---------------------------------------------------------------------------
# NAČÍTANIE DÁT
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def get_df(data_dir_str: str):
    return load_data(Path(data_dir_str))


_MODEL_MTIME = max(
    os.path.getmtime(Path(__file__).parent / "nikeliga_model.py"),
    os.path.getmtime(Path(__file__).parent / "config.py"),
    os.path.getmtime(Path(__file__)),
)

_MOT_OPTIONS = [0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2, 1.25, 1.3]


def show_value_bets(upcoming: list, all_predictions: dict, data_dir: "Path"):
    for _del_key in st.session_state.pop("_vbets_to_delete", []):
        st.session_state[_del_key] = 0.0

    vbets = _collect_value_bets(upcoming, all_predictions)

    with st.expander(
        f"📊 Value bety — {len(vbets)} nájdených" if vbets else "📊 Value bety — zadaj kurzy a klikni Obnoviť",
        expanded=bool(vbets),
    ):
        c1, c2 = st.columns([5, 1])
        with c1:
            st.caption("Označ tipy ktoré chceš uložiť do histórie, potom klikni 💾 Uložiť vybrané.")
        with c2:
            st.button("🔄 Obnoviť", key="refresh_vbets", use_container_width=True)

        if vbets:
            vdf = pd.DataFrame(vbets)
            display_df = pd.DataFrame({
                "💾": False,
                "🗑️": False,
                "Zápas": vdf["Zápas"],
                "Market": vdf["Market"],
                "Stávka": vdf["Stávka"],
                "Model": vdf["_p"].map(lambda p: f"{fair_odds(p):.2f}"),
                "Kurz": vdf["Kurz"].map(lambda k: f"{k:.2f}"),
                "Edge": vdf["_edge"].map(lambda e: f"{e:+.1%}"),
            })
            edited = st.data_editor(
                display_df,
                column_config={
                    "💾": st.column_config.CheckboxColumn("💾", width="small"),
                    "🗑️": st.column_config.CheckboxColumn("🗑️", width="small"),
                },
                disabled=["Zápas", "Market", "Stávka", "Model", "Kurz", "Edge"],
                hide_index=True, use_container_width=True, key="vbets_editor",
            )

            to_save = edited.index[edited["💾"]].tolist()
            to_delete = edited.index[edited["🗑️"]].tolist()

            col_s, col_d = st.columns(2)
            with col_s:
                if to_save:
                    if st.button(f"💾 Uložiť {len(to_save)} vybraných", key="save_vbets_btn", use_container_width=True):
                        tips_to_save = [{
                            "match_id": vbets[i]["_match_id"],
                            "home_team": vbets[i]["_home"],
                            "away_team": vbets[i]["_away"],
                            "match_date": vbets[i]["_match_date"],
                            "market": vbets[i]["_market"],
                            "bet_type": vbets[i]["_bet_type"],
                            "line": vbets[i]["_line"],
                            "direction": vbets[i]["_direction"],
                            "model_prob": vbets[i]["_p"],
                            "bm_odds": vbets[i]["Kurz"],
                            "edge": vbets[i]["_edge"],
                        } for i in to_save]
                        n_saved = save_tips(data_dir, tips_to_save)
                        if n_saved:
                            st.toast(f"Uložených {n_saved} nových tipov.", icon="💾")
                        else:
                            st.toast("Duplicity — tipy už existujú.", icon="ℹ️")
            with col_d:
                if to_delete:
                    if st.button(f"🗑️ Vymazať {len(to_delete)} označených", key="delete_vbets_btn", use_container_width=True):
                        st.session_state["_vbets_to_delete"] = [vbets[i]["_ss_key"] for i in to_delete]


@st.cache_data
def cached_predict(_df, home, away, referee, mot_home=1.0, mot_away=1.0,
                   model_v=_MODEL_MTIME, league: str = ""):
    """_df nie je hashovaný — league je v cache kľúči, aby sa oddelili obe ligy."""
    return predict_match(_df, home, away, referee or None, mot_home=mot_home, mot_away=mot_away)


@st.cache_data
def run_backtest(_df, min_matches: int = 30, league: str = "") -> pd.DataFrame:
    """Walk-forward backtest: pre každý historický zápas predikuje pomocou len predchádzajúcich dát."""
    rows = []
    n = len(_df)
    for i in range(min_matches, n):
        row = _df.iloc[i]
        home = str(row.get('home_team') or '')
        away = str(row.get('away_team') or '')
        ref = str(row.get('referee') or '')
        if not home or not away:
            continue
        try:
            pred = predict_match(_df, home, away, ref or None, before_idx=i)
        except Exception:
            continue
        for market, cols in MARKETS.items():
            actual = pd.to_numeric(row.get(cols['total_col']), errors='coerce')
            if pd.isna(actual):
                continue
            lam = pred[market]['lambda_total']
            ou = pred[market].get('over_under_blended') or pred[market]['over_under']
            for ou_key, p in ou.items():
                if not ou_key.startswith('O'):
                    continue
                line = float(ou_key[1:])
                if abs(line - lam) > 4:
                    continue
                rows.append({'type': 'ou', 'market': market, 'p_over': float(p), 'actual_over': int(actual > line)})
            x1x2_key = X1X2_KEYS.get(market)
            if x1x2_key:
                actual_home = pd.to_numeric(row.get(cols['home_col']), errors='coerce')
                actual_away = pd.to_numeric(row.get(cols['away_col']), errors='coerce')
                if not pd.isna(actual_home) and not pd.isna(actual_away):
                    actual_1x2 = '1' if actual_home > actual_away else ('X' if actual_home == actual_away else '2')
                    x1x2_data = pred[market].get(x1x2_key, {})
                    b = x1x2_data.get('blended') or x1x2_data
                    p1, px, p2 = b.get('1'), b.get('X'), b.get('2')
                    if p1 and px and p2:
                        rows.append({
                            'type': '1x2', 'market': market,
                            'p1': float(p1), 'pX': float(px), 'p2': float(p2),
                            'actual_1x2': actual_1x2,
                        })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _collect_value_bets(upcoming: list, all_predictions: dict) -> list[dict]:
    """Zbiera value bety (edge > 2 %) zo session_state + pre-computed predikcií."""
    rows = []
    for match in upcoming:
        home, away = match['home'], match['away']
        mk = f"{home}_{away}".replace(" ", "_")
        pred = all_predictions.get(mk)
        if not pred:
            continue
        label = f"{home} — {away}"
        base = {
            "_match_id": match.get("match_id", 0),
            "_home": home,
            "_away": away,
            "_match_date": match.get("date", ""),
        }
        for market, mlabel in MARKET_LABELS.items():
            mp = pred.get(market, {})
            for line in MARKET_LINES.get(market, []):
                for d in ("O", "U"):
                    ss_key = f"{mk}_{market}_{d}{line}"
                    bm = st.session_state.get(ss_key, 0.0)
                    if bm <= 1.01:
                        continue
                    p = (mp.get('over_under_blended') or mp.get('over_under', {})).get(f"{d}{line}")
                    if p and (edge := p * bm - 1) > 0.02:
                        rows.append({**base, "Zápas": label, "Market": mlabel,
                                     "Stávka": f"Totál {d}{line}", "_p": p, "Kurz": bm, "_edge": edge,
                                     "_market": market, "_bet_type": "total", "_line": line, "_direction": d,
                                     "_ss_key": ss_key})
            for side, ou_key in [("home", "over_under_home_blended"), ("away", "over_under_away_blended")]:
                team = home if side == "home" else away
                for line in TEAM_LINES.get(market, []):
                    for d in ("O", "U"):
                        ss_key = f"{mk}_{market}_{side}_{d}{line}"
                        bm = st.session_state.get(ss_key, 0.0)
                        if bm <= 1.01:
                            continue
                        p = mp.get(ou_key, {}).get(f"{d}{line}")
                        if p and (edge := p * bm - 1) > 0.02:
                            rows.append({**base, "Zápas": label, "Market": mlabel,
                                         "Stávka": f"{team} {d}{line}", "_p": p, "Kurz": bm, "_edge": edge,
                                         "_market": market, "_bet_type": side, "_line": line, "_direction": d,
                                         "_ss_key": ss_key})
            x1x2_key = X1X2_KEYS.get(market)
            x1x2_data = mp.get(x1x2_key) or {}
            for outcome, outlabel in [("1", "dom."), ("X", "rem."), ("2", "host.")]:
                ss_key = f"{mk}_{x1x2_key}_{outcome}"
                bm = st.session_state.get(ss_key, 0.0)
                if bm <= 1.01:
                    continue
                p = x1x2_data.get(outcome)
                if p and (edge := p * bm - 1) > 0.02:
                    rows.append({**base, "Zápas": label, "Market": mlabel,
                                 "Stávka": f"1X2 {outlabel}", "_p": p, "Kurz": bm, "_edge": edge,
                                 "_market": market, "_bet_type": "1x2", "_line": 0.0, "_direction": outcome,
                                 "_ss_key": ss_key})
    return sorted(rows, key=lambda r: -r["_edge"])


@st.fragment
def render_match(match: dict, df, n_lines: int, league_name: str, needs_ref_select: bool):
    """Každý zápas je samostatný fragment — interakcia v ňom nespúšťa rerender ostatných."""
    home = match["home"]
    away = match["away"]
    date = match["date"]
    orig_referee = match.get("referee", "")
    match_key = f"{home}_{away}".replace(" ", "_")

    mot_h = float(st.session_state.get(f"{match_key}_mot_home", 1.0))
    mot_a = float(st.session_state.get(f"{match_key}_mot_away", 1.0))

    referee = orig_referee
    if not referee and needs_ref_select:
        referee = st.session_state.get(f"{match_key}_referee_override", "")

    try:
        pred = cached_predict(df, home, away, referee or None, mot_h, mot_a, league=league_name)
    except Exception:
        pred = None

    ref_note = f" | Rozhodca: {referee}" if referee else " | Rozhodca: neznámy"
    with st.expander(f"**{home} vs {away}**   {date}{ref_note}", expanded=False):
        if not pred:
            st.error("Chyba predikcie")
            return

        if needs_ref_select and not orig_referee:
            _referees = sorted(r for r in df["referee"].dropna().unique() if r)
            st.selectbox(
                "Rozhodca (neznámy — vyber zo zoznamu):",
                [""] + _referees,
                key=f"{match_key}_referee_override",
            )

        mc1, mc2 = st.columns(2)
        with mc1:
            st.select_slider(
                f"Motivácia — {home}", options=_MOT_OPTIONS, value=mot_h,
                key=f"{match_key}_mot_home",
            )
        with mc2:
            st.select_slider(
                f"Motivácia — {away}", options=_MOT_OPTIONS, value=mot_a,
                key=f"{match_key}_mot_away",
            )
        tabs = st.tabs([MARKET_LABELS[m] for m in MARKET_LABELS])
        for tab, (market, label) in zip(tabs, MARKET_LABELS.items()):
            with tab:
                show_market(match_key, market, label, pred[market], n_lines, home, away, df)


def _show_backtest(bt_df: pd.DataFrame):
    has_type = 'type' in bt_df.columns
    ou_df = bt_df[bt_df['type'] == 'ou'] if has_type else bt_df
    x1x2_df = bt_df[bt_df['type'] == '1x2'] if has_type else pd.DataFrame()
    st.caption(f"Walk-forward backtest — {len(ou_df)} O/U predikcií · {len(x1x2_df)} 1X2 predikcií na historických zápasoch")
    bins = [0.0, 0.3, 0.45, 0.55, 0.7, 1.01]
    bin_labels = ["< 30 %", "30–45 %", "45–55 %", "55–70 %", "> 70 %"]
    st.markdown("#### Over/Under")
    for market, mlabel in MARKET_LABELS.items():
        mdf = ou_df[ou_df['market'] == market].copy()
        if len(mdf) < 15:
            continue
        brier = float(((mdf['p_over'] - mdf['actual_over']) ** 2).mean())
        acc = float(((mdf['p_over'] > 0.5) == mdf['actual_over']).mean())
        mdf['bucket'] = pd.cut(mdf['p_over'], bins=bins, labels=bin_labels)
        calib = (mdf.groupby('bucket', observed=True)
                 .agg(**{"Model P": ('p_over', 'mean'), "Skutočná P": ('actual_over', 'mean'), "N": ('actual_over', 'count')})
                 .reset_index().rename(columns={'bucket': 'Bucket'}))
        st.markdown(f"**{mlabel}** — {len(mdf)} pred. · Brier score: `{brier:.4f}` · Presnosť (>50%): `{acc:.1%}`")
        c1, c2 = st.columns([3, 2])
        with c1:
            st.bar_chart(calib.set_index('Bucket')[['Model P', 'Skutočná P']])
        with c2:
            st.dataframe(calib.round(3), hide_index=True, use_container_width=True)
        st.divider()
    if x1x2_df.empty:
        return
    st.markdown("#### 1X2")
    for market, mlabel in MARKET_LABELS.items():
        mdf = x1x2_df[x1x2_df['market'] == market].copy()
        if len(mdf) < 15:
            continue
        mdf['predicted'] = mdf[['p1', 'pX', 'p2']].idxmax(axis=1).map({'p1': '1', 'pX': 'X', 'p2': '2'})
        acc = float((mdf['predicted'] == mdf['actual_1x2']).mean())
        mdf['brier'] = (
            (mdf['p1'] - (mdf['actual_1x2'] == '1').astype(float)) ** 2 +
            (mdf['pX'] - (mdf['actual_1x2'] == 'X').astype(float)) ** 2 +
            (mdf['p2'] - (mdf['actual_1x2'] == '2').astype(float)) ** 2
        ) / 3
        brier = float(mdf['brier'].mean())
        st.markdown(f"**{mlabel}** — {len(mdf)} pred. · Brier score: `{brier:.4f}` · Presnosť (top výber): `{acc:.1%}`")
        outcome_rows = []
        for outcome, col, label in [('1', 'p1', 'Domáci viac'), ('X', 'pX', 'Rovnako'), ('2', 'p2', 'Hostia viac')]:
            outcome_rows.append({
                'Výsledok': label,
                'Model P (avg)': round(float(mdf[col].mean()), 3),
                'Skutočná P': round(float((mdf['actual_1x2'] == outcome).mean()), 3),
                'N': int((mdf['actual_1x2'] == outcome).sum()),
            })
        c1, c2 = st.columns([2, 3])
        with c1:
            st.dataframe(pd.DataFrame(outcome_rows), hide_index=True, use_container_width=True)
        with c2:
            freq_df = pd.DataFrame(outcome_rows).set_index('Výsledok')[['Model P (avg)', 'Skutočná P']]
            st.bar_chart(freq_df)
        st.divider()


def _parse_match_date(date_str: str):
    """Parsuje dátum vo formátoch: Niké liga (DD.MM.YYYY), Chance liga (DD/MM/YYYY), Eredivisie (YYYY-MM-DD)."""
    import re
    from datetime import datetime
    # Chance liga: DD/MM/YYYY HH:MM
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})", date_str)
    if m:
        day, mon, year, hour, minute = (int(x) for x in m.groups())
        try:
            return datetime(year, mon, day, hour, minute)
        except ValueError:
            pass
    # Niké liga: DD.MM.YYYY, HH:MM (môže obsahovať deň v týždni pred dátumom)
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4}),?\s*(\d{2}):(\d{2})", date_str)
    if m:
        day, mon, year, hour, minute = (int(x) for x in m.groups())
        try:
            return datetime(year, mon, day, hour, minute)
        except ValueError:
            pass
    # Eredivisie: YYYY-MM-DD (bez času — použij koniec dňa ako kickoff)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", date_str.strip())
    if m:
        year, mon, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(year, mon, day, 23, 59)
        except ValueError:
            pass
    return None


@st.cache_data(ttl=300)
def get_upcoming(data_dir_str: str, days_ahead: int = 5) -> list[dict]:
    from datetime import datetime, timedelta

    data_dir = Path(data_dir_str)
    index_path = data_dir / "matches_index.json"
    json_dir = data_dir / "json"

    now = datetime.now()
    cutoff = now + timedelta(days=days_ahead)

    # Niké liga / Chance liga — štandardná cesta cez matches_index.json
    if index_path.exists():
        with open(index_path, encoding="utf-8") as f:
            matches = json.load(f)
        upcoming = []
        for m in matches:
            json_path = json_dir / f"{m['id']}.json"
            if not json_path.exists():
                continue
            with open(json_path, encoding="utf-8") as f:
                d = json.load(f)
            meta = d.get("meta", {})
            if meta.get("home_score") is not None:
                continue
            home = meta.get("home_team", "")
            away = meta.get("away_team", "")
            if not home or not away or "/" in home or "/" in away:
                continue
            date_str = meta.get("date", m.get("date", ""))
            match_date = _parse_match_date(date_str) if date_str else None
            if match_date is not None and not (now <= match_date <= cutoff):
                continue
            upcoming.append({
                "home": home,
                "away": away,
                "date": date_str,
                "referee": meta.get("referee", ""),
                "match_id": m["id"],
            })
        return upcoming

    # football-data.co.uk ligy — čítaj pre_match riadky priamo z matches.csv
    if _is_footballdata(data_dir_str):
        matches_csv = data_dir / "matches.csv"
        if not matches_csv.exists():
            return []
        import csv as _csv
        upcoming = []
        with open(matches_csv, encoding="utf-8") as _f:
            for row in _csv.DictReader(_f):
                if row.get("status", "").strip() != "pre_match":
                    continue
                home = row.get("home_team", "").strip()
                away = row.get("away_team", "").strip()
                if not home or not away:
                    continue
                date_str = row.get("date", "").strip()
                match_date = _parse_match_date(date_str) if date_str else None
                if match_date is not None and not (now <= match_date <= cutoff):
                    continue
                upcoming.append({
                    "home": home,
                    "away": away,
                    "date": date_str,
                    "referee": row.get("referee", "").strip(),
                    "match_id": row.get("match_id", ""),
                })
        return upcoming

    # Eredivisie / Pro League — JSON súbory priamo v data_dir (bez index súboru)
    # Optimalizácia: matches.csv obsahuje odohraté zápasy → preskočíme ich bez čítania JSON
    # Berieme len full_time záznamy — pre-match záznamy (napr. Ligue 1) treba stále čítať
    import csv as _csv2
    _played_ids: set = set()
    _mc = data_dir / "matches.csv"
    if _mc.exists():
        with open(_mc, encoding="utf-8") as _f2:
            for _row in _csv2.DictReader(_f2):
                _st = _row.get("status", "").strip().lower().replace("_", "").replace(" ", "")
                if _st in ("fulltime", "played"):
                    _played_ids.add(str(_row.get("match_id", "")))

    upcoming = []
    for json_path in sorted(data_dir.glob("*.json")):
        if json_path.stem in _played_ids:
            continue
        try:
            d = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (d.get("status") or "").lower().replace("_", "") in ("fulltime", "played"):
            continue
        home = d.get("home_team", "")
        away = d.get("away_team", "")
        if not home or not away or "/" in home or "/" in away:
            continue
        date_str = d.get("date", "")
        match_date = _parse_match_date(date_str) if date_str else None
        if match_date is not None and not (now <= match_date <= cutoff):
            continue
        upcoming.append({
            "home": home,
            "away": away,
            "date": date_str,
            "referee": d.get("referee", ""),
            "match_id": d.get("oid", json_path.stem),
        })
    return upcoming


# ---------------------------------------------------------------------------
# UI HELPERS
# ---------------------------------------------------------------------------

def edge_color(edge: float) -> str:
    if edge >= EDGE_GREEN:
        return "🟢"
    elif edge >= EDGE_YELLOW:
        return "🟡"
    else:
        return "🔴"


def _fill_counterpart(source_key: str, direction: str, line: float,
                      match_key: str, section_key: str):
    """Callback: z Over kurzu dopočíta Under pre tú istú hranicu a naopak."""
    ko = st.session_state.get(source_key, 0.0)
    margin = st.session_state.get("bm_margin", 0.08)
    other_dir = "U" if direction == "O" else "O"
    other_key = f"{match_key}_{section_key}_{other_dir}{line}"

    if not ko or ko <= 1.01:
        st.session_state[other_key] = 0.0
        return

    fair_p = (1.0 / ko) / (1.0 + margin)
    other_gross = (1.0 - fair_p) * (1.0 + margin)
    st.session_state[other_key] = round(1.0 / other_gross, 2) if other_gross > 0.01 else 0.0


def _show_ou_block(match_key: str, section_key: str, ou: dict, show_lines: list,
                   market: str, label_prefix: str, hit_rates: dict | None = None):
    """Zobrazí tabuľku fair kurzov + vstupy pre kurzy bookmakera a edge summary."""
    rows = []
    for line in show_lines:
        p_o = ou.get(f"O{line}")
        p_u = ou.get(f"U{line}")
        if p_o is None or p_u is None:
            continue
        row = {
            "Hranica": line,
            "Over": f"{fair_odds(p_o):.2f}  ({p_o:.1%})",
            "Under": f"{fair_odds(p_u):.2f}  ({p_u:.1%})",
        }
        if hit_rates and line in hit_rates:
            row.update(hit_rates[line])
        rows.append(row)

    col_table, col_input = st.columns([2, 2])

    with col_table:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    with col_input:
        st.markdown("**Kurz bookmakera** — zadaj jeden, druhý sa dopočíta:")
        for line in show_lines:
            p_o = ou.get(f"O{line}")
            p_u = ou.get(f"U{line}")
            if p_o is None or p_u is None:
                continue
            over_key = f"{match_key}_{section_key}_O{line}"
            under_key = f"{match_key}_{section_key}_U{line}"
            c1, c2 = st.columns(2)
            with c1:
                st.number_input(
                    f"O{line}",
                    min_value=0.0, max_value=50.0, value=0.0,
                    step=0.05, format="%.2f",
                    key=over_key,
                    on_change=_fill_counterpart,
                    args=(over_key, "O", line, match_key, section_key),
                )
            with c2:
                st.number_input(
                    f"U{line}",
                    min_value=0.0, max_value=50.0, value=0.0,
                    step=0.05, format="%.2f",
                    key=under_key,
                    on_change=_fill_counterpart,
                    args=(under_key, "U", line, match_key, section_key),
                )

    value_bets = []
    for line in show_lines:
        for direction in ("O", "U"):
            key = f"{direction}{line}"
            p = ou.get(key)
            bm = st.session_state.get(f"{match_key}_{section_key}_{key}", 0.0)
            if p and bm > 0:
                edge = p * bm - 1
                if edge > 0.02:
                    value_bets.append((key, p, bm, edge))

    if value_bets:
        st.success("**Hodnota nájdená:**")
        for key, p, bm, edge in sorted(value_bets, key=lambda x: -x[3]):
            st.markdown(f"- `{key}` — model {p:.1%} × kurz {bm:.2f} = **edge {edge:+.1%}** {edge_color(edge)}")


def _show_referee_stats(ref_info: dict, market: str):
    """Zobrazí súhrnný panel štatistík rozhodcu pod O/U tabuľkou."""
    n            = ref_info.get("n", 0)
    multiplier   = ref_info.get("multiplier", 1.0)
    avg_total    = ref_info.get("avg_total")
    avg_home     = ref_info.get("avg_home")
    avg_away     = ref_info.get("avg_away")
    league_total = ref_info.get("league_avg_total")
    league_home  = ref_info.get("league_avg_home")
    league_away  = ref_info.get("league_avg_away")

    stat_label = TEAM_STAT_LABELS.get(market, market)

    with st.expander(f"🟨 Rozhodca — {n} zápasov v dátach", expanded=False):
        c1, c2, c3 = st.columns(3)

        def _delta(ref_val, league_val):
            if ref_val is None or league_val is None:
                return None
            return round(ref_val - league_val, 1)

        with c1:
            delta = _delta(avg_total, league_total)
            delta_str = f"{delta:+.1f} vs liga" if delta is not None else None
            st.metric(
                f"Priem. {stat_label} (totál)",
                f"{avg_total:.1f}" if avg_total else "—",
                delta_str,
            )
            if league_total:
                st.caption(f"Ligový priemer: {league_total:.1f}")

        with c2:
            delta_h = _delta(avg_home, league_home)
            delta_str_h = f"{delta_h:+.1f} vs liga" if delta_h is not None else None
            st.metric(
                "Domáci tím",
                f"{avg_home:.1f}" if avg_home else "—",
                delta_str_h,
            )
            if league_home:
                st.caption(f"Ligový priemer: {league_home:.1f}")

        with c3:
            delta_a = _delta(avg_away, league_away)
            delta_str_a = f"{delta_a:+.1f} vs liga" if delta_a is not None else None
            st.metric(
                "Hosťujúci tím",
                f"{avg_away:.1f}" if avg_away else "—",
                delta_str_a,
            )
            if league_away:
                st.caption(f"Ligový priemer: {league_away:.1f}")

        mult_pct = (multiplier - 1.0) * 100
        direction = "viac" if mult_pct > 0 else "menej"
        st.caption(
            f"Vplyv na predikciu: `{multiplier:.2f}×` ({abs(mult_pct):.0f} % {direction} ako ligový priemer)"
            if abs(mult_pct) > 0.5 else
            f"Vplyv na predikciu: `{multiplier:.2f}×` (blízko ligového priemeru)"
        )


def _last5_totals(df: pd.DataFrame, team: str, side: str, h_col: str, a_col: str) -> str:
    """Posledných 5 hodnôt totálu (h+a) pre zápasy tímu: side = 'home'/'away'/'all'."""
    played = df[df["status"] == "full_time"] if "status" in df.columns else df
    if side == "home":
        rows = played[played["home_team"] == team].tail(5)
    elif side == "away":
        rows = played[played["away_team"] == team].tail(5)
    else:
        rows = played[(played["home_team"] == team) | (played["away_team"] == team)].tail(5)
    vals = (pd.to_numeric(rows[h_col], errors="coerce") + pd.to_numeric(rows[a_col], errors="coerce")).dropna().iloc[::-1]
    return ", ".join(str(int(v)) for v in vals) if not vals.empty else "—"


def _last5_team_vals(df: pd.DataFrame, team: str, side: str, h_col: str, a_col: str) -> str:
    """Posledných 5 hodnôt individuálnej štatistiky tímu: side = 'home'/'away'/'all'."""
    played = df[df["status"] == "full_time"] if "status" in df.columns else df
    if side == "home":
        rows = played[played["home_team"] == team].tail(5)
        vals = pd.to_numeric(rows[h_col], errors="coerce").dropna()
    elif side == "away":
        rows = played[played["away_team"] == team].tail(5)
        vals = pd.to_numeric(rows[a_col], errors="coerce").dropna()
    else:
        rows = played[(played["home_team"] == team) | (played["away_team"] == team)].tail(5)
        is_home = rows["home_team"] == team
        vals = pd.to_numeric(rows[h_col].where(is_home, rows[a_col]), errors="coerce").dropna()
    return ", ".join(str(int(v)) for v in vals.iloc[::-1]) if not vals.empty else "—"


def _last5_form(df: pd.DataFrame, team: str, side: str, h_col: str, a_col: str) -> str:
    """Posledných 5 výsledkov tímu: V (viac) / R (rovnako) / P (menej) — pre X1X2."""
    played = df[df["status"] == "full_time"] if "status" in df.columns else df
    if side == "home":
        rows = played[played["home_team"] == team].tail(5)
        tv = pd.to_numeric(rows[h_col], errors="coerce")
        ov = pd.to_numeric(rows[a_col], errors="coerce")
    elif side == "away":
        rows = played[played["away_team"] == team].tail(5)
        tv = pd.to_numeric(rows[a_col], errors="coerce")
        ov = pd.to_numeric(rows[h_col], errors="coerce")
    else:
        rows = played[(played["home_team"] == team) | (played["away_team"] == team)].tail(5)
        is_home = rows["home_team"] == team
        tv = pd.to_numeric(rows[h_col].where(is_home, rows[a_col]), errors="coerce")
        ov = pd.to_numeric(rows[a_col].where(is_home, rows[h_col]), errors="coerce")
    result = []
    for t, o in zip(tv.values, ov.values):
        if pd.isna(t) or pd.isna(o):
            continue
        result.append("V" if t > o else ("R" if t == o else "P"))
    return ", ".join(reversed(result)) if result else "—"


def show_market(match_key: str, market: str, label: str, pred_data: dict,
                n_lines: int, home_team: str = "", away_team: str = "", df=None):
    lam = pred_data["lambda_total"]
    ou = pred_data.get("over_under_blended") or pred_data["over_under"]

    all_lines = MARKET_LINES.get(market, [])
    sorted_lines = sorted(all_lines, key=lambda l: abs(l - lam))
    show_lines = sorted(sorted_lines[:n_lines])

    st.markdown(f"**{label}** — očakávaný total: `{lam}`")
    hr_ht = pred_data.get("hit_rates_home_total", {})
    hr_at = pred_data.get("hit_rates_away_total", {})
    hr_ha = pred_data.get("hit_rates_home_all", {})
    hr_aa = pred_data.get("hit_rates_away_all", {})
    hr_h2h = pred_data.get("hit_rates_h2h_total", {})
    ref_info = pred_data.get("ref_info", {}) if market in ("fouls", "yellow_cards") else {}
    ref_hr_total = ref_info.get("hit_rates_total", {})
    ht_total = {
        line: {
            "Dom": hr_ht.get(line, ""),
            "Host": hr_at.get(line, ""),
            "Dom cel.": hr_ha.get(line, ""),
            "Host cel.": hr_aa.get(line, ""),
            "H2H": hr_h2h.get(line, ""),
            **({"Rozh.": ref_hr_total.get(line, "")} if ref_hr_total else {}),
        }
        for line in show_lines
    }
    _show_ou_block(match_key, market, ou, show_lines, market, label, hit_rates=ht_total)

    if df is not None and home_team and away_team:
        _hc, _ac = MARKETS[market]["home_col"], MARKETS[market]["away_col"]
        fc1, fc2 = st.columns(2)
        with fc1:
            st.caption(f"Forma doma ({home_team}): {_last5_totals(df, home_team, 'home', _hc, _ac)}")
            st.caption(f"Forma celkovo ({home_team}): {_last5_totals(df, home_team, 'all', _hc, _ac)}")
        with fc2:
            st.caption(f"Forma vonku ({away_team}): {_last5_totals(df, away_team, 'away', _hc, _ac)}")
            st.caption(f"Forma celkovo ({away_team}): {_last5_totals(df, away_team, 'all', _hc, _ac)}")

    if ref_info and ref_info.get("n", 0) > 0:
        _show_referee_stats(ref_info, market)

    x1x2_key = X1X2_KEYS.get(market)
    x1x2_label = X1X2_LABELS.get(market, "")
    team_stat_label = TEAM_STAT_LABELS.get(market, "")

    if market in ("fouls", "shots_on_target", "corners", "yellow_cards") and "over_under_home" in pred_data:
        st.divider()
        lam_h = pred_data["lambda_home"]
        lam_a = pred_data["lambda_away"]
        ou_h = pred_data.get("over_under_home_blended") or pred_data["over_under_home"]
        ou_a = pred_data.get("over_under_away_blended") or pred_data["over_under_away"]
        team_lines = TEAM_LINES[market]

        lines_h = sorted(sorted(team_lines, key=lambda l: abs(l - lam_h))[:n_lines])
        lines_a = sorted(sorted(team_lines, key=lambda l: abs(l - lam_a))[:n_lines])

        hr_hs     = pred_data.get("hit_rates_home_stat", {})
        hr_as     = pred_data.get("hit_rates_away_stat", {})
        hr_hs_opp = pred_data.get("hit_rates_home_opp_stat", {})
        hr_as_opp = pred_data.get("hit_rates_away_opp_stat", {})
        ref_hr_home = ref_info.get("hit_rates_home", {})
        ref_hr_away = ref_info.get("hit_rates_away", {})
        col_h, col_a = st.columns(2)
        with col_h:
            home_label = home_team if home_team else "Domáci"
            st.markdown(f"**{home_label}** — {team_stat_label}: `{lam_h}`")
            ht_h = {
                line: {
                    "Tím":    hr_hs.get(line, ""),
                    "Súperi": hr_as_opp.get(line, ""),
                    **({"Rozh.": ref_hr_home.get(line, "")} if ref_hr_home else {}),
                }
                for line in lines_h
            }
            _show_ou_block(match_key, f"{market}_home", ou_h, lines_h, market, home_label, hit_rates=ht_h)
            if df is not None and home_team:
                _hc, _ac = MARKETS[market]["home_col"], MARKETS[market]["away_col"]
                st.caption(f"Forma doma: {_last5_team_vals(df, home_team, 'home', _hc, _ac)}")
                st.caption(f"Forma celkovo: {_last5_team_vals(df, home_team, 'all', _hc, _ac)}")
        with col_a:
            away_label = away_team if away_team else "Hostia"
            st.markdown(f"**{away_label}** — {team_stat_label}: `{lam_a}`")
            ht_a = {
                line: {
                    "Tím":    hr_as.get(line, ""),
                    "Súperi": hr_hs_opp.get(line, ""),
                    **({"Rozh.": ref_hr_away.get(line, "")} if ref_hr_away else {}),
                }
                for line in lines_a
            }
            _show_ou_block(match_key, f"{market}_away", ou_a, lines_a, market, away_label, hit_rates=ht_a)
            if df is not None and away_team:
                _hc, _ac = MARKETS[market]["home_col"], MARKETS[market]["away_col"]
                st.caption(f"Forma vonku: {_last5_team_vals(df, away_team, 'away', _hc, _ac)}")
                st.caption(f"Forma celkovo: {_last5_team_vals(df, away_team, 'all', _hc, _ac)}")

    if x1x2_key and pred_data.get(x1x2_key):
        st.divider()
        fx = pred_data[x1x2_key]
        b = fx.get("blended") or fx
        p1, px, p2 = b["1"], b["X"], b["2"]
        st.markdown(f"**{x1x2_label}**")
        cols = st.columns(3)
        labels_1x2 = [
            (f"1 — {home_team or 'Domáci'}", p1, fx.get("home_record"),
             fx.get("home_commits"), fx.get("home_receives")),
            ("X — Rovnako", px, None, None, None),
            (f"2 — {away_team or 'Hostia'}", p2, fx.get("away_record"),
             fx.get("away_commits"), fx.get("away_receives")),
        ]
        for col, (lbl, p, rec, commits, receives) in zip(cols, labels_1x2):
            with col:
                st.metric(lbl, f"{fair_odds(p):.2f}", f"{p:.1%}")
                bm_key = f"{match_key}_{x1x2_key}_{lbl[0]}"
                bm = st.number_input(
                    "Kurz BM", min_value=0.0, max_value=50.0, value=0.0,
                    step=0.05, format="%.2f", key=bm_key,
                )
                if bm > 0:
                    edge = p * bm - 1
                    st.markdown(f"Edge: **{edge:+.1%}** {edge_color(edge)}")
                if rec:
                    side_label = "dom" if lbl.startswith("1") else "host"
                    st.caption(f"Sezóna ({side_label}): {rec['w']}V / {rec['d']}R / {rec['l']}P z {rec['n']}")
                if commits is not None and receives is not None:
                    diff = commits - receives
                    st.caption(f"Robí: {commits:.1f} | Dostáva: {receives:.1f} | {diff:+.1f}")
                if df is not None:
                    _hc, _ac = MARKETS[market]["home_col"], MARKETS[market]["away_col"]
                    if lbl.startswith("1") and home_team:
                        st.caption(f"Forma doma: {_last5_form(df, home_team, 'home', _hc, _ac)}")
                        st.caption(f"Forma celkovo: {_last5_form(df, home_team, 'all', _hc, _ac)}")
                    elif lbl.startswith("2") and away_team:
                        st.caption(f"Forma vonku: {_last5_form(df, away_team, 'away', _hc, _ac)}")
                        st.caption(f"Forma celkovo: {_last5_form(df, away_team, 'all', _hc, _ac)}")

    h2h_matches = pred_data.get("h2h_matches", [])
    if h2h_matches:
        st.divider()
        st.markdown("**Posledné vzájomné zápasy (H2H)**")
        h2h_df = pd.DataFrame(h2h_matches)
        stat_cols = [c for c in h2h_df.columns if c not in ("match_id", "date", "home_team", "away_team")]
        col_map = {"date": "Dátum", "home_team": "Domáci", "away_team": "Hostia"}
        if len(stat_cols) == 2:
            col_map[stat_cols[0]] = f"{label} D"
            col_map[stat_cols[1]] = f"{label} H"
            h2h_df[f"{label} spolu"] = pd.to_numeric(h2h_df[stat_cols[0]], errors="coerce") + pd.to_numeric(h2h_df[stat_cols[1]], errors="coerce")
        h2h_df = h2h_df.drop(columns=["match_id"], errors="ignore").rename(columns=col_map)
        if "Dátum" in h2h_df.columns:
            h2h_df["Dátum"] = h2h_df["Dátum"].apply(
                lambda s: d.strftime("%d.%m.%Y") if (d := _parse_match_date(str(s))) else s
            )
        st.dataframe(h2h_df, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# HLAVNÁ APPKA
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Futbal — Fair kurzy", page_icon="⚽", layout="wide")

# Sidebar
with st.sidebar:
    st.header("Nastavenia")
    league_name = st.selectbox("Liga", list(_LEAGUES.keys()))
    DATA_DIR = Path(_LEAGUES[league_name])
    data_dir_str = str(DATA_DIR)
    st.divider()
    n_lines = st.slider("Počet zobrazených línií", min_value=3, max_value=12, value=5)
    margin_pct = st.slider("Marža bookmakera %", min_value=4, max_value=15, value=8)
    st.session_state["bm_margin"] = margin_pct / 100
    balanced = 2 / (1 + margin_pct / 100)
    st.caption(f"Pri 50/50: O={balanced:.2f}  U={balanced:.2f}")
    st.divider()
    _source = (
        "nikeliga.sk"             if "nikeliga"        in data_dir_str
        else "chanceliga.cz"      if "chanceliga"      in data_dir_str
        else "eredivisie.eu"      if "eredivisie"      in data_dir_str
        else "proleague.be"       if "proleague"       in data_dir_str
        else "ligue1.com"         if "ligue1"          in data_dir_str
        else "football-data.co.uk"
    )
    st.caption(f"Model: Poisson exp-decay={DECAY}")
    st.caption(f"Dáta: {_source}")
    st.divider()

    if "nikeliga" in data_dir_str:
        from nikeliga_batch import refresh_upcoming, fetch_season, export_csv as nike_export_csv
        if st.button("🔄 Aktualizovať výsledky", use_container_width=True):
            with st.spinner("Aktualizujem odohraté zápasy..."):
                refreshed, ref_errors = refresh_upcoming(DATA_DIR, days_ahead=4)
            with st.spinner("Sťahujem nové zápasy zo sezóny..."):
                downloaded, skipped, errors = fetch_season(2025, DATA_DIR, only_finished=False)
            with st.spinner("Exportujem matches.csv..."):
                nike_export_csv(DATA_DIR)
            st.cache_data.clear()
            total_errors = errors + ref_errors
            if total_errors:
                st.warning(f"Obnovené: {refreshed}, nové: {downloaded}, chyby: {total_errors}")
            else:
                st.success(f"Hotovo — obnovené: {refreshed}, nové: {downloaded}.")
            st.rerun()
        if st.button("Aktualizovať rozhodcov (PDF SFZ)", use_container_width=True):
            from nikeliga_referees import patch_referees
            with st.spinner("Sťahujem PDF obsadenia z futbalsfz.sk..."):
                updated, not_found = patch_referees(DATA_DIR)
            st.cache_data.clear()
            if not_found:
                st.warning(f"Aktualizovaných: {updated}, nenájdených: {not_found}")
            else:
                st.success(f"Rozhodcov aktualizovaných: {updated}")
            st.rerun()
    elif "chanceliga" in data_dir_str:
        from chanceliga_batch import discover_from_listing, fetch_matches, export_csv as chance_export_csv, _load_or_create_index, refresh_recent as chance_refresh
        if st.button("🔄 Aktualizovať zápasy", use_container_width=True):
            with st.spinner("Aktualizujem odohraté zápasy..."):
                refreshed, ref_errors = chance_refresh(DATA_DIR)
            with st.spinner("Sťahujem nové zápasy zo chanceliga.cz..."):
                matches = discover_from_listing()
                merged = _load_or_create_index(DATA_DIR, matches)
                downloaded, skipped, errors = fetch_matches(merged, DATA_DIR, only_finished=False)
            with st.spinner("Exportujem matches.csv..."):
                chance_export_csv(DATA_DIR)
            st.cache_data.clear()
            total_errors = errors + ref_errors
            if total_errors:
                st.warning(f"Obnovené: {refreshed}, nové: {downloaded}, chyby: {total_errors}")
            else:
                st.success(f"Hotovo — obnovené: {refreshed}, nové: {downloaded}.")
            st.rerun()
    elif "eredivisie" in data_dir_str:
        if st.button("🔄 Aktualizovať Eredivisie", use_container_width=True):
            import subprocess, sys as _sys
            with st.spinner("Sťahujem nové zápasy ..."):
                subprocess.run(
                    [_sys.executable, str(Path(__file__).parent / "eredivisie_scraper.py")],
                    capture_output=True, text=True,
                )
            with st.spinner("Generujem matches.csv ..."):
                subprocess.run(
                    [_sys.executable, str(Path(__file__).parent / "eredivisie_to_csv.py")],
                    capture_output=True, text=True,
                )
            st.cache_data.clear()
            st.success("Eredivisie aktualizovaná.")
            st.rerun()
    elif "proleague" in data_dir_str:
        if st.button("🔄 Aktualizovať Jupiler Pro League", use_container_width=True):
            import subprocess, sys as _sys
            with st.spinner("Sťahujem nové zápasy ..."):
                subprocess.run(
                    [_sys.executable, str(Path(__file__).parent / "proleague_scraper.py")],
                    capture_output=True, text=True,
                )
            with st.spinner("Generujem matches.csv ..."):
                subprocess.run(
                    [_sys.executable, str(Path(__file__).parent / "proleague_to_csv.py")],
                    capture_output=True, text=True,
                )
            st.cache_data.clear()
            st.success("Jupiler Pro League aktualizovaná.")
            st.rerun()
    elif _is_footballdata(data_dir_str):
        _fd_codes = {
            "premier_league": "E0", "bundesliga": "D1", "la_liga": "SP1",
            "serie_a": "I1", "primeira_liga": "P1",
        }
        _fd_code = next((v for k, v in _fd_codes.items() if k in data_dir_str), "E0")
        if st.button("🔄 Aktualizovať dáta", use_container_width=True):
            import subprocess, sys as _sys
            with st.spinner("Sťahujem aktuálne dáta z football-data.co.uk ..."):
                subprocess.run(
                    [_sys.executable, str(Path(__file__).parent / "footballdata_scraper.py"),
                     "--league", _fd_code, "--force"],
                    capture_output=True, text=True,
                )
            st.cache_data.clear()
            st.success("Dáta aktualizované.")
            st.rerun()
    else:
        if st.button("🔄 Aktualizovať Ligue 1", use_container_width=True):
            import subprocess, sys as _sys
            _errors = []
            with st.spinner("Sťahujem a aktualizujem zápasy ..."):
                r1 = subprocess.run(
                    [_sys.executable, str(Path(__file__).parent / "ligue1_scraper.py"), "--refresh"],
                    capture_output=True, text=True, encoding="utf-8",
                )
                if r1.returncode != 0:
                    _errors.append(r1.stderr or r1.stdout or "ligue1_scraper zlyhал")
            with st.spinner("Generujem matches.csv ..."):
                r2 = subprocess.run(
                    [_sys.executable, str(Path(__file__).parent / "ligue1_to_csv.py")],
                    capture_output=True, text=True, encoding="utf-8",
                )
                if r2.returncode != 0:
                    _errors.append(r2.stderr or r2.stdout or "ligue1_to_csv zlyhал")
            st.cache_data.clear()
            if _errors:
                st.warning("Ligue 1 aktualizovaná s chybami:\n\n" + "\n".join(_errors))
            else:
                st.success("Ligue 1 aktualizovaná.")
            st.rerun()

    if st.button("Reštart", use_container_width=True):
        import importlib, nikeliga_model, config
        importlib.reload(config)
        importlib.reload(nikeliga_model)
        st.cache_data.clear()
        st.session_state.pop("_manual_pred", None)
        st.session_state.pop("_bt_results", None)
        st.rerun()

st.title(f"⚽ {league_name} — Fair kurzy bočných trhov")

# Načítaj dáta
try:
    df = get_df(data_dir_str)
except Exception as e:
    st.error(f"Chyba pri načítaní dát: {e}")
    df = None

upcoming = get_upcoming(data_dir_str) if df is not None else []

_is_manual_league = (
    "eredivisie" in data_dir_str or "ligue1" in data_dir_str or
    "proleague" in data_dir_str or _is_footballdata(data_dir_str)
)

if df is None or not upcoming or _is_manual_league:
    if df is not None:
        if _is_manual_league:
            st.info(
                f"Načítané historické dáta ({len(df)} zápasov). "
                "Zadaj tímy manuálne pre predikciu."
            )
            _teams = sorted(set(df["home_team"].dropna()) | set(df["away_team"].dropna()))
            mc1, mc2 = st.columns(2)
            with mc1:
                _manual_home = st.selectbox("Domáci tím", _teams, key="manual_home")
            with mc2:
                _manual_away = st.selectbox("Hosťujúci tím",
                                            [t for t in _teams if t != _manual_home],
                                            key="manual_away")
            _referees = sorted(df["referee"].dropna().unique())
            _manual_ref = st.selectbox("Rozhodca (voliteľné)", [""] + _referees, key="manual_ref")
            if st.button("Vypočítať predikciu", use_container_width=True):
                try:
                    _pred = cached_predict(df, _manual_home, _manual_away, _manual_ref or None,
                                          league=league_name)
                    st.session_state["_manual_pred"] = (_manual_home, _manual_away, _manual_ref, _pred)
                except Exception as _e:
                    st.error(f"Chyba predikcie: {_e}")

            if "_manual_pred" in st.session_state:
                _mh, _ma, _mr, _mpred = st.session_state["_manual_pred"]
                _mk = f"{_mh}_{_ma}".replace(" ", "_")
                st.divider()
                st.markdown(f"### {_mh} vs {_ma}")
                if _mr:
                    st.caption(f"Rozhodca: {_mr}")

                _manual_upcoming = [{
                    "home": _mh,
                    "away": _ma,
                    "date": "",
                    "referee": _mr or "",
                    "match_id": 0,
                }]
                show_value_bets(_manual_upcoming, {_mk: _mpred}, DATA_DIR)
                st.divider()

                _tabs = st.tabs([MARKET_LABELS[m] for m in MARKET_LABELS])
                for _tab, (_market, _label) in zip(_tabs, MARKET_LABELS.items()):
                    with _tab:
                        show_market(_mk, _market, _label, _mpred[_market], n_lines, _mh, _ma, df)
        else:
            _lookahead = get_upcoming(data_dir_str, days_ahead=21)
            if _lookahead:
                from datetime import datetime as _dt
                def _sort_key(m):
                    d = _parse_match_date(m.get("date", ""))
                    return d if d else _dt.max
                _next = min(_lookahead, key=_sort_key)
                _label = f"{_next['home']} — {_next['away']}"
                _date_part = f" ({_next['date']})" if _next.get("date") else ""
                st.info(f"Žiadne zápasy v najbližších 3 dňoch. Najbližší: **{_label}**{_date_part}")
            else:
                _fd_codes = {
                    "premier_league": "E0", "bundesliga": "D1", "la_liga": "SP1",
                    "serie_a": "I1", "primeira_liga": "P1",
                }
                _fd_code = next((v for k, v in _fd_codes.items() if k in data_dir_str), None)
                fetch_hint = (
                    "`python nikeliga_batch.py fetch 2025 data/nikeliga`"
                    if "nikeliga" in data_dir_str else
                    "`python chanceliga_batch.py listing data/chanceliga --all`"
                    if "chanceliga" in data_dir_str else
                    "`python proleague_scraper.py`"
                    if "proleague" in data_dir_str else
                    f"`python footballdata_scraper.py --league {_fd_code} --force`"
                    if _fd_code else
                    "`python eredivisie_to_csv.py`"
                )
                st.info(f"Žiadne nadchádzajúce zápasy. Spusti najprv: {fetch_hint}")

else:
    st.markdown(f"**{len(upcoming)} nadchádzajúcich zápasov** — zadaj kurzy bookmakera a uvidíš kde je hodnota.")
    st.divider()

    all_predictions: dict = {}
    _needs_ref_select = "chanceliga" in data_dir_str or "ligue1" in data_dir_str or "premier_league" in data_dir_str or "eredivisie" in data_dir_str
    for _m in upcoming:
        _mk = f"{_m['home']}_{_m['away']}".replace(" ", "_")
        try:
            _mot_h = float(st.session_state.get(f"{_mk}_mot_home", 1.0))
            _mot_a = float(st.session_state.get(f"{_mk}_mot_away", 1.0))
            _ref = _m['referee'] or (st.session_state.get(f"{_mk}_referee_override", "") if _needs_ref_select else "")
            all_predictions[_mk] = cached_predict(
                df, _m['home'], _m['away'], _ref or None, _mot_h, _mot_a, league=league_name
            )
        except Exception:
            pass

    show_value_bets(upcoming, all_predictions, DATA_DIR)
    st.divider()

    for match in upcoming:
        render_match(match, df, n_lines, league_name, _needs_ref_select)

    st.divider()
    st.caption("Edge = (pravdepodobnosť modelu × kurz bookmakera) − 1. Stávkuj len ak edge > 0.")

# História tipov a backtest — viditeľné vždy (nezávisia od upcoming)
if df is not None:
    with st.expander("📋 História tipov", expanded=False):
        tips_df = load_tips(DATA_DIR)
        summ = tips_summary(tips_df)

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Pending", summ["pending"])
        m2.metric("Vyhrané", summ["won"])
        m3.metric("Prehrané", summ["lost"])
        m4.metric("Profit (j.)", f"{summ['profit']:+.2f}")
        roi_str = f"{summ['roi']:.1%}" if summ["roi"] is not None else "—"
        m5.metric("ROI", roi_str)

        c_settle, c_refresh = st.columns([1, 1])
        with c_settle:
            if st.button("✅ Vyhodnoť tipy", key="settle_tips_btn", use_container_width=True):
                with st.spinner("Vyhodnocujem..."):
                    settled, errs = settle_tips(DATA_DIR)
                if errs:
                    st.warning(f"Vyhodnotených: {settled}, chyby: {errs}")
                    st.rerun()
                elif settled > 0:
                    st.success(f"Vyhodnotených: {settled} tipov.")
                    st.rerun()
                else:
                    # Skontroluj či sú pending tipy pre minulé zápasy bez výsledkov
                    from datetime import datetime as _dt2, timedelta as _td
                    _fresh = load_tips(DATA_DIR)
                    _pending = _fresh[_fresh["status"] == "pending"] if not _fresh.empty else _fresh
                    _stale = []
                    for _, _tip in _pending.iterrows():
                        _raw_date = str(_tip.get("match_date", "")).strip()
                        _match_date_str = "" if _raw_date.lower() in ("nan", "none", "") else _raw_date
                        _md = _parse_match_date(_match_date_str) if _match_date_str else None
                        if _md is None:
                            # Fallback: ak match_date chýba, použi recorded_at + 1 deň
                            _rec = str(_tip.get("recorded_at", "")).strip()
                            try:
                                _rec_dt = _dt2.strptime(_rec[:16], "%Y-%m-%d %H:%M")
                                if _rec_dt < _dt2.now() - _td(hours=24):
                                    _md = _rec_dt
                            except Exception:
                                pass
                        if _md and _md < _dt2.now():
                            _label = _match_date_str or str(_tip.get("recorded_at", ""))[:10]
                            _stale.append(f"{_tip['home_team']} — {_tip['away_team']} ({_label})")
                    if _stale:
                        _refresh_hint = "🔄 Aktualizovať výsledky" if "nikeliga" in data_dir_str else "🔄 Aktualizovať zápasy"
                        st.warning(
                            f"Zápas ešte nemá stiahnutý výsledok. "
                            f"Klikni **{_refresh_hint}** v sidebari a potom skús znova.\n\n"
                            + "\n".join(f"- {s}" for s in _stale)
                        )
                    else:
                        st.info("Žiadne tipy na vyhodnotenie (zápasy ešte nenastali).")
        with c_refresh:
            if st.button("🔄 Obnoviť históriu", key="refresh_tips_btn", use_container_width=True):
                st.rerun()

        if not tips_df.empty:
            display_cols = ["recorded_at", "home_team", "away_team", "market",
                            "bet_type", "line", "direction", "model_prob",
                            "bm_odds", "edge", "stake", "status", "actual_value", "profit"]
            show_df = tips_df[[c for c in display_cols if c in tips_df.columns]].copy()
            for col in ("model_prob", "edge"):
                if col in show_df.columns:
                    show_df[col] = pd.to_numeric(show_df[col], errors="coerce").map(
                        lambda v: f"{v:.1%}" if pd.notna(v) else "")
            st.dataframe(show_df, hide_index=True, use_container_width=True)
        else:
            st.info("Zatiaľ žiadne uložené tipy. Zadaj BM kurzy a klikni 💾 Uložiť tipy.")

    with st.expander("🔬 Backtest modelu", expanded=False):
        if st.button("Spustiť backtest", key="run_backtest_btn"):
            with st.spinner("Počítam walk-forward backtest — chvíľu strpenia..."):
                bt = run_backtest(df, league=league_name)
            st.session_state["_bt_results"] = bt
        bt = st.session_state.get("_bt_results")
        if bt is not None:
            if bt.empty:
                st.warning("Nedostatok historických dát.")
            else:
                _show_backtest(bt)

_data_root = Path(__file__).parent / "data"
_league_tips_frames = []
for _ld in sorted(_data_root.iterdir()):
    if _ld.is_dir():
        _tp = _ld / "tips.csv"
        if _tp.exists():
            _lf = pd.read_csv(_tp, dtype=str)
            _lf.insert(0, "league", _ld.name)
            _league_tips_frames.append(_lf)

if _league_tips_frames:
    with st.expander("📊 Všetky tipy — súhrnný prehľad", expanded=False):
        _all_df = pd.concat(_league_tips_frames, ignore_index=True)
        _all_df["stake"] = pd.to_numeric(_all_df["stake"], errors="coerce")
        _all_df["bm_odds"] = pd.to_numeric(_all_df["bm_odds"], errors="coerce")
        # Prepočítaj profit podľa aktuálneho stake
        _all_df["profit"] = _all_df.apply(
            lambda r: round((r["bm_odds"] - 1) * r["stake"], 2) if r["status"] == "won"
            else (-r["stake"] if r["status"] == "lost" else float("nan")),
            axis=1,
        )

        # Filtre
        _league_filter = st.multiselect(
            "Filtruj ligu", options=sorted(_all_df["league"].unique()),
            default=sorted(_all_df["league"].unique()), key="all_tips_league_filter"
        )
        _col_f1, _col_f2 = st.columns(2)
        with _col_f1:
            _status_filter = st.multiselect(
                "Filtruj status", options=["won", "lost", "pending"],
                default=["won", "lost", "pending"], key="all_tips_status_filter"
            )
        with _col_f2:
            _market_options = sorted(_all_df["market"].dropna().unique())
            _market_filter = st.multiselect(
                "Filtruj market", options=_market_options,
                default=_market_options, key="all_tips_market_filter"
            )
        _filtered = _all_df[
            _all_df["league"].isin(_league_filter)
            & _all_df["status"].isin(_status_filter)
            & _all_df["market"].isin(_market_filter)
        ]

        # Metriky celkovo (z filtrovaných dát)
        _settled = _filtered[_filtered["status"].isin(["won", "lost"])]
        _total_profit = _settled["profit"].sum()
        _total_stake = _settled["stake"].sum()
        _roi = _total_profit / _total_stake if _total_stake > 0 else None
        _cm1, _cm2, _cm3, _cm4, _cm5 = st.columns(5)
        _cm1.metric("Celkom tipov", len(_filtered))
        _cm2.metric("Vyhrané", int((_filtered["status"] == "won").sum()))
        _cm3.metric("Prehrané", int((_filtered["status"] == "lost").sum()))
        _cm4.metric("Profit (j.)", f"{_total_profit:+.2f}")
        _cm5.metric("ROI", f"{_roi:.1%}" if _roi is not None else "—")

        # Súhrn per liga (z filtrovaných dát)
        st.markdown("**Per liga:**")
        _league_grp = []
        for _lg, _grp in _filtered.groupby("league"):
            _s = _grp[_grp["status"].isin(["won", "lost"])]
            _p = _s["profit"].sum()
            _sk = _s["stake"].sum()
            _league_grp.append({
                "Liga": _lg,
                "Tipy": len(_grp),
                "Won": int((_grp["status"] == "won").sum()),
                "Lost": int((_grp["status"] == "lost").sum()),
                "Pending": int((_grp["status"] == "pending").sum()),
                "Profit": round(_p, 2),
                "ROI": f"{_p/_sk:.1%}" if _sk > 0 else "—",
            })
        if _league_grp:
            st.dataframe(pd.DataFrame(_league_grp), hide_index=True, use_container_width=True)
        else:
            st.info("Žiadne tipy zodpovedajú filtru.")

        # Detailná tabuľka
        st.markdown("**Detailná história:**")
        _disp_cols = ["league", "recorded_at", "home_team", "away_team", "market",
                      "bet_type", "line", "direction", "bm_odds", "edge",
                      "stake", "status", "actual_value", "profit"]
        st.dataframe(
            _filtered[[c for c in _disp_cols if c in _filtered.columns]],
            hide_index=True, use_container_width=True
        )
