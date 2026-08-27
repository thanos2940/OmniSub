"""Embedded ASS extraction: track selection, probe parsing, sidecar naming, queue lanes.

Network- and ffmpeg-free — the subprocess seam is stubbed the same way the suite
stubs the LLM seam. See docs/PLAN_embedded_ass_extraction.md.
"""

import asyncio
import json

import pytest

from integrations import embedded_subs
from integrations.embedded_subs import SubtitleTrack, select_track
from utils.translation_queue import (
    TranslationQueue,
    KIND_EXTRACTION,
    KIND_TRANSLATION,
    PRIORITY_MANUAL,
)


def track(index, **kw):
    kw.setdefault("codec", "ass")
    kw.setdefault("language", "eng")
    return SubtitleTrack(index=index, **kw)


# ---------------------------------------------------------------------------
# Selection — the part that decides whether the user gets dialogue or signs
# ---------------------------------------------------------------------------

def test_full_dialogue_track_beats_signs_track():
    tracks = [
        track(0, title="Signs & Songs", frames=25),
        track(1, title="Full Subtitles", frames=480),
    ]
    assert select_track(tracks).index == 1


def test_signs_track_is_used_when_it_is_the_only_one():
    """The locked decision (D-C): signs/songs titles are DEPRIORITIZED, never excluded.

    Some releases ship a single track carrying signs, songs and dialogue under a
    "Signs & Songs" title; excluding it would make the episode silently unavailable.
    """
    only = [track(3, title="Signs & Songs [Fansub]", frames=612)]
    chosen = select_track(only)
    assert chosen is not None
    assert chosen.index == 3
    assert chosen.penalized is True
    assert any("signs" in r for r in chosen.penalty_reasons)


def test_forced_track_is_deprioritized_not_dropped():
    tracks = [track(0, forced=True, frames=40), track(1, frames=500)]
    assert select_track(tracks).index == 1
    assert select_track([track(0, forced=True, frames=40)]).index == 0


def test_frame_count_outranks_default_disposition():
    """Some releases mark the signs track as default — event count is the better signal."""
    tracks = [
        track(0, title="Signs", default=True, frames=20),
        track(1, title="Dialogue", default=False, frames=700),
    ]
    assert select_track(tracks).index == 1


def test_default_disposition_breaks_ties_when_frame_counts_are_absent():
    tracks = [track(0, title="Track A"), track(1, title="Track B", default=True)]
    assert select_track(tracks).index == 1


def test_tagged_language_outranks_untagged():
    tracks = [track(0, language="und", frames=900), track(1, language="eng", frames=100)]
    assert select_track(tracks).index == 1


def test_untagged_track_is_still_a_candidate():
    """Fansub muxes routinely omit the language tag entirely."""
    assert select_track([track(0, language="")]).index == 0
    assert select_track([track(0, language="und")]).index == 0


def test_wrong_language_is_dropped():
    assert select_track([track(0, language="jpn"), track(1, language="spa")]) is None
    assert select_track([track(0, language="jpn"), track(1, language="eng")]).index == 1


def test_language_variants_are_accepted():
    for tag in ("en", "eng", "english"):
        assert select_track([track(0, language=tag)]) is not None


def test_image_codecs_are_never_chosen():
    tracks = [
        track(0, codec="hdmv_pgs_subtitle", frames=900),
        track(1, codec="dvd_subtitle", frames=900),
    ]
    assert select_track(tracks) is None


def test_srt_and_subrip_codecs_are_accepted():
    tracks = [
        track(0, codec="subrip", frames=500),
        track(1, codec="mov_text", frames=300),
    ]
    chosen = select_track(tracks)
    assert chosen is not None
    assert chosen.index == 0
    assert chosen.output_format == "srt"


def test_ass_outranks_srt_when_both_present():
    tracks = [
        track(0, codec="subrip", frames=900),
        track(1, codec="ass", frames=900),
    ]
    assert select_track(tracks).index == 1


