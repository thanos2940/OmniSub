"""Autopilot pipeline (services/autopilot/pipeline.py) and the shared media inspection.

Offline: a temp media folder with dummy videos and sidecars, container probes faked at
``embedded_subs.probe_subtitle_tracks``, the real (isolated) translation queue, and the
real sync engine for routing. The translation/extraction workers are played by the tests.
"""

import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import services.autopilot as autopilot_pkg
from integrations import embedded_subs
from integrations.media_sync_engine import MediaSyncEngine
from integrations.path_resolver import PathResolver
from integrations.sonarr import SonarrConfig
from services.autopilot import media_inspect
from services.autopilot.pipeline import AutopilotPipeline
from services.autopilot.store import AutopilotStore, iso_utc
from utils import paths, storage
from utils.translation_queue import KIND_EXTRACTION, KIND_TRANSLATION, TranslationQueue

SRT = "1\n00:00:01,000 --> 00:00:04,000\nHello, Mark.\n\n2\n00:00:05,000 --> 00:00:08,000\nWelcome to Lumon.\n"
ASS = ("[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname\nStyle: Default,Arial\n\n"
       "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
       "Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,Hello, Mark.\n")
GREEK_LINES = ["Καλημέρα σας, πώς είστε σήμερα;", "Δεν ξέρω τι να σου πω, φίλε μου.",
               "Πού πηγαίνεις τόσο νωρίς το πρωί;"]
GREEK_SRT = "".join(f"{i}\r\n00:00:0{i},000 --> 00:00:0{i + 1},000\r\n{c}\r\n\r\n"
                    for i, c in enumerate(GREEK_LINES, 1))


def track(index, codec, lang="eng", title="", frames=400, **kw):
    return embedded_subs.SubtitleTrack(index=index, codec=codec, language=lang, title=title, frames=frames, **kw)


class Clock:
    """Real time plus an offset the tests can move, so file mtimes and the clock agree."""

    def __init__(self):
        self.offset = timedelta(0)

    def __call__(self):
        return datetime.now(timezone.utc) + self.offset

    def advance(self, **kw):
        self.offset += timedelta(**kw)


class Env:
    pass


@pytest.fixture(autouse=True)
def empty_queue():
    def wipe():
        with TranslationQueue()._conn() as conn:
            conn.execute("DELETE FROM translation_queue")
            conn.commit()
    wipe()
    yield
    wipe()


