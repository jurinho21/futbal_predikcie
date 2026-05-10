"""
test_chanceliga_scraper.py — unit testy pre chanceliga_scraper (parsing bez sieťových požiadaviek)
"""
import pytest
from chanceliga_scraper import parse_match, _to_num, _parse_combo, STAT_MAP

# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

MINIMAL_HTML = """
<html><body>
  <div class="col-xs-12 box-content bcg-white no-padding typography game game__header">
    3. kolo | sobota 02/05/2026, 18:00 | Stadión Test |
  </div>
  <div class="game__scorebox">
    <div class="game__scorebox__team">
      <div class="game__scorebox__team-name hidden-xs"><a href="/tym/slovan">Slovan Praha</a></div>
    </div>
    <div class="game__scorebox__team">
      <div class="game__scorebox__team-name hidden-xs"><a href="/tym/sparta">AC Sparta Praha</a></div>
    </div>
    <div class="game__scorebox__score">2 : 1</div>
    <div class="game__scorebox__score-halftime">(1 : 0)</div>
  </div>
  <div class="game__info-referees">
    Rozhodčí: <a href="/rozhodci/novak">Jan Novák</a>, <a href="/rozhodci/kral">Pavel Král</a>
  </div>
</body></html>
"""

HTML_WITH_STATS = """
<html><body>
  <div class="col-xs-12 box-content bcg-white no-padding typography game game__header">
    5. kolo | neděle 10/05/2026, 17:00 | Stadión Test |
  </div>
  <div class="game__scorebox">
    <div class="game__scorebox__team">
      <div class="game__scorebox__team-name hidden-xs"><a href="/tym/slovan">Slovan</a></div>
    </div>
    <div class="game__scorebox__team">
      <div class="game__scorebox__team-name hidden-xs"><a href="/tym/sparta">Sparta</a></div>
    </div>
    <div class="game__scorebox__score">1 : 0</div>
  </div>
  <div class="hidden-xs col-sm-12 box-content bcg-white typography game game__stats">
    <div class="col-xs-12 stats-container">
      <div class="stats right"><div class="value">14</div></div>
      <div class="stats-name">Fauly</div>
      <div class="stats"><div class="value">11</div></div>
    </div>
    <div class="col-xs-12 stats-container">
      <div class="stats right"><div class="value">6</div></div>
      <div class="stats-name">Rohy</div>
      <div class="stats"><div class="value">4</div></div>
    </div>
    <div class="col-xs-12 stats-container">
      <div class="stats right"><div class="value">12/5</div></div>
      <div class="stats-name">Střely/na branku</div>
      <div class="stats"><div class="value">8/3</div></div>
    </div>
  </div>
</body></html>
"""

HTML_WITH_CARDS_IN_EVENTS = """
<html><body>
  <div class="game__scorebox">
    <div class="game__scorebox__team">
      <div class="game__scorebox__team-name hidden-xs"><a href="/a">Team A</a></div>
    </div>
    <div class="game__scorebox__team">
      <div class="game__scorebox__team-name hidden-xs"><a href="/b">Team B</a></div>
    </div>
    <div class="game__scorebox__score">0 : 0</div>
  </div>
  <div class="col-xs-12 col-sm-4 items-list goal-modal"><ul></ul></div>
  <div class="col-xs-12 col-sm-4 items-list goal-modal"><ul></ul></div>
  <div class="col-xs-12 col-sm-4 items-list">
    <ul>
      <li><i class="ico ico-card yellow"></i><a href="/h1">34' Hráč A</a></li>
      <li><i class="ico ico-card yellow"></i><a href="/h2">71' Hráč B</a></li>
    </ul>
  </div>
  <div class="col-xs-12 col-sm-4 items-list">
    <ul>
      <li><i class="ico ico-card red"></i><a href="/h3">55' Hráč C</a></li>
    </ul>
  </div>
</body></html>
"""

HTML_NO_SCORE = """
<html><body>
  <div class="col-xs-12 box-content bcg-white no-padding typography game game__header">
    7. kolo | sobota 20/06/2026, 18:00 | Stadión Test |
  </div>
  <div class="game__scorebox">
    <div class="game__scorebox__team">
      <div class="game__scorebox__team-name hidden-xs"><a href="/a">Slovan</a></div>
    </div>
    <div class="game__scorebox__team">
      <div class="game__scorebox__team-name hidden-xs"><a href="/b">DAC</a></div>
    </div>
  </div>
</body></html>
"""


# ---------------------------------------------------------------------------
# _to_num
# ---------------------------------------------------------------------------

class TestToNum:
    def test_integer_string(self):
        assert _to_num("14") == 14

    def test_float_string(self):
        assert _to_num("3.5") == pytest.approx(3.5)

    def test_integer_float(self):
        assert _to_num("4.0") == 4

    def test_empty_string_returns_none(self):
        assert _to_num("") is None

    def test_none_returns_none(self):
        assert _to_num(None) is None

    def test_comma_decimal(self):
        assert _to_num("3,5") == pytest.approx(3.5)


# ---------------------------------------------------------------------------
# _parse_combo
# ---------------------------------------------------------------------------