def test_ssa_codec_is_accepted():
    assert select_track([track(0, codec="ssa")]).index == 0


def test_no_tracks_at_all():
    assert select_track([]) is None


def test_user_keywords_override_the_defaults():
    tracks = [track(0, title="Honorifics", frames=500), track(1, title="Literal", frames=400)]
    # Default list doesn't mention honorifics -> the bigger track wins.
    assert select_track(tracks).index == 0
    # User adds it -> the other track wins despite having fewer events.
    assert select_track(tracks, keywords=["honorifics"]).index == 1


def test_empty_keyword_list_penalizes_nothing():
    chosen = select_track([track(0, title="Signs & Songs", frames=10)], keywords=[])
    assert chosen.penalized is False


def test_parse_keywords_handles_blanks_and_case():
    assert embedded_subs.parse_keywords("Signs, , SONGS ,") == ["signs", "songs"]
    assert embedded_subs.parse_keywords("") == []


def test_stream_index_is_the_final_tie_break():
    tracks = [track(5), track(2), track(9)]
    assert select_track(tracks).index == 2


# ---------------------------------------------------------------------------
# Probe parsing
# ---------------------------------------------------------------------------

def test_parse_probe_output_reads_tags_and_dispositions():
    raw = json.dumps({
        "streams": [{
            "index": 4,
            "codec_name": "ass",
            "tags": {
                "language": "eng",
                "title": "Full Dialogue",
                "NUMBER_OF_FRAMES": "301",
            },
            "disposition": {
                "forced": 0,
                "default": 1,
            },
        }]
    })
    (t,) = embedded_subs.parse_probe_output(raw)
    assert t.index == 4
    assert t.codec == "ass"
    assert t.language == "eng"
    assert t.title == "Full Dialogue"
    assert t.frames == 301
    assert t.forced is False
    assert t.default is True


def test_parse_probe_output_handles_missing_tags():
    raw = json.dumps({"streams": [{"index": 0, "codec_name": "ass"}]})
    assert embedded_subs.parse_probe_output(raw)[0].frames == 0


def test_parse_probe_output_handles_mkvmerge_frames_tag():
    raw = json.dumps({
        "streams": [{
            "index": 0,
            "codec_name": "ass",
            "tags": {"NUMBER_OF_FRAMES-eng": "301"},
        }]
    })
    assert embedded_subs.parse_probe_output(raw)[0].frames == 301


def test_parse_probe_output_bad_json_returns_empty():
    assert embedded_subs.parse_probe_output("not json") == []
    assert embedded_subs.parse_probe_output("{}") == []


def test_track_dict_round_trip():
    original = track(4, title="Dialogue", frames=88, default=True)
    assert SubtitleTrack.from_dict(original.to_dict()).to_dict() == original.to_dict()
    # Unknown keys from a future/older version must not explode the cache read.
    SubtitleTrack.from_dict({"index": 1, "codec": "ass", "some_new_field": 42})


# ---------------------------------------------------------------------------
# Sidecar naming and validation
# ---------------------------------------------------------------------------

def test_probe_subtitle_tracks_returns_empty_when_tools_missing(monkeypatch):
    monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: None)
    assert embedded_subs.probe_subtitle_tracks("X:/nope.mkv") == []


def test_sidecar_path_sits_next_to_media():
    p = embedded_subs.sidecar_path_for(r"D:\Media\Show\Show.S01E01.mkv", "en")
    assert p.name == "Show.S01E01.en.ass"
    assert p.parent.name == "Show"

    p_srt = embedded_subs.sidecar_path_for(r"D:\Media\Show\Show.S01E01.mkv", "en", ext="srt")
    assert p_srt.name == "Show.S01E01.en.srt"


