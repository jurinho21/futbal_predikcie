"""
config.py — centrálna konfigurácia Niké liga projektu
Všetky laditeľné parametre a definície trhov sú tu.
"""

# ---------------------------------------------------------------------------
# PARAMETRE MODELU
# ---------------------------------------------------------------------------

DECAY = 0.07          # exponenciálny decay na zápas (~10 zápasov = polovica váhy)
CREDIBILITY_K = 10    # pri 10 zápasoch = 50/50 váha model vs empirika
REFEREE_MIN = 5       # min zápasov rozhodcu pre čiastočný referee-adjust
REFEREE_FULL = 10     # zápasov rozhodcu pre plný referee-adjust
H2H_WEIGHT = 0.25     # váha H2H pri výpočte totalu
YELLOW_REF_STRENGTH = 1.3  # rozhodca priamo udeľuje karty — vyšší vplyv ako pri fauloch

# Stiahnutie lambda k priemeru pre 1X2 (vyhladzuje extrémne predikcie)
X1X2_SHRINKS = {
    "fouls": 0.6,
    "shots_on_target": 0.6,
    "corners": 0.6,
    "yellow_cards": 0.6,
}

# ---------------------------------------------------------------------------
# DEFINÍCIE TRHOV
# ---------------------------------------------------------------------------

MARKETS = {
    "fouls": {
        "home_col": "home_fouls",
        "away_col": "away_fouls",
        "total_col": "total_fouls",
    },
    "shots_on_target": {
        "home_col": "home_shots_on_target",
        "away_col": "away_shots_on_target",
        "total_col": "total_sot",
    },
    "corners": {
        "home_col": "home_corners",
        "away_col": "away_corners",
        "total_col": "total_corners",
    },
    "yellow_cards": {
        "home_col": "home_yellow",
        "away_col": "away_yellow",
        "total_col": "total_yellow",
    },
}

MARKET_LABELS = {
    "fouls": "Fauly",
    "shots_on_target": "Strely na bránu",
    "corners": "Rohové kopy",
    "yellow_cards": "Žlté karty",
}

# Zobrazované línie pre totálne O/U
MARKET_LINES = {
    "fouls": [l + 0.5 for l in range(14, 36)],
    "shots_on_target": [l + 0.5 for l in range(3, 18)],
    "corners": [l + 0.5 for l in range(5, 18)],
    "yellow_cards": [l + 0.5 for l in range(1, 10)],
}

# Zobrazované línie pre individuálne tímové štatistiky
TEAM_LINES = {
    "fouls": [l + 0.5 for l in range(7, 23)],
    "shots_on_target": [l + 0.5 for l in range(1, 11)],
    "corners": [l + 0.5 for l in range(1, 11)],
    "yellow_cards": [l + 0.5 for l in range(0, 6)],
}

# Kľúče výsledného dicts pre 1X2 výstupy
X1X2_KEYS = {
    "fouls": "foul_1x2",
    "shots_on_target": "sot_1x2",
    "corners": "corner_1x2",
    "yellow_cards": "yellow_1x2",
}

X1X2_LABELS = {
    "fouls": "Viac faulov — 1X2",
    "shots_on_target": "Viac striel na bránu — 1X2",
    "corners": "Viac rohov — 1X2",
    "yellow_cards": "Viac žltých kariet — 1X2",
}

TEAM_STAT_LABELS = {
    "fouls": "fauly",
    "shots_on_target": "SoT",
    "corners": "rohy",
    "yellow_cards": "žlté karty",
}

# Maximálny fair kurz pre X v 1X2 — ak by bol vyšší, prebytok sa prerozdelí na 1 a 2
X1X2_MAX_DRAW_ODDS = {
    "fouls": 15,
    "shots_on_target": 9,
    "corners": 10,
    "yellow_cards": 4.5,
}

# Unifikovaný rozsah línií pre hit rate výpočty (oba moduly používali rôzne rozsahy: 35 vs 40)
HR_LINES = tuple(l + 0.5 for l in range(0, 40))

# ---------------------------------------------------------------------------
# UI PRAHY PRE ZOBRAZENIE EDGE
# ---------------------------------------------------------------------------

EDGE_GREEN = 0.08
EDGE_YELLOW = 0.03