@pytest.fixture
def env(tmp_path, monkeypatch):
    import services.queue_service as queue_service
    monkeypatch.setattr(queue_service, "_wake_worker", lambda: None)

    e = Env()
    e.clock = Clock()
    e.media = tmp_path / "tv" / "Season 01"
    e.media.mkdir(parents=True)
    e.tracks = {}          # video file name -> [SubtitleTrack]
    e.tools = True
    e.facts = {"title": "Show", "original_language": "English", "tags": [], "monitored": True,
               "series_type": "standard"}
    e.events = []
    e.registered = []
    e.fail_events = False

    storage.save_global_config({"embedded_extraction_enabled": True, "default_target_language": "Greek",
                                "arr_source_language": "en"})
    monkeypatch.setattr(embedded_subs, "resolve_tools",
                        lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe") if e.tools else None)
    monkeypatch.setattr(embedded_subs, "probe_subtitle_tracks",
                        lambda path, *a, **k: list(e.tracks.get(Path(path).name, [])))
    monkeypatch.setattr("utils.media_probe_cache.get", lambda *a, **k: None)
    monkeypatch.setattr("utils.media_probe_cache.put", lambda *a, **k: None)

    ownership = types.ModuleType("services.autopilot.ownership")
    ownership.register_file = lambda path, project, episode: e.registered.append((path, project, episode))
    e.ours = set()  # files the exporter "wrote" (the test workers add them)
    ownership.is_ours = lambda path, meta=None: str(path) in e.ours
    e.forgotten = []
    ownership.forget = lambda path: e.forgotten.append(str(path))
    monkeypatch.setitem(sys.modules, "services.autopilot.ownership", ownership)
    monkeypatch.setattr(autopilot_pkg, "ownership", ownership, raising=False)

    e.store = AutopilotStore(tmp_path / "ap.db", clock=e.clock)

    def engine():
        return MediaSyncEngine(
            sonarr_config=SonarrConfig(base_url="http://sonarr", api_key="k", enabled=True), radarr_config=None,
            resolver=PathResolver(), source_lang_code="en", target_lang_code="el", target_language="Greek",
            embedded_extraction=True)

    def on_event(kind, item, **data):
        e.events.append((kind, item["state"], data))
        if e.fail_events:
            raise RuntimeError("integration down")

    e.pipeline = AutopilotPipeline(e.store, engine_factory=engine, media_info=lambda item: dict(e.facts),
                                   clock=e.clock, on_event=on_event)

    def make(name="Show.S01E01.mkv", sidecars=(), tracks=(), episode=1, file_id=None, project="Show", **kw):
        video = e.media / name
        video.write_bytes(b"\x1aE\xdf\xa3")
        for suffix, content in sidecars:
            data = content if isinstance(content, bytes) else content.encode("utf-8")
            (e.media / f"{video.stem}{suffix}").write_bytes(data)
        e.tracks[name] = list(tracks)
        item, _ = e.store.upsert_detection(
            source="sonarr", kind="episode", arr_item_id=100 + episode, arr_parent_id=7,
            arr_file_id=file_id or 200 + episode, media_path=str(video), project_name=project,
            episode_name=f"S01E{episode:02d}", title="Show", season=1, episode=episode,
            detected_at=e.clock() - timedelta(minutes=10), grace_seconds=300, **kw)
        return item

    e.make = make
    e.queue = TranslationQueue()

    async def run(item):
        return await e.pipeline.process_item(item)

    e.run = run
    e.kinds = lambda: [k for k, _, _ in e.events]
    return e


# --- shared inspection ----------------------------------------------------------

def test_inspect_lists_every_greek_sidecar_variant(env):
    names = ["Show.S01E01.el.srt", "Show.S01E01.ell.ass", "Show.S01E01.gre.forced.srt",
             "Show.S01E01.greek.sdh.srt", "Show.S01E01.gr.srt", "Show.S01E01.el.vtt"]
    item = env.make(sidecars=[(n[len("Show.S01E01"):], SRT) for n in names] + [(".en.srt", SRT), (".fr.srt", SRT)])
    info = media_inspect.inspect(item["media_path"], probe=False)
    assert sorted(Path(p).name for p in info["target_paths"]) == sorted(names)
    assert info["has_source"] and [Path(p).name for p in info["source_paths"]] == ["Show.S01E01.en.srt"]


def test_inspect_ignores_other_languages_and_other_videos(env):
    item = env.make(sidecars=[(".en.srt", SRT), (".es.srt", SRT), (".elx.srt", SRT)])
    (env.media / "Show.S01E02.el.srt").write_text(SRT)
    (env.media / "Show.S01E010.el.srt").write_text(SRT)
    assert media_inspect.inspect(item["media_path"], probe=False)["target_paths"] == []


@pytest.mark.parametrize("sidecars,tracks,kind", [
    ([(".en.ass", ASS), (".en.srt", SRT)], [track(2, "ass"), track(3, "subrip")], "embedded_ass"),
    ([(".en.ass", ASS)], [track(3, "subrip")], "embedded_srt"),
    ([(".en.ass", ASS), (".en.srt", SRT)], [], "external_ass"),
    ([(".en.srt", SRT)], [track(3, "hdmv_pgs_subtitle")], "external_srt"),
])
def test_source_choice_order(env, sidecars, tracks, kind):
    item = env.make(sidecars=sidecars, tracks=tracks)
    choice = media_inspect.inspect(item["media_path"], probe=True)["source_choice"]
    assert choice["kind"] == kind
    assert ("track" in choice) == kind.startswith("embedded") and ("path" in choice) == kind.startswith("external")


def test_inspect_reports_embedded_greek_and_why_there_is_no_source(env):
    item = env.make(tracks=[track(2, "subrip", lang="ell"), track(3, "hdmv_pgs_subtitle", lang="eng")])
    info = media_inspect.inspect(item["media_path"], probe=True)
    assert info["embedded_target_kind"] == "text" and info["embedded_target"] == "text"
    assert info["source_choice"] is None and info["reason"] == "no_source_language"
    env.tracks[Path(item["media_path"]).name] = [track(2, "hdmv_pgs_subtitle", lang="gre")]
    assert media_inspect.inspect(item["media_path"], probe=True)["embedded_target_kind"] == "image"
    env.tracks[Path(item["media_path"]).name] = []
    assert media_inspect.inspect(item["media_path"], probe=True)["reason"] == "no_subtitles"
    env.tools = False
    assert media_inspect.inspect(item["media_path"], probe=True)["reason"] == "ffmpeg_unavailable"
    assert media_inspect.inspect(str(env.media / "nope.mkv"))["reason"] == "media_unreachable"


# --- skip rules -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skip_rules(env):
    cases = [
        ({"original_language": "Greek"}, {}, "original_greek"),
        ({"tags": ["Other", "No-Greek"]}, {}, "skip_tag"),
        ({"tags": ["ombisub-skip"]}, {}, "skip_tag"),
        ({"monitored": False}, {}, "unmonitored"),
    ]
    for n, (facts, _, reason) in enumerate(cases, start=1):
        env.facts = {**env.facts, "original_language": "English", "tags": [], "monitored": True, **facts}
        item = env.make(f"Show.S01E{n:02d}.mkv", sidecars=[(".en.srt", SRT)], episode=n)
        done = await env.run(item)
        assert (done["state"], done["reason"]) == ("skipped", reason)
    assert env.kinds() == ["skipped"] * 4 and env.events[0][2] == {"reason": "original_greek"}


@pytest.mark.asyncio
async def test_disabled_project_is_skipped(env):
    storage.create_project("Show", {"show_name": "Show", "target_language": "Greek", "arr_disabled": True})
    done = await env.run(env.make(sidecars=[(".en.srt", SRT)]))
    assert (done["state"], done["reason"]) == ("skipped", "arr_disabled")


@pytest.mark.asyncio
async def test_skip_rules_can_be_switched_off(env):
    storage.save_global_config({"autopilot_skip_original_greek": False, "autopilot_monitored_only": False,
                                "autopilot_skip_tags": ""})
    env.facts.update(original_language="Greek", monitored=False, tags=["no-greek"])
    done = await env.run(env.make(sidecars=[(".en.srt", SRT)]))
    assert done["state"] == "translating"


@pytest.mark.asyncio
async def test_media_info_failure_is_retried_not_skipped(env):
    def broken(item):
        raise RuntimeError("sonarr unreachable")
    env.pipeline._media_info = broken
    done = await env.run(env.make(sidecars=[(".en.srt", SRT)]))
    assert done["state"] == "waiting" and done["attempts"] == 1 and "sonarr unreachable" in done["detail"]


# --- Greek check ------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [".el.srt", ".ell.ass", ".gre.forced.srt", ".greek.sdh.srt", ".gr.srt"])
async def test_greek_sidecar_variants_stand_the_autopilot_down(env, suffix):
    item = env.make(sidecars=[(".en.srt", SRT), (suffix, SRT)])
    done = await env.run(item)
    assert (done["state"], done["reason"]) == ("has_greek", "greek_sidecar")
    assert done["outputs"] == [] and env.queue.get_all() == []
    assert env.kinds() == ["has_greek"]


@pytest.mark.asyncio
async def test_embedded_greek_text_or_image_counts(env):
    for n, (codec, kind) in enumerate([("subrip", "text"), ("hdmv_pgs_subtitle", "image")], start=1):
        item = env.make(f"Show.S01E0{n}.mkv", tracks=[track(2, codec, lang="gre"), track(3, "ass")], episode=n)
        done = await env.run(item)
        assert (done["state"], done["reason"]) == ("has_greek", f"embedded_greek_{kind}")


@pytest.mark.asyncio
async def test_embedded_greek_is_ignored_when_the_setting_is_off(env):
    storage.save_global_config({"autopilot_count_embedded_greek": False})
    item = env.make(tracks=[track(2, "subrip", lang="gre"), track(3, "ass")])
    assert (await env.run(item))["state"] == "extracting"


@pytest.mark.asyncio
async def test_garbled_foreign_sidecar_is_repaired_with_a_backup(env):
    garbled = GREEK_SRT.encode("cp1253").decode("cp1252", errors="replace")
    original = codecs_bom() + garbled.encode("utf-8")
    item = env.make(sidecars=[(".el.srt", original), (".en.srt", SRT)])
    sidecar = env.media / "Show.S01E01.el.srt"
    done = await env.run(item)
    assert done["state"] == "has_greek" and "repaired" in done["detail"]
    fixed = sidecar.read_bytes()
    assert fixed == codecs_bom() + GREEK_SRT.encode("utf-8")  # BOM and CRLF kept, text fixed
    backup = Path(paths.DATA_DIR) / "autopilot" / "backups" / item["id"] / "Show.S01E01.el.srt"
    assert backup.read_bytes() == original
    assert env.kinds() == ["foreign_repaired", "has_greek"]
    assert env.events[0][2]["paths"] == [str(sidecar)]


@pytest.mark.asyncio
async def test_correct_greek_sidecar_is_left_untouched(env):
    content = codecs_bom() + GREEK_SRT.encode("utf-8")
    item = env.make(sidecars=[(".el.srt", content)])
    sidecar = env.media / "Show.S01E01.el.srt"
    before = sidecar.stat().st_mtime_ns
    done = await env.run(item)
    assert done["state"] == "has_greek" and sidecar.read_bytes() == content
    assert sidecar.stat().st_mtime_ns == before
    assert not (Path(paths.DATA_DIR) / "autopilot" / "backups").exists()
    assert env.kinds() == ["has_greek"]


@pytest.mark.asyncio
async def test_single_byte_greek_sidecar_is_converted_to_utf8(env):
    legacy = GREEK_SRT.encode("cp1253")
    item = env.make(sidecars=[(".el.srt", legacy)])
    await env.run(item)
    assert (env.media / "Show.S01E01.el.srt").read_bytes() == codecs_bom() + GREEK_SRT.encode("utf-8")


@pytest.mark.asyncio
async def test_repair_can_be_switched_off(env):
    garbled = GREEK_SRT.encode("cp1253").decode("cp1252", errors="replace").encode("utf-8")
    item = env.make(sidecars=[(".el.srt", garbled)])
    storage.save_global_config({"autopilot_repair_foreign_encoding": False})
    done = await env.run(item)
    assert done["state"] == "has_greek" and (env.media / "Show.S01E01.el.srt").read_bytes() == garbled


def codecs_bom():
    return b"\xef\xbb\xbf"


@pytest.mark.asyncio
async def test_existing_translation_of_the_same_source_is_re_exported(env):
    storage.create_project("Show", {"show_name": "Show", "target_language": "Greek"})
    item = env.make(sidecars=[(".en.srt", SRT)])
    fingerprint = MediaSyncEngine._fingerprint(str(env.media / "Show.S01E01.en.srt"))
    rows = [{"id": 1, "index": 1, "timecode": "00:00:01,000 --> 00:00:04,000", "original": "Hello, Mark.",
             "translated": "Γεια, Μαρκ.", "translations": {"el": "Γεια, Μαρκ."}}]
    storage.save_episode("Show", "S01E01", rows, {
        "arr_source": True, "translated": True, "translation_status": "completed",
        "original_filename": "Show.S01E01.old.en.srt", "arr_sub_fingerprint": fingerprint})
    done = await env.run(item)
    target = env.media / "Show.S01E01.el.srt"
    assert done["state"] == "done" and done["outputs"] == [str(target)]
    assert "Γεια" in target.read_text(encoding="utf-8-sig")
    assert env.queue.get_all() == [] and env.kinds() == ["done"]
    assert env.registered == [(str(target), "Show", "S01E01")]


@pytest.mark.asyncio
async def test_translation_of_a_different_source_is_not_reused(env):
    storage.create_project("Show", {"show_name": "Show", "target_language": "Greek"})
    item = env.make(sidecars=[(".en.srt", SRT)])
    rows = [{"id": 1, "index": 1, "timecode": "00:00:01,000 --> 00:00:04,000", "original": "x",
             "translated": "y", "translations": {"el": "y"}}]
    storage.save_episode("Show", "S01E01", rows, {
        "arr_source": True, "translated": True, "original_filename": "x.srt", "arr_sub_fingerprint": "0_0"})
    assert (await env.run(item))["state"] == "translating"
    assert not (env.media / "Show.S01E01.el.srt").exists()


# --- no source --------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("tracks,tools,reason", [
    ([track(2, "hdmv_pgs_subtitle"), track(3, "dvd_subtitle")], True, "image_only"),
    ([], True, "no_subtitles"),
    ([], False, "ffmpeg_missing"),
    ([track(2, "subrip", lang="spa")], True, "no_source_language"),
])
async def test_no_usable_source(env, tracks, tools, reason):
    env.tools = tools
    done = await env.run(env.make(tracks=tracks))
    assert (done["state"], done["reason"]) == ("no_source", reason)
    assert env.queue.get_all() == [] and env.kinds() == ["no_source"]
    assert env.events[0][2] == {"reason": reason}