def test_sidecar_is_found_by_the_scanner():
    """The scanner strips a trailing language tag from the media stem; the sidecar
    name must survive that round trip or the extracted file is invisible."""
    from integrations.subtitle_scanner import SubtitleScannerService
    from utils.language_codes import matches_language
    from pathlib import Path

    media = r"D:\Media\Movie.2019.BluRay.x264.AAC.mkv"
    sidecar = embedded_subs.sidecar_path_for(media, "en")
    stem = SubtitleScannerService._get_clean_stem(Path(media))
    assert matches_language(sidecar.name, stem, "en")


def test_usable_ass_gate():
    header = "[Script Info]\nTitle: x\n\n[Events]\nFormat: Layer, Start, End, Style, Text\n"
    assert embedded_subs.looks_like_usable_ass(
        header + "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,Hello\n"
    )
    assert not embedded_subs.looks_like_usable_ass(header)   # header only, no events
    assert not embedded_subs.looks_like_usable_ass("")
    assert not embedded_subs.looks_like_usable_ass("1\n00:00:01,000 --> 00:00:02,000\nSRT\n")


def test_usable_srt_and_sub_gate():
    srt = "1\n00:00:01,000 --> 00:00:02,000\nHello\n"
    assert embedded_subs.looks_like_usable_srt(srt)
    assert not embedded_subs.looks_like_usable_srt("no timecodes here")
    assert not embedded_subs.looks_like_usable_srt("")
    assert embedded_subs.looks_like_usable_sub(srt, ext="srt")


def test_write_sidecar_is_atomic(tmp_path):
    target = tmp_path / "Show.en.ass"
    embedded_subs.write_sidecar(target, "[Script Info]\n")
    assert target.read_text(encoding="utf-8-sig") == "[Script Info]\n"
    assert not list(tmp_path.glob("*.omnisub-tmp"))


# ---------------------------------------------------------------------------
# Extraction (subprocess seam stubbed)
# ---------------------------------------------------------------------------

def test_extract_track_maps_the_absolute_stream_index(monkeypatch):
    """-map must use the absolute stream index ffprobe reported, not 0:s:N — the
    subtitle-relative form silently selects a different track."""
    seen = {}

    async def fake_spawn(cmd, timeout):
        seen["cmd"] = cmd
        return 0, b"[Script Info]\n\n[Events]\nDialogue: 0,0:00:01.00,0:00:02.00,D,,hi\n", b""

    monkeypatch.setattr(embedded_subs, "_spawn", fake_spawn)
    monkeypatch.setattr(embedded_subs, "resolve_tools",
                        lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))

    out = asyncio.run(embedded_subs.extract_track("m.mkv", track(7)))
    assert "Dialogue:" in out
    assert "0:7" in seen["cmd"]
    assert "-c:s" in seen["cmd"] and "copy" in seen["cmd"]


def test_extract_srt_track_uses_proper_codec_flags(monkeypatch):
    seen = {}

    async def fake_spawn(cmd, timeout):
        seen["cmd"] = cmd
        return 0, b"1\n00:00:01,000 --> 00:00:02,000\nHi\n", b""

    monkeypatch.setattr(embedded_subs, "_spawn", fake_spawn)
    monkeypatch.setattr(embedded_subs, "resolve_tools",
                        lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))

    out = asyncio.run(embedded_subs.extract_track("m.mkv", track(2, codec="subrip")))
    assert "-->" in out
    assert "-c:s" in seen["cmd"] and "srt" in seen["cmd"]
    assert "-f" in seen["cmd"] and "srt" in seen["cmd"]


def test_extract_track_raises_on_ffmpeg_failure(monkeypatch):
    async def fake_spawn(cmd, timeout):
        return 1, b"", b"Stream map matches no streams"

    monkeypatch.setattr(embedded_subs, "_spawn", fake_spawn)
    monkeypatch.setattr(embedded_subs, "resolve_tools",
                        lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))
    with pytest.raises(RuntimeError, match="exited with code 1"):
        asyncio.run(embedded_subs.extract_track("m.mkv", track(1)))


def test_extract_track_without_ffmpeg_is_actionable(monkeypatch):
    monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: None)
    with pytest.raises(embedded_subs.FfmpegUnavailable):
        asyncio.run(embedded_subs.extract_track("m.mkv", track(1)))


