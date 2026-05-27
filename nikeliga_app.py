"""
nikeliga_app.py — Streamlit app pre predikciu bočných trhov Niké ligy a Chance ligy
Spustenie: streamlit run nikeliga_app.py
"""

import json
import logging
from pathlib import Path

import streamlit as st
import pandas as pd

from nikeliga_model import load_data
from nikeliga_tips import settle_tips, load_tips, tips_summary
from config import DECAY, MARKET_LABELS
from github_sync import get_github_token

from predictions import cached_predict, run_backtest
from ui.widgets import (
    parse_match_date as _parse_match_date,
    show_market, render_match,
    show_value_bets, show_backtest as _show_backtest,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Ochrana heslom
# ---------------------------------------------------------------------------
def _check_auth() -> bool:
    try:
        correct = st.secrets["APP_PASSWORD"]
    except Exception:
        return True  # lokálne bez secrets = voľný prístup

    if st.session_state.get("authenticated"):
        return True

    st.title("🔒 Prístup chránený heslom")
    pwd = st.text_input("Heslo", type="password")
    if st.button("Prihlásiť"):
        if pwd == correct:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Nesprávne heslo.")
    st.stop()

_check_auth()

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

@st.cache_data
def get_df(data_dir_str: str, csv_mtime: float = 0.0):
    return load_data(Path(data_dir_str))


def _csv_mtime(data_dir_str: str) -> float:
    p = Path(data_dir_str) / "matches.csv"
    return p.stat().st_mtime if p.exists() else 0.0


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
_mtime = _csv_mtime(data_dir_str)
try:
    df = get_df(data_dir_str, csv_mtime=_mtime)
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
                                          data_v=_mtime, league=league_name)
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
                df, _m['home'], _m['away'], _ref or None, _mot_h, _mot_a,
                data_v=_mtime, league=league_name
            )
        except Exception:
            pass

    show_value_bets(upcoming, all_predictions, DATA_DIR)
    st.divider()

    for match in upcoming:
        render_match(match, df, n_lines, league_name, _needs_ref_select, data_v=_mtime)

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
                    settled, errs = settle_tips(DATA_DIR, github_token=get_github_token())
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