# --- routing ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embedded_ass_is_routed_to_extraction_chained_into_translation(env):
    item = env.make(tracks=[track(2, "ass", title="Full"), track(3, "subrip")])
    routed = await env.run(item)
    assert (routed["state"], routed["source_kind"]) == ("extracting", "embedded_ass")
    assert routed["routed_at"] and routed["attempts"] == 0
    row = env.queue.find_item("Show", "S01E01", "", KIND_EXTRACTION)
    assert row["status"] == "pending" and row["priority"] == 0
    opts = json.loads(row["options"])
    assert opts["media_path"] == item["media_path"] and opts["stream_index"] == 2
    assert opts["output_format"] == "ass" and opts["auto_translate"] is False
    after = opts["translate_after"]
    assert after["lang"] == "" and after["options"]["target_language_name"] == "Greek"
    assert {"model", "ai_provider"} <= set(after["options"])
    assert opts["import_metadata"]["arr_sonarr_episode_id"] == 101
    assert opts["project_metadata"]["series_type"] == "standard"
    assert env.queue.find_item("Show", "S01E01", "", KIND_TRANSLATION) is None


@pytest.mark.asyncio
async def test_external_source_is_imported_and_queued_for_translation(env):
    item = env.make(sidecars=[(".en.srt", SRT)])
    routed = await env.run(item)
    assert (routed["state"], routed["source_kind"]) == ("translating", "external_srt")
    assert "Show" in storage.list_projects() and storage.original_subtitle_exists("Show", "S01E01")
    row = env.queue.find_item("Show", "S01E01", "", KIND_TRANSLATION)
    assert row["status"] == "pending" and row["priority"] == 0
    assert json.loads(row["options"])["target_language_name"] == "Greek"
    assert env.queue.find_item("Show", "S01E01", "", KIND_EXTRACTION) is None