class TestParseCombo:
    def test_slash_format(self):
        shots, sot = _parse_combo("12/5")
        assert shots == 12
        assert sot == 5

    def test_single_value(self):
        shots, sot = _parse_combo("7")
        assert shots == 7
        assert sot is None

    def test_with_spaces(self):
        shots, sot = _parse_combo("10 / 4")
        assert shots == 10
        assert sot == 4


# ---------------------------------------------------------------------------
# parse_match — meta
# ---------------------------------------------------------------------------

class TestParseMeta:
    def test_extracts_team_names(self):
        result = parse_match(MINIMAL_HTML, "http://test.url")
        assert result["meta"]["home_team"] == "Slovan Praha"
        assert result["meta"]["away_team"] == "AC Sparta Praha"

    def test_extracts_score(self):
        result = parse_match(MINIMAL_HTML, "")
        assert result["meta"]["home_score"] == 2
        assert result["meta"]["away_score"] == 1

    def test_extracts_halftime_score(self):
        result = parse_match(MINIMAL_HTML, "")
        assert result["meta"]["home_score_ht"] == 1
        assert result["meta"]["away_score_ht"] == 0

    def test_extracts_referee(self):
        result = parse_match(MINIMAL_HTML, "")
        assert result["meta"]["referee"] == "Jan Novák"

    def test_extracts_date(self):
        result = parse_match(MINIMAL_HTML, "")
        assert "2026" in result["meta"]["date"]

    def test_no_score_when_not_played(self):
        result = parse_match(HTML_NO_SCORE, "")
        assert "home_score" not in result["meta"]

    def test_url_stored_in_result(self):
        result = parse_match(MINIMAL_HTML, "http://example.com/zapas/1234")
        assert result["url"] == "http://example.com/zapas/1234"


# ---------------------------------------------------------------------------
# parse_match — stats
# ---------------------------------------------------------------------------

class TestParseStats:
    def test_parses_total_stats(self):
        result = parse_match(HTML_WITH_STATS, "")
        assert "total" in result["stats"]

    def test_fouls_correct_values(self):
        result = parse_match(HTML_WITH_STATS, "")
        fouls = result["stats"]["total"]["fouls"]
        assert fouls["home"] == 14
        assert fouls["away"] == 11

    def test_corners_correct_values(self):
        result = parse_match(HTML_WITH_STATS, "")
        corners = result["stats"]["total"]["corners"]
        assert corners["home"] == 6
        assert corners["away"] == 4

    def test_shots_combo_split(self):
        result = parse_match(HTML_WITH_STATS, "")
        stats = result["stats"]["total"]
        assert stats["shots"]["home"] == 12
        assert stats["shots"]["away"] == 8
        assert stats["shots_on_target"]["home"] == 5
        assert stats["shots_on_target"]["away"] == 3

    def test_empty_stats_on_no_stats_html(self):
        result = parse_match(MINIMAL_HTML, "")
        assert result["stats"] == {}


# ---------------------------------------------------------------------------
# parse_match — cards from events injected into stats
# ---------------------------------------------------------------------------

class TestCardsFromEvents:
    def test_yellow_cards_counted_from_events(self):
        result = parse_match(HTML_WITH_CARDS_IN_EVENTS, "")
        yc = result["stats"]["total"]["yellow_cards"]
        assert yc["home"] == 2
        assert yc["away"] == 0

    def test_red_cards_counted_from_events(self):
        result = parse_match(HTML_WITH_CARDS_IN_EVENTS, "")
        rc = result["stats"]["total"]["red_cards"]
        assert rc["home"] == 0
        assert rc["away"] == 1

    def test_card_events_in_events_list(self):
        result = parse_match(HTML_WITH_CARDS_IN_EVENTS, "")
        yellow = [e for e in result["events"] if e["type"] == "yellow_card"]
        red = [e for e in result["events"] if e["type"] == "red_card"]
        assert len(yellow) == 2
        assert len(red) == 1


# ---------------------------------------------------------------------------
# parse_match — výsledková štruktúra
# ---------------------------------------------------------------------------

class TestParseMatchStructure:
    def test_result_has_required_keys(self):
        result = parse_match(MINIMAL_HTML, "")
        assert "url" in result
        assert "meta" in result
        assert "events" in result
        assert "stats" in result
        assert "lineups" in result

    def test_lineups_have_home_away(self):
        result = parse_match(MINIMAL_HTML, "")
        assert "home" in result["lineups"]
        assert "away" in result["lineups"]

    def test_events_is_list(self):
        result = parse_match(MINIMAL_HTML, "")
        assert isinstance(result["events"], list)


# ---------------------------------------------------------------------------
# STAT_MAP
# ---------------------------------------------------------------------------

class TestStatMap:
    def test_fauly_mapped(self):
        assert "Fauly" in STAT_MAP
        assert STAT_MAP["Fauly"] == "fouls"

    def test_rohy_mapped(self):
        assert "Rohy" in STAT_MAP
        assert STAT_MAP["Rohy"] == "corners"

    def test_shots_combo_mapped(self):
        assert "Střely/na branku" in STAT_MAP
        assert STAT_MAP["Střely/na branku"] == "shots_combo"

    def test_required_model_stats_covered(self):
        mapped = set(STAT_MAP.values())
        required = {"fouls", "corners", "shots_on_target"}
        assert required.issubset(mapped | {"shots_combo"})
