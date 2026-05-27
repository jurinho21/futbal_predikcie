"""UI widgety pre Streamlit app — show_market, render_match, value bets, backtest, helpers."""

import re
import json
import pandas as pd
import streamlit as st
from pathlib import Path
from datetime import datetime

from config import (
    MARKETS, MARKET_LABELS, MARKET_LINES, TEAM_LINES,
    X1X2_KEYS, X1X2_LABELS, TEAM_STAT_LABELS, EDGE_GREEN, EDGE_YELLOW,
)
from nikeliga_model import fair_odds
from nikeliga_tips import save_tips
from predictions import cached_predict, MOT_OPTIONS
from github_sync import get_github_token


# ---------------------------------------------------------------------------
# DATE PARSING
# ---------------------------------------------------------------------------

def parse_match_date(date_str: str):
    """Parsuje dátum vo formátoch: Niké liga (DD.MM.YYYY), Chance liga (DD/MM/YYYY), Eredivisie (YYYY-MM-DD)."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})", date_str)
    if m:
        day, mon, year, hour, minute = (int(x) for x in m.groups())
        try:
            return datetime(year, mon, day, hour, minute)
        except ValueError:
            pass
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4}),?\s*(\d{2}):(\d{2})", date_str)
    if m:
        day, mon, year, hour, minute = (int(x) for x in m.groups())
        try:
            return datetime(year, mon, day, hour, minute)
        except ValueError:
            pass
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", date_str.strip())
    if m:
        year, mon, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(year, mon, day, 23, 59)
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# EDGE + OU HELPERS
# ---------------------------------------------------------------------------

def edge_color(edge: float) -> str:
    if edge >= EDGE_GREEN:
        return "🟢"
    elif edge >= EDGE_YELLOW:
        return "🟡"
    else:
        return "🔴"


def _form_html(form_str: str) -> str:
    """Konvertuje formu 'V, R, P' na farebné HTML odznaky."""
    _colors = {"V": "#27ae60", "R": "#e67e22", "P": "#e74c3c"}
    parts = []
    for token in form_str.split(", "):
        token = token.strip()
        color = _colors.get(token)
        if color:
            parts.append(
                f'<span style="background:{color};color:white;padding:2px 7px;'
                f'border-radius:4px;font-weight:bold;font-size:0.8em;margin:1px">{token}</span>'
            )
        elif token and token != "—":
            parts.append(token)
    return " ".join(parts) if parts else "—"


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
                    f"O{line}", min_value=0.0, max_value=50.0, value=0.0,
                    step=0.05, format="%.2f", key=over_key,
                    on_change=_fill_counterpart,
                    args=(over_key, "O", line, match_key, section_key),
                )
            with c2:
                st.number_input(
                    f"U{line}", min_value=0.0, max_value=50.0, value=0.0,
                    step=0.05, format="%.2f", key=under_key,
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
    stat_label   = TEAM_STAT_LABELS.get(market, market)

    with st.expander(f"🟨 Rozhodca — {n} zápasov v dátach", expanded=False):
        c1, c2, c3 = st.columns(3)

        def _delta(ref_val, league_val):
            if ref_val is None or league_val is None:
                return None
            return round(ref_val - league_val, 1)

        with c1:
            delta = _delta(avg_total, league_total)
            st.metric(
                f"Priem. {stat_label} (totál)",
                f"{avg_total:.1f}" if avg_total else "—",
                f"{delta:+.1f} vs liga" if delta is not None else None,
            )
            if league_total:
                st.caption(f"Ligový priemer: {league_total:.1f}")
        with c2:
            delta_h = _delta(avg_home, league_home)
            st.metric(
                "Domáci tím",
                f"{avg_home:.1f}" if avg_home else "—",
                f"{delta_h:+.1f} vs liga" if delta_h is not None else None,
            )
            if league_home:
                st.caption(f"Ligový priemer: {league_home:.1f}")
        with c3:
            delta_a = _delta(avg_away, league_away)
            st.metric(
                "Hosťujúci tím",
                f"{avg_away:.1f}" if avg_away else "—",
                f"{delta_a:+.1f} vs liga" if delta_a is not None else None,
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


# ---------------------------------------------------------------------------
# FORMA — last 5 zápasov
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# SHOW MARKET
# ---------------------------------------------------------------------------

def show_market(match_key: str, market: str, label: str, pred_data: dict,
                n_lines: int, home_team: str = "", away_team: str = "", df=None):
    lam = pred_data["lambda_total"]
    ou = pred_data.get("over_under_blended") or pred_data["over_under"]

    all_lines = MARKET_LINES.get(market, [])
    sorted_lines = sorted(all_lines, key=lambda l: abs(l - lam))
    show_lines = sorted(sorted_lines[:n_lines])

    st.markdown(f"**{label}** — očakávaný total: `{lam}`")
    hr_ht    = pred_data.get("hit_rates_home_total", {})
    hr_at    = pred_data.get("hit_rates_away_total", {})
    hr_ha    = pred_data.get("hit_rates_home_all", {})
    hr_aa    = pred_data.get("hit_rates_away_all", {})
    hr_h2h   = pred_data.get("hit_rates_h2h_total", {})
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
            st.caption(f"Forma vonku ({home_team}): {_last5_totals(df, home_team, 'away', _hc, _ac)}")
        with fc2:
            st.caption(f"Forma vonku ({away_team}): {_last5_totals(df, away_team, 'away', _hc, _ac)}")
            st.caption(f"Forma doma ({away_team}): {_last5_totals(df, away_team, 'home', _hc, _ac)}")

    if ref_info and ref_info.get("n", 0) > 0:
        _show_referee_stats(ref_info, market)

    x1x2_key       = X1X2_KEYS.get(market)
    x1x2_label     = X1X2_LABELS.get(market, "")
    team_stat_label = TEAM_STAT_LABELS.get(market, "")

    if market in ("fouls", "shots_on_target", "corners", "yellow_cards") and "over_under_home" in pred_data:
        st.divider()
        lam_h = pred_data["lambda_home"]
        lam_a = pred_data["lambda_away"]
        ou_h  = pred_data.get("over_under_home_blended") or pred_data["over_under_home"]
        ou_a  = pred_data.get("over_under_away_blended") or pred_data["over_under_away"]
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
                st.caption(f"Forma vonku: {_last5_team_vals(df, home_team, 'away', _hc, _ac)}")
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
                st.caption(f"Forma doma: {_last5_team_vals(df, away_team, 'home', _hc, _ac)}")

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
                    st.markdown(f"<small>Sezóna ({side_label}): {rec['w']}V / {rec['d']}R / {rec['l']}P z {rec['n']}</small>", unsafe_allow_html=True)
                if commits is not None and receives is not None:
                    diff = commits - receives
                    st.markdown(f"<small>Robí: {commits:.1f} | Dostáva: {receives:.1f} | {diff:+.1f}</small>", unsafe_allow_html=True)
                if df is not None:
                    _hc, _ac = MARKETS[market]["home_col"], MARKETS[market]["away_col"]
                    if lbl.startswith("1") and home_team:
                        st.markdown(f"Forma doma: {_form_html(_last5_form(df, home_team, 'home', _hc, _ac))}", unsafe_allow_html=True)
                        st.markdown(f"<small style='opacity:0.6'>Forma vonku: {_form_html(_last5_form(df, home_team, 'away', _hc, _ac))}</small>", unsafe_allow_html=True)
                    elif lbl.startswith("2") and away_team:
                        st.markdown(f"Forma vonku: {_form_html(_last5_form(df, away_team, 'away', _hc, _ac))}", unsafe_allow_html=True)
                        st.markdown(f"<small style='opacity:0.6'>Forma doma: {_form_html(_last5_form(df, away_team, 'home', _hc, _ac))}</small>", unsafe_allow_html=True)

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
            h2h_df[f"{label} spolu"] = (
                pd.to_numeric(h2h_df[stat_cols[0]], errors="coerce") +
                pd.to_numeric(h2h_df[stat_cols[1]], errors="coerce")
            )
        h2h_df = h2h_df.drop(columns=["match_id"], errors="ignore").rename(columns=col_map)
        if "Dátum" in h2h_df.columns:
            h2h_df["Dátum"] = h2h_df["Dátum"].apply(
                lambda s: d.strftime("%d.%m.%Y") if (d := parse_match_date(str(s))) else s
            )
        st.dataframe(h2h_df, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# RENDER MATCH (fragment)
# ---------------------------------------------------------------------------

@st.fragment
def render_match(match: dict, df, n_lines: int, league_name: str,
                 needs_ref_select: bool, data_v: float = 0.0):
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
        pred = cached_predict(df, home, away, referee or None, mot_h, mot_a,
                              data_v=data_v, league=league_name)
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
                f"Motivácia — {home}", options=MOT_OPTIONS, value=mot_h,
                key=f"{match_key}_mot_home",
            )
        with mc2:
            st.select_slider(
                f"Motivácia — {away}", options=MOT_OPTIONS, value=mot_a,
                key=f"{match_key}_mot_away",
            )
        tabs = st.tabs([MARKET_LABELS[m] for m in MARKET_LABELS])
        for tab, (market, label) in zip(tabs, MARKET_LABELS.items()):
            with tab:
                show_market(match_key, market, label, pred[market], n_lines, home, away, df)


# ---------------------------------------------------------------------------
# VALUE BETS
# ---------------------------------------------------------------------------

def _collect_value_bets(upcoming: list, all_predictions: dict) -> list[dict]:
    """Zbiera value bety (edge > 2 %) zo session_state + pre-computed predikcií."""
    rows = []
    for match in upcoming:
        home, away = match["home"], match["away"]
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
                    p = (mp.get("over_under_blended") or mp.get("over_under", {})).get(f"{d}{line}")
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


def show_value_bets(upcoming: list, all_predictions: dict, data_dir: Path):
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

            to_save   = edited.index[edited["💾"]].tolist()
            to_delete = edited.index[edited["🗑️"]].tolist()

            col_s, col_d = st.columns(2)
            with col_s:
                if to_save:
                    stake = st.number_input("Stake (€)", min_value=0.0, step=1.0, value=0.0, key="vbets_stake")
                    if st.button(f"💾 Uložiť {len(to_save)} vybraných", key="save_vbets_btn", use_container_width=True):
                        if stake <= 0:
                            st.warning("Zadaj stake pred uložením.")
                        else:
                            tips_to_save = [{
                                "match_id":   vbets[i]["_match_id"],
                                "home_team":  vbets[i]["_home"],
                                "away_team":  vbets[i]["_away"],
                                "match_date": vbets[i]["_match_date"],
                                "market":     vbets[i]["_market"],
                                "bet_type":   vbets[i]["_bet_type"],
                                "line":       vbets[i]["_line"],
                                "direction":  vbets[i]["_direction"],
                                "model_prob": vbets[i]["_p"],
                                "bm_odds":    vbets[i]["Kurz"],
                                "edge":       vbets[i]["_edge"],
                            } for i in to_save]
                            n_saved = save_tips(data_dir, tips_to_save, stake=stake, github_token=get_github_token())
                            st.success(f"Uložených {n_saved} tipov.")
                            st.rerun()
            with col_d:
                if to_delete:
                    if st.button(f"🗑️ Vymazať {len(to_delete)} vybraných", key="del_vbets_btn", use_container_width=True):
                        st.session_state["_vbets_to_delete"] = [vbets[i]["_ss_key"] for i in to_delete]
                        st.rerun()


# ---------------------------------------------------------------------------
# BACKTEST VISUALIZATION
# ---------------------------------------------------------------------------

def show_backtest(bt_df: pd.DataFrame):
    has_type = "type" in bt_df.columns
    ou_df   = bt_df[bt_df["type"] == "ou"]   if has_type else bt_df
    x1x2_df = bt_df[bt_df["type"] == "1x2"]  if has_type else pd.DataFrame()
    st.caption(f"Walk-forward backtest — {len(ou_df)} O/U predikcií · {len(x1x2_df)} 1X2 predikcií na historických zápasoch")
    bins       = [0.0, 0.3, 0.45, 0.55, 0.7, 1.01]
    bin_labels = ["< 30 %", "30–45 %", "45–55 %", "55–70 %", "> 70 %"]
    st.markdown("#### Over/Under")
    for market, mlabel in MARKET_LABELS.items():
        mdf = ou_df[ou_df["market"] == market].copy()
        if len(mdf) < 15:
            continue
        brier = float(((mdf["p_over"] - mdf["actual_over"]) ** 2).mean())
        acc   = float(((mdf["p_over"] > 0.5) == mdf["actual_over"]).mean())
        mdf["bucket"] = pd.cut(mdf["p_over"], bins=bins, labels=bin_labels)
        calib = (mdf.groupby("bucket", observed=True)
                 .agg(**{"Model P": ("p_over", "mean"), "Skutočná P": ("actual_over", "mean"), "N": ("actual_over", "count")})
                 .reset_index().rename(columns={"bucket": "Bucket"}))
        st.markdown(f"**{mlabel}** — {len(mdf)} pred. · Brier score: `{brier:.4f}` · Presnosť (>50%): `{acc:.1%}`")
        c1, c2 = st.columns([3, 2])
        with c1:
            st.bar_chart(calib.set_index("Bucket")[["Model P", "Skutočná P"]])
        with c2:
            st.dataframe(calib.round(3), hide_index=True, use_container_width=True)
        st.divider()
    if x1x2_df.empty:
        return
    st.markdown("#### 1X2")
    for market, mlabel in MARKET_LABELS.items():
        mdf = x1x2_df[x1x2_df["market"] == market].copy()
        if len(mdf) < 15:
            continue
        mdf["predicted"] = mdf[["p1", "pX", "p2"]].idxmax(axis=1).map({"p1": "1", "pX": "X", "p2": "2"})
        acc = float((mdf["predicted"] == mdf["actual_1x2"]).mean())
        mdf["brier"] = (
            (mdf["p1"] - (mdf["actual_1x2"] == "1").astype(float)) ** 2 +
            (mdf["pX"] - (mdf["actual_1x2"] == "X").astype(float)) ** 2 +
            (mdf["p2"] - (mdf["actual_1x2"] == "2").astype(float)) ** 2
        ) / 3
        brier = float(mdf["brier"].mean())
        st.markdown(f"**{mlabel}** — {len(mdf)} pred. · Brier score: `{brier:.4f}` · Presnosť (top výber): `{acc:.1%}`")
        outcome_rows = []
        for outcome, col, label in [("1", "p1", "Domáci viac"), ("X", "pX", "Rovnako"), ("2", "p2", "Hostia viac")]:
            outcome_rows.append({
                "Výsledok": label,
                "Model P (avg)": round(float(mdf[col].mean()), 3),
                "Skutočná P": round(float((mdf["actual_1x2"] == outcome).mean()), 3),
                "N": int((mdf["actual_1x2"] == outcome).sum()),
            })
        c1, c2 = st.columns([2, 3])
        with c1:
            st.dataframe(pd.DataFrame(outcome_rows), hide_index=True, use_container_width=True)
        with c2:
            freq_df = pd.DataFrame(outcome_rows).set_index("Výsledok")[["Model P (avg)", "Skutočná P"]]
            st.bar_chart(freq_df)
        st.divider()