# ---------------------------------------------------------------------------
# Queue lanes
# ---------------------------------------------------------------------------

@pytest.fixture
def queue(tmp_path):
    return TranslationQueue(db_path=tmp_path / "queue.db")


def test_extraction_and_translation_coexist_for_one_episode(queue):
    """The UNIQUE constraint must include `kind`. Without it the second enqueue
    collides and silently reuses the first row."""
    ex_id = queue.enqueue("Show", "S01E01", PRIORITY_MANUAL, {"media_path": "m.mkv"},
                          "", KIND_EXTRACTION)
    tr_id = queue.enqueue("Show", "S01E01", PRIORITY_MANUAL)
    assert ex_id != tr_id


def test_claims_do_not_cross_lanes(queue):
    queue.enqueue("Show", "S01E01", PRIORITY_MANUAL, {"media_path": "m.mkv"}, "", KIND_EXTRACTION)
    queue.enqueue("Show", "S01E01", PRIORITY_MANUAL)

    extraction = queue.claim_next_extraction()
    translation = queue.claim_next()
    assert extraction["kind"] == KIND_EXTRACTION
    assert translation["kind"] == KIND_TRANSLATION
    assert queue.claim_next_extraction() is None
    assert queue.claim_next() is None


def test_extractions_are_excluded_from_the_translation_queue_views(queue):
    queue.enqueue("Show", "S01E01", PRIORITY_MANUAL, {"media_path": "m.mkv"}, "", KIND_EXTRACTION)
    assert queue.get_all() == []
    assert queue.get_summary()["pending"] == 0
    assert len(queue.get_extractions()) == 1


def test_re_enqueueing_an_extraction_reuses_its_row(queue):
    first = queue.enqueue("Show", "S01E01", PRIORITY_MANUAL, {"media_path": "a.mkv"}, "", KIND_EXTRACTION)
    second = queue.enqueue("Show", "S01E01", PRIORITY_MANUAL, {"media_path": "a.mkv"}, "", KIND_EXTRACTION)
    assert first == second
    assert len(queue.get_extractions()) == 1


def test_kind_unique_migration_is_idempotent(tmp_path):
    """Re-opening an already-migrated DB must not rebuild (or lose) the table."""
    db = tmp_path / "queue.db"
    q1 = TranslationQueue(db_path=db)
    q1.enqueue("Show", "S01E01", PRIORITY_MANUAL, None, "", KIND_EXTRACTION)
    q2 = TranslationQueue(db_path=db)
    assert len(q2.get_extractions()) == 1


# ---------------------------------------------------------------------------
# prefer_ass_format — the per-project toggle, anime-defaulted
# ---------------------------------------------------------------------------

from integrations.media_sync_engine import (  # noqa: E402
    MediaSyncEngine, prefer_ass_for_project, secondary_episode_name,
)


class FakeScan:
    def __init__(self, source_subs, primary=None):
        self.source_subs = source_subs
        self.source_sub_path = primary or (source_subs[0] if source_subs else None)
        self.has_source_sub = bool(source_subs)


def test_anime_series_defaults_to_preferring_ass():
    assert prefer_ass_for_project({"series_type": "anime"}) is True
    assert prefer_ass_for_project({"series_type": "standard"}) is False
    assert prefer_ass_for_project({"series_type": "daily"}) is False
    assert prefer_ass_for_project({}) is False
    assert prefer_ass_for_project(None) is False


def test_explicit_project_setting_beats_the_anime_default():
    assert prefer_ass_for_project(
        {"series_type": "anime", "settings": {"prefer_ass_format": "never"}}) is False
    assert prefer_ass_for_project(
        {"series_type": "standard", "settings": {"prefer_ass_format": "always"}}) is True
    # Booleans (older payloads / API callers) work too.
    assert prefer_ass_for_project(
        {"series_type": "anime", "settings": {"prefer_ass_format": False}}) is False
    assert prefer_ass_for_project({"settings": {"prefer_ass_format": True}}) is True