@pytest.mark.asyncio
async def test_external_ass_is_preferred_over_srt(env):
    routed = await env.run(env.make(sidecars=[(".en.srt", SRT), (".en.ass", ASS)]))
    assert routed["source_kind"] == "external_ass"
    assert storage.load_original_subtitle("Show", "S01E01")[1].lstrip(".") == "ass"


@pytest.mark.asyncio
async def test_movie_item_routes_with_radarr_metadata(env):
    video = env.media / "Heat.1995.mkv"
    video.write_bytes(b"x")
    env.tracks[video.name] = [track(2, "subrip")]
    item, _ = env.store.upsert_detection(
        source="radarr", kind="movie", arr_item_id=60, arr_parent_id=None, arr_file_id=700,
        media_path=str(video), project_name="Heat (1995)", episode_name="Heat (1995)", title="Heat",
        season=None, episode=None, detected_at=env.clock() - timedelta(minutes=9), grace_seconds=300)
    routed = await env.run(item)
    assert routed["state"] == "extracting" and routed["source_kind"] == "embedded_srt"
    opts = json.loads(env.queue.find_item("Heat (1995)", "Heat (1995)", "", KIND_EXTRACTION)["options"])
    assert opts["import_metadata"] == {"arr_radarr_id": 60} and opts["project_metadata"]["type"] == "movie"


@pytest.mark.asyncio
async def test_extraction_lane_switched_off_parks_the_item_with_a_clear_reason(env):
    storage.save_global_config({"embedded_extraction_enabled": False})
    done = await env.run(env.make(tracks=[track(2, "ass")]))
    assert (done["state"], done["reason"]) == ("failed", "extraction_disabled")
    assert env.kinds() == ["failed"] and env.queue.get_all() == []


@pytest.mark.asyncio
async def test_a_fresh_project_asks_for_its_context_guide(env):
    await env.run(env.make(sidecars=[(".en.srt", SRT)]))
    assert json.loads(env.queue.find_item("Show", "S01E01", "", KIND_TRANSLATION)["options"])["ensure_context"] is True


@pytest.mark.asyncio
async def test_missing_media_is_retried_every_minute_then_fails(env):
    item = env.make(sidecars=[(".en.srt", SRT)])
    Path(item["media_path"]).unlink()
    for attempt in range(1, 15):
        item = await env.run(item)
        assert item["state"] == "waiting" and item["attempts"] == attempt
        assert item["due_at"] == iso_utc(env.clock() + timedelta(seconds=60))
    item = await env.run(item)
    assert (item["state"], item["reason"], item["attempts"]) == ("failed", "media_missing", 15)
    assert env.kinds() == ["failed"]


@pytest.mark.asyncio
async def test_media_that_arrives_in_time_is_processed(env):
    item = env.make(sidecars=[(".en.srt", SRT)])
    video = Path(item["media_path"])
    data = video.read_bytes()
    video.unlink()
    item = await env.run(item)
    assert item["state"] == "waiting"
    video.write_bytes(data)
    env.clock.advance(seconds=61)
    assert env.pipeline  # due again
    assert [i["id"] for i in env.store.due_items(env.clock())] == [item["id"]]
    assert (await env.pipeline.process_due()) == 1
    assert env.store.get(item["id"])["state"] == "translating"


