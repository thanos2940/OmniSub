"""Export hardening: atomic writes, ownership registry, foreign-file guard, SRT companion."""

import hashlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from services.autopilot import ownership
from services.translation import export_formatter as ef

GREEK = "Μια ελληνική γραμμή"


def _rows(ass=False):
    row = {"original": "Hello", "translated": GREEK, "translations": {"el": GREEK},
           "timecode": "00:00:01,000 --> 00:00:02,000"}
    if ass:
        row["_ass"] = {"kind": "dialogue", "translatable": True}
    return [row]


def _export(media, metadata, *, ext="srt", cfg=None, force=False, rows=None):
    config = {"autopilot_protect_foreign_subs": True}
    config.update(cfg or {})
    with patch("utils.storage.load_project_metadata", return_value={"target_language": "Greek"}), \
         patch("utils.storage.load_global_config", return_value=config), \
         patch("utils.storage.save_episode_metadata"), \
         patch("utils.storage.load_original_subtitle", return_value=("", "." + ext)), \
         patch("utils.source_clean.reconstruct_cleaned_srt", return_value="CONTENT-" + GREEK):
        return ef.auto_export_translated_subtitle(
            "proj", "S01E01", rows if rows is not None else _rows(ext == "ass"), metadata, force=force)


@pytest.fixture
def media(tmp_path):
    m = tmp_path / "Show.S01E01.mkv"
    m.write_text("x", encoding="utf-8")
    return m


class TestAtomicWrite:
    def test_replaces_content_and_leaves_no_temp_files(self, tmp_path):
        tmp_path = tmp_path / "w"
        tmp_path.mkdir()
        target = tmp_path / "a.el.srt"
        target.write_text("old", encoding="utf-8")
        ef._atomic_write_text(target, "new " + GREEK, "utf-8-sig")
        assert target.read_bytes().startswith(b"\xef\xbb\xbf")
        assert target.read_text(encoding="utf-8-sig") == "new " + GREEK
        assert [p.name for p in tmp_path.iterdir()] == ["a.el.srt"]

    def test_failure_keeps_old_file_and_cleans_temp(self, tmp_path):
        tmp_path = tmp_path / "w"
        tmp_path.mkdir()
        target = tmp_path / "a.el.srt"
        target.write_text("old", encoding="utf-8")
        with patch("os.replace", side_effect=OSError("locked")):
            with pytest.raises(OSError):
                ef._atomic_write_text(target, "new")
        assert target.read_text(encoding="utf-8") == "old"
        assert [p.name for p in tmp_path.iterdir()] == ["a.el.srt"]


class TestOwnership:
    def test_register_and_is_ours(self, tmp_path):
        f = tmp_path / "x.el.srt"
        f.write_text("hello", encoding="utf-8")
        assert not ownership.is_ours(f)
        ownership.register_file(f, "proj", "S01E01")
        assert ownership.is_ours(f)
        rows = ownership.owned_files()
        assert len(rows) == 1 and rows[0]["sha1"] == hashlib.sha1(b"hello").hexdigest()
        assert rows[0]["size"] == 5 and rows[0]["project_name"] == "proj"
        assert ownership.owned_files("other") == []

    def test_changed_content_is_no_longer_ours(self, tmp_path):
        f = tmp_path / "x.el.srt"
        f.write_text("hello", encoding="utf-8")
        ownership.register_file(f)
        f.write_text("someone else edited this", encoding="utf-8")
        assert not ownership.is_ours(f)

    def test_forget(self, tmp_path):
        f = tmp_path / "x.el.srt"
        f.write_text("hello", encoding="utf-8")
        ownership.register_file(f)
        ownership.forget(f)
        assert not ownership.is_ours(f)

    def test_legacy_rule(self, tmp_path):
        f = tmp_path / "x.el.srt"
        f.write_text("hello", encoding="utf-8")
        meta = {"arr_target_path": str(f)}
        assert not ownership.is_ours(f, meta)  # target recorded, but not translated by us
        meta["translation_source"] = "gemini"
        assert ownership.is_ours(f, meta)
        # the .srt companion of a recorded .ass target
        ass = tmp_path / "x.el.ass"
        ass.write_text("a", encoding="utf-8")
        meta2 = {"arr_target_path": str(ass), "last_translated_at": "2026-01-01T00:00:00"}
        assert ownership.is_ours(f, meta2)
        other = tmp_path / "y.el.srt"
        other.write_text("z", encoding="utf-8")
        assert not ownership.is_ours(other, meta2)

    def test_never_raises(self, tmp_path):
        with patch("services.autopilot.ownership._connect", side_effect=RuntimeError("db broken")):
            f = tmp_path / "x.srt"
            f.write_text("a")
            ownership.register_file(f)
            ownership.forget(f)
            assert ownership.is_ours(f) is False
            assert ownership.owned_files() == []