def test_auto_falls_back_to_series_type():
    assert prefer_ass_for_project(
        {"series_type": "anime", "settings": {"prefer_ass_format": "auto"}}) is True
    assert prefer_ass_for_project(
        {"series_type": "standard", "settings": {"prefer_ass_format": "auto"}}) is False


def test_probe_triggers_for_srt_only_when_preferring_ass():
    engine = MediaSyncEngine(embedded_extraction=True)
    srt_only = FakeScan([r"D:\m\Show.en.srt"])

    # The whole point of the toggle: an .srt is NOT good enough for these projects.
    assert engine._wants_embedded_probe(srt_only, prefer_ass=True) is True
    assert engine._wants_embedded_probe(srt_only, prefer_ass=False) is False


def test_probe_never_triggers_when_an_ass_is_already_present():
    engine = MediaSyncEngine(embedded_extraction=True)
    both = FakeScan([r"D:\m\Show.en.ass", r"D:\m\Show.en.srt"])
    assert engine._wants_embedded_probe(both, prefer_ass=True) is False
    assert engine._wants_embedded_probe(FakeScan([r"D:\m\Show.en.ssa"]), prefer_ass=True) is False


def test_probe_triggers_for_bare_media_regardless_of_toggle():
    engine = MediaSyncEngine(embedded_extraction=True)
    assert engine._wants_embedded_probe(FakeScan([]), prefer_ass=False) is True


def test_probe_never_triggers_when_the_feature_is_off():
    engine = MediaSyncEngine(embedded_extraction=False)
    assert engine._wants_embedded_probe(FakeScan([]), prefer_ass=True) is False


def test_preferring_ass_defaults_alt_formats_on_but_explicit_wins():
    """The user wants the .srt translated too, so viewers get both."""
    assert MediaSyncEngine._create_alt_formats({"series_type": "anime"}, True) is True
    assert MediaSyncEngine._create_alt_formats({"series_type": "standard"}, False) is False
    # An explicit opt-out is still honoured.
    assert MediaSyncEngine._create_alt_formats(
        {"settings": {"translate_all_source_formats": False}}, True) is False
    assert MediaSyncEngine._create_alt_formats(
        {"settings": {"translate_all_source_formats": True}}, False) is True


# ---------------------------------------------------------------------------
# Format-flip migration — the destructive case this feature had to solve
# ---------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path, monkeypatch):
    from utils import storage
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(storage, "_arr_library_cache", None, raising=False)
    storage.PROJECTS_DIR.mkdir(parents=True)
    storage.create_project("Anime Show", {"show_name": "Anime Show", "series_type": "anime"})
    return storage


def test_format_flip_moves_the_srt_episode_aside_with_its_translations(project):
    storage = project
    storage.save_episode("Anime Show", "S01E01",
                         [{"id": 1, "timecode": "00:00:01,000 --> 00:00:02,000",
                           "original": "Hello", "translated": "Γεια"}],
                         {"arr_source": True, "original_extension": "srt",
                          "arr_sub_path": r"D:\m\Show.en.srt", "arr_sub_fingerprint": "fp-srt",
                          "translated": True})

    scan = FakeScan([r"D:\m\Show.en.ass", r"D:\m\Show.en.srt"], primary=r"D:\m\Show.en.ass")
    sibling = MediaSyncEngine._migrate_primary_format_flip(
        "Anime Show", "S01E01", scan,
        storage.load_episode_metadata("Anime Show", "S01E01"),
    )

    assert sibling == "S01E01 [srt]"
    # The base slot is free for the .ass...
    assert storage.load_episode_metadata("Anime Show", "S01E01") is None
    # ...and the Greek translation moved with the episode rather than being overwritten.
    moved = storage.load_episode("Anime Show", "S01E01 [srt]")
    assert moved["data"][0]["translated"] == "Γεια"
    moved_meta = storage.load_episode_metadata("Anime Show", "S01E01 [srt]")
    assert moved_meta["arr_secondary_of"] == "S01E01"
    assert moved_meta["arr_source_format"] == "srt"
    assert moved_meta["arr_sub_fingerprint"] == "fp-srt"  # so the next sync won't re-import