@pytest.mark.asyncio
async def test_process_due_handles_only_due_items_and_respects_the_limit(env):
    for n in range(1, 4):
        env.make(f"Show.S01E0{n}.mkv", sidecars=[(".el.srt", GREEK_SRT)], episode=n)
    late, _ = env.store.upsert_detection(
        source="sonarr", kind="episode", arr_item_id=999, arr_parent_id=7, arr_file_id=999,
        media_path=str(env.media / "x.mkv"), project_name="Show", episode_name="S01E09", title="Show",
        season=1, episode=9, detected_at=env.clock(), grace_seconds=300)  # still in its grace period
    assert await env.pipeline.process_due(limit=2) == 2
    assert env.store.counts()["has_greek"] == 2
    assert await env.pipeline.process_due() == 1
    assert await env.pipeline.process_due() == 0
    assert env.store.get(late["id"])["state"] == "waiting"


@pytest.mark.asyncio
async def test_an_item_is_not_processed_twice_in_parallel(env):
    item = env.make(sidecars=[(".en.srt", SRT)])
    import asyncio
    first, second = await asyncio.gather(env.pipeline.process_item(item), env.pipeline.process_item(item))
    assert len(env.queue.get_all()) == 1 and "translating" in {first["state"], second["state"]}


# --- tracking ---------------------------------------------------------------------

def play_extraction_worker(env, project="Show", episode="S01E01", complete=True):
    """What process_extraction_item does after a successful extraction: complete the row, queue the translation."""
    row = env.queue.find_item(project, episode, "", KIND_EXTRACTION)
    opts = json.loads(row["options"])
    env.queue.update_status(row["id"], status="completed", completed_at=env.clock().isoformat())
    after = opts["translate_after"]
    from services.queue_service import enqueue_translation
    enqueue_translation(project, episode, row["priority"], after["options"], lang=after["lang"])


def play_translation_worker(env, files, project="Show", episode="S01E01", status="completed", error=None):
    row = env.queue.find_item(project, episode, "", KIND_TRANSLATION)
    for n, name in enumerate(files):
        (env.media / name).write_text(GREEK_SRT if name.endswith("srt") else ASS, encoding="utf-8-sig")
        now = env.clock().timestamp()
        os.utime(env.media / name, (now, now))  # file times follow the test clock
        env.ours.add(str(env.media / name))  # the exporter registers what it writes
        if n == 0:  # the exporter records the file it wrote on the episode
            meta = storage.load_episode_metadata(project, episode) or {}
            meta.update(arr_target_path=str(env.media / name), arr_has_target=True)
            storage.save_episode(project, episode, (storage.load_episode(project, episode) or {}).get("data", []), meta)
    env.queue.update_status(row["id"], status=status, error=error, completed_at=env.clock().isoformat())


@pytest.mark.asyncio
async def test_ass_item_is_done_when_both_greek_files_exist(env):
    item = env.make(tracks=[track(2, "ass")])
    await env.run(item)
    assert await env.pipeline.track_active() == 0  # extraction still pending: nothing changes
    assert env.store.get(item["id"])["state"] == "extracting"

    play_extraction_worker(env)
    assert await env.pipeline.track_active() == 0
    assert env.store.get(item["id"])["state"] == "translating"

    assert await env.pipeline.track_active() == 0  # translation pending
    play_translation_worker(env, ["Show.S01E01.el.ass"])  # only the ASS: the SRT companion is missing
    assert await env.pipeline.track_active() == 1
    assert env.store.get(item["id"])["state"] == "waiting"  # retried, not done

    # second run: the engine writes both
    item = env.store.get(item["id"])
    env.clock.advance(minutes=6)
    for name in ("Show.S01E01.el.ass", "Show.S01E01.el.srt"):
        (env.media / name).unlink(missing_ok=True)
    await env.pipeline.process_due()
    assert env.store.get(item["id"])["state"] == "extracting"
    play_extraction_worker(env)
    await env.pipeline.track_active()
    play_translation_worker(env, ["Show.S01E01.el.ass", "Show.S01E01.el.srt"])
    assert await env.pipeline.track_active() == 1
    done = env.store.get(item["id"])
    assert done["state"] == "done"
    expected = [str(env.media / "Show.S01E01.el.ass"), str(env.media / "Show.S01E01.el.srt")]
    assert done["outputs"] == expected
    assert [r[0] for r in env.registered] == expected and {r[1:] for r in env.registered} == {("Show", "S01E01")}
    assert env.kinds() == ["done"] and env.events[0][2]["outputs"] == expected


@pytest.mark.asyncio
async def test_srt_item_needs_only_the_srt(env):
    item = env.make(sidecars=[(".en.srt", SRT)])
    await env.run(item)
    play_translation_worker(env, ["Show.S01E01.el.srt"])
    assert await env.pipeline.track_active() == 1
    done = env.store.get(item["id"])
    assert done["state"] == "done" and done["outputs"] == [str(env.media / "Show.S01E01.el.srt")]


@pytest.mark.asyncio
async def test_output_older_than_the_routing_does_not_count(env):
    item = env.make(sidecars=[(".en.srt", SRT)])
    await env.run(item)
    stale = env.media / "Show.S01E01.el.srt"
    stale.write_text(GREEK_SRT, encoding="utf-8")
    old = (env.clock() - timedelta(hours=2)).timestamp()
    os.utime(stale, (old, old))
    row = env.queue.find_item("Show", "S01E01", "", KIND_TRANSLATION)
    env.queue.update_status(row["id"], status="completed", completed_at=env.clock().isoformat())
    await env.pipeline.track_active()
    assert env.store.get(item["id"])["state"] != "done"


@pytest.mark.asyncio
async def test_waiting_for_the_engine_is_not_a_failure(env):
    item = env.make(sidecars=[(".en.srt", SRT)])
    await env.run(item)
    row = env.queue.find_item("Show", "S01E01", "", KIND_TRANSLATION)
    for status in ("pending", "running", "paused_daily_limit"):
        env.queue.update_status(row["id"], status=status)
        env.clock.advance(hours=7)
        assert await env.pipeline.track_active() == 0
    current = env.store.get(item["id"])
    assert current["state"] == "translating" and current["attempts"] == 0 and env.events == []


