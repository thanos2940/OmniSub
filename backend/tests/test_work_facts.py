"""Sonarr/Radarr facts a new show's context is built from (media_sync_engine.work_facts)."""
from integrations.media_sync_engine import work_facts
from services.project_bootstrap import project_brief


def test_series_facts_reach_the_bootstrap_brief():
    series = {"title": "Fruits Basket", "year": 2019, "genres": ["Animation", "Drama", "Romance"],
              "overview": "  Tohru Honda   lives in a tent.\nThe Soma family hides a curse. "}
    facts = work_facts(series)
    assert facts == {"arr_year": 2019, "arr_genres": ["Animation", "Drama", "Romance"],
                     "arr_overview": "Tohru Honda lives in a tent. The Soma family hides a curse."}
    brief = project_brief({**facts, "arr_media_type": "series"})
    assert brief["year"] == 2019 and brief["genres"][0] == "Animation" and "Soma" in brief["overview"]
    assert brief["movie"] is False


def test_missing_or_odd_fields_are_left_out():
    assert work_facts({"title": "X", "year": 0, "genres": [None, ""], "overview": None}) == {}
    assert len(work_facts({"overview": "a" * 5000})["arr_overview"]) == 1500