def test_no_migration_when_the_format_is_unchanged(project):
    storage = project
    storage.save_episode("Anime Show", "S01E01", [],
                         {"original_extension": "ass", "arr_sub_path": r"D:\m\Show.en.ass"})
    scan = FakeScan([r"D:\m\Show.en.ass"])
    assert MediaSyncEngine._migrate_primary_format_flip(
        "Anime Show", "S01E01", scan,
        storage.load_episode_metadata("Anime Show", "S01E01")) is None
    assert storage.load_episode_metadata("Anime Show", "S01E01") is not None


def test_no_migration_when_the_old_format_is_gone_from_disk(project):
    """The .srt was replaced, not supplemented — there is no sibling for it to become."""
    storage = project
    storage.save_episode("Anime Show", "S01E01", [],
                         {"original_extension": "srt", "arr_sub_path": r"D:\m\Show.en.srt"})
    scan = FakeScan([r"D:\m\Show.en.ass"])
    assert MediaSyncEngine._migrate_primary_format_flip(
        "Anime Show", "S01E01", scan,
        storage.load_episode_metadata("Anime Show", "S01E01")) is None


def test_migration_refuses_to_clobber_an_existing_sibling(project):
    storage = project
    storage.save_episode("Anime Show", "S01E01", [],
                         {"original_extension": "srt", "arr_sub_path": r"D:\m\Show.en.srt"})
    storage.save_episode("Anime Show", "S01E01 [srt]", [{"id": 1, "original": "keep me"}],
                         {"original_extension": "srt"})
    scan = FakeScan([r"D:\m\Show.en.ass", r"D:\m\Show.en.srt"], primary=r"D:\m\Show.en.ass")
    assert MediaSyncEngine._migrate_primary_format_flip(
        "Anime Show", "S01E01", scan,
        storage.load_episode_metadata("Anime Show", "S01E01")) is None
    assert storage.load_episode("Anime Show", "S01E01 [srt]")["data"][0]["original"] == "keep me"


def test_rename_episode_refuses_to_overwrite(project):
    storage = project
    storage.save_episode("Anime Show", "A", [], {})
    storage.save_episode("Anime Show", "B", [], {})
    assert storage.rename_episode("Anime Show", "A", "B") is False
    assert storage.rename_episode("Anime Show", "missing", "C") is False
    assert storage.rename_episode("Anime Show", "A", "C") is True
    assert "C" in storage.list_episodes("Anime Show")


def test_sibling_naming_matches_the_dual_format_convention():
    assert secondary_episode_name("S01E01", "srt") == "S01E01 [srt]"


# ---------------------------------------------------------------------------
# Probe cache
# ---------------------------------------------------------------------------

def test_probe_cache_round_trip_and_fingerprint_invalidation(tmp_path):
    from utils import media_probe_cache

    db = tmp_path / "cache.db"
    media_probe_cache._initialized = False
    tracks = [track(0, title="Dialogue", frames=500).to_dict()]

    media_probe_cache.put("m.mkv", "fp1", tracks, db_path=db)
    assert media_probe_cache.get("m.mkv", "fp1", db_path=db) == tracks
    # A changed media file (new fingerprint) must miss, not serve a stale track list.
    assert media_probe_cache.get("m.mkv", "fp2", db_path=db) is None
    # An unstattable file has no fingerprint and can never be served from cache.
    assert media_probe_cache.get("m.mkv", None, db_path=db) is None

    media_probe_cache.invalidate("m.mkv", db_path=db)
    assert media_probe_cache.get("m.mkv", "fp1", db_path=db) is None
    media_probe_cache._initialized = False