@pytest.mark.asyncio
async def test_failed_translation_is_retried_with_backoff_then_parked(env):
    item = env.make(sidecars=[(".en.srt", SRT)])
    item = await env.run(item)
    delays = [300, 1800, 7200]
    for attempt, delay in enumerate(delays, start=1):
        play_translation_worker(env, [], status="failed", error="model exploded\ntrace")
        assert await env.pipeline.track_active() == 1
        item = env.store.get(item["id"])
        assert item["state"] == "waiting" and item["attempts"] == attempt
        assert item["due_at"] == iso_utc(env.clock() + timedelta(seconds=delay))
        assert "model exploded" in item["detail"] and "trace" not in item["detail"]
        assert env.store.due_items(env.clock()) == []
        env.clock.advance(seconds=delay + 1)
        item = await env.run(env.store.due_items(env.clock())[0])
        assert item["state"] == "translating"
    play_translation_worker(env, [], status="failed", error="still broken")
    await env.pipeline.track_active()
    item = env.store.get(item["id"])
    assert (item["state"], item["attempts"], item["reason"]) == ("failed", 4, "max_attempts")
    assert "still broken" in item["detail"] and env.kinds() == ["failed"]
    env.clock.advance(days=2)
    assert env.store.due_items(env.clock()) == []  # parked until someone retries it


@pytest.mark.asyncio
async def test_failed_extraction_is_retried(env):
    item = env.make(tracks=[track(2, "ass")])
    await env.run(item)
    row = env.queue.find_item("Show", "S01E01", "", KIND_EXTRACTION)
    env.queue.update_status(row["id"], status="failed", error="WinError 64")
    await env.pipeline.track_active()
    item = env.store.get(item["id"])
    assert item["state"] == "waiting" and "extraction failed" in item["detail"] and "WinError 64" in item["detail"]


@pytest.mark.asyncio
async def test_lost_translation_job_is_routed_again(env):
    item = env.make(sidecars=[(".en.srt", SRT)])
    await env.run(item)
    env.queue.delete_item(env.queue.find_item("Show", "S01E01", "", KIND_TRANSLATION)["id"])
    env.clock.advance(minutes=20)
    assert await env.pipeline.track_active() == 0  # not yet 30 minutes
    env.clock.advance(minutes=11)
    assert await env.pipeline.track_active() == 1
    waiting = env.store.get(item["id"])
    assert waiting["state"] == "waiting" and "vanished" in waiting["detail"]
    assert await env.pipeline.process_due() == 1
    assert env.store.get(item["id"])["state"] == "translating"
    assert env.queue.find_item("Show", "S01E01", "", KIND_TRANSLATION)["status"] == "pending"


@pytest.mark.asyncio
async def test_lost_extraction_job_is_routed_again(env):
    item = env.make(tracks=[track(2, "ass")])
    await env.run(item)
    env.queue.delete_item(env.queue.find_item("Show", "S01E01", "", KIND_EXTRACTION)["id"])
    env.clock.advance(minutes=31)
    assert await env.pipeline.track_active() == 1
    assert await env.pipeline.process_due() == 1
    assert env.store.get(item["id"])["state"] == "extracting"
    assert env.queue.find_item("Show", "S01E01", "", KIND_EXTRACTION) is not None


@pytest.mark.asyncio
async def test_extraction_that_never_hands_over_to_translation_is_routed_again(env):
    item = env.make(tracks=[track(2, "ass")])
    await env.run(item)
    row = env.queue.find_item("Show", "S01E01", "", KIND_EXTRACTION)
    env.queue.update_status(row["id"], status="completed", completed_at=env.clock().isoformat())
    assert await env.pipeline.track_active() == 0
    env.clock.advance(minutes=3)
    assert await env.pipeline.track_active() == 1
    assert env.store.get(item["id"])["state"] == "waiting"


@pytest.mark.asyncio
async def test_a_record_of_an_earlier_translation_does_not_complete_a_new_item(env):
    storage.create_project("Show", {"show_name": "Show", "target_language": "Greek"})
    old = env.queue.enqueue("Show", "S01E01", 0, {"x": 1}, "", KIND_TRANSLATION)
    env.queue.update_status(old, status="completed", completed_at=(env.clock() - timedelta(days=3)).isoformat())
    item = env.make(tracks=[track(2, "ass")])
    await env.run(item)  # extraction queued; the completed old row is dropped
    assert env.queue.find_item("Show", "S01E01", "", KIND_TRANSLATION) is None
    await env.pipeline.track_active()
    assert env.store.get(item["id"])["state"] == "extracting"


@pytest.mark.asyncio
async def test_foreign_greek_file_appearing_mid_translation_wins(env):
    item = env.make(sidecars=[(".en.srt", SRT)])
    await env.run(item)
    foreign = env.media / "Show.S01E01.el.srt"
    foreign.write_text(GREEK_SRT, encoding="utf-8")  # Bazarr, late; the exporter's guard refuses to overwrite it
    row = env.queue.find_item("Show", "S01E01", "", KIND_TRANSLATION)
    old = (env.clock() - timedelta(hours=1)).timestamp()
    os.utime(foreign, (old, old))
    env.queue.update_status(row["id"], status="completed", completed_at=env.clock().isoformat())
    await env.pipeline.track_active()
    done = env.store.get(item["id"])
    assert (done["state"], done["reason"]) == ("has_greek", "appeared_during_translation")
    assert "Show.S01E01.el.srt" in done["detail"] and env.kinds() == ["has_greek"]
    assert env.registered == []