class TestExportGuard:
    def test_writes_and_registers(self, media):
        out = _export(media, {"arr_media_path": str(media)})
        target = media.parent / "Show.S01E01.el.srt"
        assert out == str(target)
        assert target.read_text(encoding="utf-8-sig") == "CONTENT-" + GREEK
        assert ownership.is_ours(target)
        assert [p.name for p in media.parent.glob("*.tmp")] == []

    def test_second_export_overwrites_own_file(self, media):
        meta = {"arr_media_path": str(media)}
        _export(media, meta)
        with patch("utils.source_clean.reconstruct_cleaned_srt", return_value="v2"):
            out = _export_again(media, meta)
        assert out is not None
        assert (media.parent / "Show.S01E01.el.srt").read_text(encoding="utf-8-sig") == "v2"

    def test_foreign_file_is_not_overwritten(self, media):
        foreign = media.parent / "Show.S01E01.el.srt"
        foreign.write_text("from bazarr", encoding="utf-8")
        meta = {"arr_media_path": str(media)}
        assert _export(media, meta) is None
        assert foreign.read_text(encoding="utf-8") == "from bazarr"
        assert "not written by OmbiSub" in meta["translation_warning"]
        assert "arr_target_path" not in meta
        assert not ownership.is_ours(foreign)

    def test_force_overwrites_foreign_file(self, media):
        foreign = media.parent / "Show.S01E01.el.srt"
        foreign.write_text("from bazarr", encoding="utf-8")
        out = _export(media, {"arr_media_path": str(media)}, force=True)
        assert out == str(foreign)
        assert foreign.read_text(encoding="utf-8-sig") == "CONTENT-" + GREEK
        assert ownership.is_ours(foreign)

    def test_protection_can_be_switched_off(self, media):
        foreign = media.parent / "Show.S01E01.el.srt"
        foreign.write_text("from bazarr", encoding="utf-8")
        out = _export(media, {"arr_media_path": str(media)}, cfg={"autopilot_protect_foreign_subs": False})
        assert out == str(foreign)

    def test_legacy_export_is_overwritten(self, media):
        old = media.parent / "Show.S01E01.el.srt"
        old.write_text("old export", encoding="utf-8")
        meta = {"arr_media_path": str(media), "arr_target_path": str(old), "translation_source": "gemini"}
        assert _export(media, meta) == str(old)
        assert old.read_text(encoding="utf-8-sig") == "CONTENT-" + GREEK

    def test_superseded_foreign_file_is_not_deleted(self, tmp_path):
        folder = tmp_path / "S"
        folder.mkdir()
        new_media = folder / "Show - S01E01 - Opus.mkv"
        new_media.write_text("x")
        old = folder / "Show - S01E01 - AAC.el.srt"
        old.write_text("foreign", encoding="utf-8")
        meta = {"arr_media_path": str(new_media), "arr_target_path": str(old)}  # no translation markers
        _export(new_media, meta)
        assert old.exists()

    def test_superseded_own_file_is_deleted(self, tmp_path):
        folder = tmp_path / "S"
        folder.mkdir()
        new_media = folder / "Show - S01E01 - Opus.mkv"
        new_media.write_text("x")
        old = folder / "Show - S01E01 - AAC.el.srt"
        old.write_text("ours", encoding="utf-8")
        ownership.register_file(old)
        meta = {"arr_media_path": str(new_media), "arr_target_path": str(old)}
        _export(new_media, meta)
        assert not old.exists()
        assert (folder / "Show - S01E01 - Opus.el.srt").exists()


