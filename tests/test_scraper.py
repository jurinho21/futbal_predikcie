"""
test_scraper.py — unit testy pre nikeliga_scraper (parsing bez sieťových požiadaviek)
"""
import pytest
from nikeliga_scraper import parse_match, _to_num, STAT_MAP

# ---------------------------------------------------------------------------
# Minimálne HTML fixtures
# ---------------------------------------------------------------------------

MINIMAL_HTML = """
<html><body>
  <div class="game__scoreboard__team--home">
    <div class="game__scoreboard__name"><span class="hidden-xs">Slovan Bratislava</span></div>
  </div>
  <div class="game__scoreboard__team--away">
    <div class="game__scoreboard__name"><span class="hidden-xs">FC DAC 1904</span></div>
  </div>
  <div class="game__scoreboard__fulltime">2 : 1</div>
  <div class="game__scoreboard__halftime">1 : 0</div>
  <div class="game__scoreboard__date hidden-xs">sobota 10.05.2026, 18:00</div>
  <div class="game__additional">
    <div>Rozhodcovia: Lukáš Dzivjak, Pomocník A, Pomocník B</div>
    <div>5 000 divákov</div>
  </div>
</body></html>
"""

HTML_WITH_STATS = """
<html><body>
  <div class="game__scoreboard__team--home">
    <div class="game__scoreboard__name"><span class="hidden-xs">Slovan</span></div>
  </div>
  <div class="game__scoreboard__team--away">
    <div class="game__scoreboard__name"><span class="hidden-xs">Sparta</span></div>
  </div>
  <div class="game__scoreboard__fulltime">1 : 0</div>
  <div id="stage_3">
    <div class="hidden-xs">
      <div class="stats-container">
        <div class="stats-name">Fauly</div>
        <div class="stats right"><span class="value">14</span></div>
        <div class="stats"><span class="value">11</span></div>
      </div>
      <div class="stats-container">
        <div class="stats-name">Rohové kopy</div>
        <div class="stats right"><span class="value">6</span></div>
        <div class="stats"><span class="value">4</span></div>
      </div>
    </div>
  </div>
</body></html>
"""

HTML_NO_SCORE = """
<html><body>
  <div class="game__scoreboard__team--home">
    <div class="game__scoreboard__name"><span class="hidden-xs">Slovan</span></div>
  </div>
  <div class="game__scoreboard__team--away">
    <div class="game__scoreboard__name"><span class="hidden-xs">DAC</span></div>
  </div>
  <div class="game__scoreboard__date hidden-xs">nedeľa 15.06.2026, 17:00</div>
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
        assert _to_num("4.0") == 4  # 4.0 == int(4.0) → vráti int

    def test_empty_string_returns_none(self):
        assert _to_num("") is None

    def test_invalid_string_returns_unchanged(self):
        result = _to_num("abc")
        assert result == "abc"

    def test_comma_decimal(self):
        assert _to_num("3,5") == pytest.approx(3.5)


# ---------------------------------------------------------------------------
# parse_match — meta
# ---------------------------------------------------------------------------

class TestParseMeta:
    def test_extracts_team_names(self):
        result = parse_match(MINIMAL_HTML, "http://test.url")
        assert result["meta"]["home_team"] == "Slovan Bratislava"
        assert result["meta"]["away_team"] == "FC DAC 1904"

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
        assert result["meta"]["referee"] == "Lukáš Dzivjak"

    def test_extracts_date(self):
        result = parse_match(MINIMAL_HTML, "")
        assert "2026" in result["meta"]["date"]

    def test_no_score_when_match_not_played(self):
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
        stats = result["stats"]
        assert "total" in stats

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

    def test_empty_stats_on_no_stats_html(self):
        result = parse_match(MINIMAL_HTML, "")
        assert result["stats"] == {}


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
# STAT_MAP kontrola
# ---------------------------------------------------------------------------

class TestStatMap:
    def test_fouls_mapped(self):
        assert "Fauly" in STAT_MAP
        assert STAT_MAP["Fauly"] == "fouls"

    def test_all_model_stats_in_stat_map(self):
        required = {"fouls", "shots_on_target", "corners", "yellow_cards"}
        mapped = set(STAT_MAP.values())
        assert required.issubset(mapped), f"Chýbajúce štatistiky: {required - mapped}"