@pytest.mark.asyncio
async def test_restart_resumes_tracking_from_the_queue(env, tmp_path):
    item = env.make(sidecars=[(".en.srt", SRT)])
    await env.run(item)
    env.store.close()
    reopened = AutopilotStore(tmp_path / "ap.db", clock=env.clock)
    assert reopened.recover() == 0
    pipeline = AutopilotPipeline(reopened, engine_factory=env.pipeline._engine_factory,
                                 media_info=lambda i: dict(env.facts), clock=env.clock)
    play_translation_worker(env, ["Show.S01E01.el.srt"])
    assert await pipeline.track_active() == 1
    assert reopened.get(item["id"])["state"] == "done"


@pytest.mark.asyncio
async def test_item_interrupted_while_checking_is_picked_up_after_recovery(env):
    item = env.make(sidecars=[(".en.srt", SRT)])
    env.store.transition(item["id"], "checking")
    assert env.store.due_items(env.clock()) == []
    assert env.store.recover() == 1
    assert await env.pipeline.process_due() == 1
    assert env.store.get(item["id"])["state"] == "translating"


# --- callbacks / registration ------------------------------------------------------

@pytest.mark.asyncio
async def test_a_failing_on_event_callback_never_breaks_the_pipeline(env):
    env.fail_events = True
    done = await env.run(env.make(sidecars=[(".el.srt", GREEK_SRT)]))
    assert done["state"] == "has_greek"


@pytest.mark.asyncio
async def test_async_callbacks_are_awaited(env):
    seen = []

    async def on_event(kind, item, **data):
        seen.append(kind)
    env.pipeline._on_event = on_event
    await env.run(env.make(sidecars=[(".el.srt", GREEK_SRT)]))
    assert seen == ["has_greek"]


@pytest.mark.asyncio
async def test_missing_ownership_module_does_not_block_completion(env, monkeypatch):
    monkeypatch.setitem(sys.modules, "services.autopilot.ownership", None)
    monkeypatch.delattr(autopilot_pkg, "ownership", raising=False)
    item = env.make(sidecars=[(".en.srt", SRT)])
    await env.run(item)
    play_translation_worker(env, ["Show.S01E01.el.srt"])
    await env.pipeline.track_active()
    assert env.store.get(item["id"])["state"] == "done"


@pytest.mark.asyncio
async def test_expected_outputs_use_the_exporters_naming(env):
    item = {"media_path": str(env.media / "Show.S01E01.1080p.WEB-DL.mkv"), "source_kind": "embedded_ass"}
    names = [p.name for p in AutopilotPipeline._expected_outputs(item, None)]
    assert names == ["Show.S01E01.1080p.WEB-DL.el.ass", "Show.S01E01.1080p.WEB-DL.el.srt"]
    item["source_kind"] = "external_srt"
    assert [p.name for p in AutopilotPipeline._expected_outputs(item, None)] == ["Show.S01E01.1080p.WEB-DL.el.srt"]
    assert [p.name for p in AutopilotPipeline._expected_outputs(item, {"original_filename": "x.en.ass"})] == [
        "Show.S01E01.1080p.WEB-DL.el.ass", "Show.S01E01.1080p.WEB-DL.el.srt"]


def test_translation_options_for_matches_the_webhook_options(env):
    from services.queue_service import enqueue_webhook_episode, translation_options_for
    storage.create_project("Show", {"show_name": "Show", "target_language": "Greek"})
    storage.save_episode("Show", "S01E01", [], {"arr_source": True})
    opts = translation_options_for("Show")
    assert {"model", "ai_provider", "temperature", "top_k", "top_p"} <= set(opts)
    assert opts["target_language_name"] == "Greek"
    assert translation_options_for("Nope")["target_language_name"] == "Greek"  # unknown project: global default
    (queued,) = enqueue_webhook_episode("Show", "S01E01", delay_seconds=0)
    sent = json.loads(env.queue.get_item(queued)["options"])
    assert sent == {k: v for k, v in opts.items() if v is not None}


# --- ownership-aware Greek check / blocked export ------------------------------------

@pytest.mark.asyncio
async def test_our_own_greek_sidecar_does_not_count_as_greek_present(env):
    item = env.make(sidecars=[(".en.srt", SRT), (".el.srt", GREEK_SRT)])
    env.ours.add(str(env.media / "Show.S01E01.el.srt"))
    done = await env.run(item)
    assert done["state"] == "translating"  # routed for a new translation, not has_greek
    assert env.kinds() == []


@pytest.mark.asyncio
async def test_our_sidecar_with_an_embedded_source_still_probes_the_container(env):
    item = env.make(tracks=[track(2, "ass")], sidecars=[(".el.ass", ASS)])
    env.ours.add(str(env.media / "Show.S01E01.el.ass"))
    assert (await env.run(item))["state"] == "extracting"


@pytest.mark.asyncio
async def test_our_sidecar_plus_foreign_one_still_stands_down(env):
    item = env.make(sidecars=[(".en.srt", SRT), (".el.srt", GREEK_SRT), (".gre.forced.srt", GREEK_SRT)])
    env.ours.add(str(env.media / "Show.S01E01.el.srt"))
    done = await env.run(item)
    assert (done["state"], done["reason"]) == ("has_greek", "greek_sidecar")
    assert done["detail"] == "Show.S01E01.gre.forced.srt"


@pytest.mark.asyncio
async def test_our_incomplete_outputs_are_completed_from_the_existing_translation(env):
    storage.create_project("Show", {"show_name": "Show", "target_language": "Greek"})
    item = env.make(sidecars=[(".en.ass", ASS), (".el.ass", ASS)])
    env.ours.add(str(env.media / "Show.S01E01.el.ass"))
    fingerprint = MediaSyncEngine._fingerprint(str(env.media / "Show.S01E01.en.ass"))
    rows = [{"id": 1, "index": 1, "timecode": "00:00:01,000 --> 00:00:04,000", "original": "Hello, Mark.",
             "translated": "Γεια, Μαρκ.", "translations": {"el": "Γεια, Μαρκ."},
             "_ass": {"kind": "dialogue", "translatable": True}}]
    storage.save_episode("Show", "S01E01", rows, {
        "arr_source": True, "translated": True, "translation_status": "completed",
        "original_filename": "Show.S01E01.en.ass", "arr_sub_fingerprint": fingerprint})
    done = await env.run(item)
    assert done["state"] == "done", done["detail"]
    assert (env.media / "Show.S01E01.el.srt").exists()  # the missing companion was written