def _export_again(media, meta):
    config = {"autopilot_protect_foreign_subs": True}
    with patch("utils.storage.load_project_metadata", return_value={"target_language": "Greek"}), \
         patch("utils.storage.load_global_config", return_value=config), \
         patch("utils.storage.save_episode_metadata"), \
         patch("utils.storage.load_original_subtitle", return_value=("", ".srt")):
        return ef.auto_export_translated_subtitle("proj", "S01E01", _rows(), meta)


class TestAssCompanion:
    def test_companion_written_and_registered(self, media):
        out = _export(media, {"arr_media_path": str(media)}, ext="ass")
        ass = media.parent / "Show.S01E01.el.ass"
        srt = media.parent / "Show.S01E01.el.srt"
        assert out == str(ass)
        assert ass.exists() and srt.exists()
        assert GREEK in srt.read_text(encoding="utf-8-sig")
        assert ownership.is_ours(ass) and ownership.is_ours(srt)

    def test_foreign_companion_is_kept_but_main_is_written(self, media):
        foreign_srt = media.parent / "Show.S01E01.el.srt"
        foreign_srt.write_text("from bazarr", encoding="utf-8")
        out = _export(media, {"arr_media_path": str(media)}, ext="ass")
        assert out == str(media.parent / "Show.S01E01.el.ass")
        assert foreign_srt.read_text(encoding="utf-8") == "from bazarr"

    def test_foreign_main_is_kept_companion_still_written(self, media):
        foreign_ass = media.parent / "Show.S01E01.el.ass"
        foreign_ass.write_text("foreign", encoding="utf-8")
        meta = {"arr_media_path": str(media)}
        assert _export(media, meta, ext="ass") is None
        assert foreign_ass.read_text(encoding="utf-8") == "foreign"
        assert (media.parent / "Show.S01E01.el.srt").exists()
        assert "translation_warning" in meta

    def test_companion_failure_is_logged_as_error(self, media, caplog):
        with patch("services.translation.export_formatter._atomic_write_text",
                   side_effect=[None, OSError("disk full")]), caplog.at_level("ERROR"):
            out = _export(media, {"arr_media_path": str(media)}, ext="ass")
        assert out is not None
        assert any("SRT companion" in r.message and r.levelname == "ERROR" for r in caplog.records)


class TestSrtSiblingStandsDownTheCompanion:
    def test_translated_srt_sibling_means_no_plain_companion(self, media):
        rows = [{"original": "Hello", "translated": GREEK, "translations": {"el": GREEK},
                 "timecode": "00:00:01,000 --> 00:00:02,000"}]
        with patch("utils.storage.load_episode", return_value={"data": rows}) as load:
            out = _export(media, {"arr_media_path": str(media)}, ext="ass")
        load.assert_called_with("proj", "S01E01 [srt]")
        assert out == str(media.parent / "Show.S01E01.el.ass")
        assert not (media.parent / "Show.S01E01.el.srt").exists()

    def test_untranslated_or_missing_sibling_keeps_the_companion(self, media):
        untranslated = [{"original": "Hello", "translations": {}, "timecode": "00:00:01,000 --> 00:00:02,000"}]
        with patch("utils.storage.load_episode", return_value={"data": untranslated}):
            _export(media, {"arr_media_path": str(media)}, ext="ass")
        assert (media.parent / "Show.S01E01.el.srt").exists()
        (media.parent / "Show.S01E01.el.srt").unlink()
        with patch("utils.storage.load_episode", return_value=None):
            _export(media, {"arr_media_path": str(media)}, ext="ass")
        assert (media.parent / "Show.S01E01.el.srt").exists()
