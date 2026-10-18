"""Plex client and autopilot notifier (offline: httpx MockTransport, fake sender/clock)."""

from datetime import datetime, timedelta, timezone

import httpx

from services import discord_notifier
from services.autopilot.notify import AutopilotNotifier
from services.autopilot.plex import PlexClient, plex_from_config

SECTIONS = {"MediaContainer": {"Directory": [
    {"key": "1", "title": "TV", "type": "show", "Location": [{"path": "D:\\Media\\TV"}]},
    {"key": "2", "title": "Anime", "type": "show", "Location": [{"path": "D:\\Media\\TV\\Anime"}]},
    {"key": "3", "title": "Movies", "type": "movie", "Location": [{"path": "D:\\Media\\Movies"}, {"path": "E:\\Films"}]},
]}}


def _plex(requests):
    def handler(request: httpx.Request):
        requests.append(request)
        if request.url.path == "/library/sections":
            return httpx.Response(200, json=SECTIONS)
        if request.url.path.endswith("/refresh"):
            return httpx.Response(200)
        return httpx.Response(404)
    return PlexClient("http://plex:32400/", "TOKEN", transport=httpx.MockTransport(handler))


class TestPlex:
    def test_sections_parse_and_headers(self):
        reqs = []
        sections = _plex(reqs).sections()
        assert [s["key"] for s in sections] == ["1", "2", "3"]
        assert sections[2]["locations"] == ["D:\\Media\\Movies", "E:\\Films"]
        assert reqs[0].headers["X-Plex-Token"] == "TOKEN"
        assert reqs[0].headers["Accept"] == "application/json"

    def test_longest_prefix_and_case_insensitive(self):
        client = _plex([])
        assert client.section_for("d:\\media\\tv\\Anime\\Show\\S01")["key"] == "2"
        assert client.section_for("D:\\Media\\TV\\Show")["key"] == "1"
        assert client.section_for("e:\\films\\A (2020)")["key"] == "3"
        assert client.section_for("D:\\Media\\TVX\\Show") is None  # not a path-prefix match
        assert client.section_for("C:\\Other") is None

    def test_refresh_path_uses_folder_of_file(self):
        reqs = []
        client = _plex(reqs)
        assert client.refresh_path("D:\\Media\\Movies\\Film (2020)\\Film.mkv") is True
        refresh = reqs[-1]
        assert refresh.url.path == "/library/sections/3/refresh"
        assert refresh.url.params["path"] == "D:\\Media\\Movies\\Film (2020)"

    def test_refresh_without_section_or_on_error_returns_false(self):
        assert _plex([]).refresh_path("C:\\Nowhere\\a.mkv") is False
        broken = PlexClient("http://plex", "T", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        assert broken.sections() == []
        assert broken.refresh_path("D:\\Media\\TV\\a.mkv") is False

    def test_connection_error_never_raises(self):
        def boom(request):
            raise httpx.ConnectError("down")
        client = PlexClient("http://plex", "T", transport=httpx.MockTransport(boom))
        assert client.sections() == []
        assert client.refresh_path("D:\\Media\\TV\\a.mkv") is False
        assert "down" in client.last_error

    def test_from_config(self):
        assert plex_from_config({}) is None
        assert plex_from_config({"plex_refresh_enabled": True, "plex_token": ""}) is None
        assert plex_from_config({"plex_refresh_enabled": False, "plex_token": "t"}) is None
        c = plex_from_config({"plex_refresh_enabled": True, "plex_token": "t"})
        assert c.url == "http://localhost:32400" and c.token == "t"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


def _notifier(cfg=None, clock=None):
    sent = []
    config = {"discord_webhook_url": "https://discord.example/hook"}
    config.update(cfg or {})
    n = AutopilotNotifier(config_provider=lambda: config, clock=clock or Clock(),
                          sender=lambda url, embed: sent.append((url, embed)) or True)
    return n, sent


EP = {"id": "a1", "kind": "episode", "title": "The Show", "project_name": "The Show", "season": 1, "episode": 2}


class TestNotifier:
    def test_no_webhook_is_a_noop(self):
        n, sent = _notifier({"discord_webhook_url": ""})
        assert n.item_failed(EP) is False and n.daily_digest({"done": 1}) is False
        assert sent == []

    def test_item_done_only_when_enabled(self):
        n, sent = _notifier()
        assert n.item_done(EP) is False
        n, sent = _notifier({"autopilot_notify_success": True})
        assert n.item_done(EP) is True
        assert "The Show S01E02" in sent[0][1]["description"]

    def test_failure_once_per_item(self):
        clock = Clock()
        n, sent = _notifier(clock=clock)
        assert n.item_failed({**EP, "detail": "media_missing"}) is True
        clock.now += timedelta(days=2)
        assert n.item_failed(EP) is False
        assert n.item_failed({**EP, "id": "b2"}) is True
        assert "media_missing" in sent[0][1]["description"] and "Retry" in sent[0][1]["description"]

    def test_no_source_once_per_series_per_day(self):
        clock = Clock()
        n, sent = _notifier(clock=clock)
        assert n.no_source({**EP, "reason": "image_only"}) is True
        assert n.no_source({**EP, "id": "b2", "episode": 3, "reason": "image_only"}) is False
        assert n.no_source({**EP, "id": "c3", "project_name": "Other", "title": "Other"}) is True
        clock.now += timedelta(days=1)
        assert n.no_source({**EP, "id": "d4"}) is True
        assert "image-based" in sent[0][1]["description"]

    def test_rate_limit_per_event_and_key(self):
        clock = Clock()
        n, sent = _notifier(clock=clock)
        assert n.arr_unreachable("sonarr", "refused", "12:00") is True
        assert n.arr_unreachable("sonarr", "refused", "12:00") is False
        assert n.arr_unreachable("radarr", "refused", "12:00") is True
        clock.now += timedelta(hours=7)
        assert n.arr_unreachable("sonarr", "refused", "12:00") is True
        assert n.engine_missing(3) is True and n.engine_missing(3) is False
        clock.now += timedelta(hours=7)
        assert n.engine_missing(3) is True
        assert n.runner_problem("auth_expired", "sign in again") is True
        assert n.runner_problem("auth_expired", "sign in again") is False

    def test_arr_recovered_only_after_alert(self):
        n, sent = _notifier()
        assert n.arr_recovered("sonarr") is False
        n.arr_unreachable("sonarr", "refused", "12:00")
        assert n.arr_recovered("sonarr") is True
        assert n.arr_recovered("sonarr") is False

    def test_foreign_repaired(self):
        n, sent = _notifier()
        assert n.foreign_repaired(EP, "D:\\x.el.srt") is True
        assert "backup" in sent[0][1]["description"]

    def test_digest_text(self):
        n, sent = _notifier()
        stats = {"done": 4, "has_greek": 7, "skipped": 2, "failed": 1, "no_source": 1,
                 "attention": ["The Show S01E02 failed: media_missing"]}
        assert n.daily_digest(stats) is True
        text = sent[0][1]["description"]
        assert "4 translated" in text and "7 already had Greek" in text and "1 failed" in text
        assert "Needs attention" in text and "media_missing" in text
        assert n.daily_digest(stats) is False  # same day
        quiet, qsent = _notifier()
        quiet.daily_digest({"done": 1})
        assert "Nothing needs your attention" in qsent[0][1]["description"]

    def test_failed_delivery_can_be_retried_and_never_raises(self):
        calls = []

        def flaky(url, embed):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return True
        n = AutopilotNotifier(config_provider=lambda: {"discord_webhook_url": "u"}, clock=Clock(), sender=flaky)
        assert n.item_failed(EP) is False
        assert n.item_failed(EP) is True

    def test_default_sender_posts_embed(self, monkeypatch):
        posted = {}

        def fake_post(url, json=None, timeout=None):
            posted.update(url=url, json=json)
            return httpx.Response(204)
        monkeypatch.setattr(discord_notifier.httpx, "post", fake_post)
        n = AutopilotNotifier(config_provider=lambda: {"discord_webhook_url": "https://d/h"}, clock=Clock())
        assert n.engine_missing(2) is True
        assert posted["url"] == "https://d/h" and posted["json"]["embeds"][0]["title"].startswith("No translation")