@pytest.mark.asyncio
async def test_a_fresh_foreign_file_is_not_taken_for_our_output(env):
    item = env.make(sidecars=[(".en.srt", SRT)])
    await env.run(item)
    foreign = env.media / "Show.S01E01.el.srt"
    foreign.write_text(GREEK_SRT, encoding="utf-8")  # written after the routing: fresh, but not ours
    row = env.queue.find_item("Show", "S01E01", "", KIND_TRANSLATION)
    env.queue.update_status(row["id"], status="completed", completed_at=env.clock().isoformat())
    await env.pipeline.track_active()
    done = env.store.get(item["id"])
    assert (done["state"], done["reason"]) == ("has_greek", "appeared_during_translation")
    assert env.registered == [] and env.kinds() == ["has_greek"]


@pytest.mark.asyncio
async def test_blocked_export_ends_the_item_has_greek(env):
    """The exporter refused to write (translation_warning) because a foreign Greek file is there."""
    item = env.make(sidecars=[(".en.srt", SRT)])
    await env.run(item)
    (env.media / "Show.S01E01.el.srt").write_text(GREEK_SRT, encoding="utf-8")
    storage.save_episode("Show", "S01E01", [], {"translation_warning": "Not exported: protected"})
    row = env.queue.find_item("Show", "S01E01", "", KIND_TRANSLATION)
    env.queue.update_status(row["id"], status="completed", completed_at=env.clock().isoformat())
    assert await env.pipeline.track_active() == 1
    done = env.store.get(item["id"])
    assert (done["state"], done["reason"]) == ("has_greek", "appeared_during_translation")
    assert env.store.get(item["id"])["outputs"] == []


@pytest.mark.asyncio
async def test_srt_companion_from_a_translated_sibling_need_not_be_rewritten(env):
    item = env.make(tracks=[track(2, "ass")])
    await env.run(item)
    play_extraction_worker(env)
    await env.pipeline.track_active()
    # the sibling episode's own export wrote the .el.srt long before this routing
    srt = env.media / "Show.S01E01.el.srt"
    srt.write_text(GREEK_SRT, encoding="utf-8")
    old = (env.clock() - timedelta(days=3)).timestamp()
    os.utime(srt, (old, old))
    env.ours.add(str(srt))
    storage.save_episode("Show", "S01E01 [srt]", [{"original": "x", "translations": {"el": "y"}, "translated": "y"}],
                         {"arr_source": True})
    play_translation_worker(env, ["Show.S01E01.el.ass"])
    assert await env.pipeline.track_active() == 1
    assert env.store.get(item["id"])["state"] == "done"


# --- upgrades: the replaced video's Greek files ----------------------------------

def _replaced(env, old_outputs=(".el.ass", ".el.srt"), ours=None, delete_video=True):
    """A done item with our Greek files, then an upgrade under a new name (Sonarr deleted the old video)."""
    old = env.make("Show.S01E01.WEBDL.mkv")
    outputs = []
    for suffix in old_outputs:
        path = env.media / f"Show.S01E01.WEBDL{suffix}"
        path.write_text("Ελληνικά", encoding="utf-8")
        outputs.append(str(path))
    env.store.transition(old["id"], "done", outputs=outputs)
    env.ours.update(outputs if ours is None else ours)
    new = env.make("Show.S01E01.Bluray.mkv", file_id=999)
    if delete_video:
        (env.media / "Show.S01E01.WEBDL.mkv").unlink()
    return old, new, outputs


@pytest.mark.asyncio
async def test_upgrade_removes_our_files_of_the_replaced_video(env):
    old, new, outputs = _replaced(env)
    assert env.store.get(old["id"])["state"] == "superseded"
    await env.run(new)
    assert not any(Path(p).exists() for p in outputs)
    assert sorted(env.forgotten) == sorted(outputs)
    old = env.store.get(old["id"])
    assert old["state"] == "superseded" and old["outputs"] == []
    assert "removed its Greek files" in old["events"][-1]["message"]


@pytest.mark.asyncio
async def test_upgrade_keeps_a_replaced_file_someone_else_changed(env):
    old, new, outputs = _replaced(env, ours=[str(env.media / "Show.S01E01.WEBDL.el.ass")])
    await env.run(new)
    assert not Path(outputs[0]).exists()
    assert Path(outputs[1]).exists()  # Bazarr overwrote our .el.srt: it is theirs now


@pytest.mark.asyncio
async def test_upgrade_keeps_the_files_while_the_old_video_still_exists(env):
    old, new, outputs = _replaced(env, delete_video=False)
    await env.run(new)
    assert all(Path(p).exists() for p in outputs)
    assert env.store.get(old["id"])["outputs"] == outputs


@pytest.mark.asyncio
async def test_same_name_replacement_keeps_the_files(env):
    old = env.make("Show.S01E01.mkv")
    path = env.media / "Show.S01E01.el.srt"
    path.write_text("Ελληνικά", encoding="utf-8")
    env.store.transition(old["id"], "done", outputs=[str(path)])
    env.ours.add(str(path))
    new = env.make("Show.S01E01.mkv", file_id=999)  # Sonarr replaced the file in place
    await env.run(new)
    assert path.exists() and env.forgotten == []
