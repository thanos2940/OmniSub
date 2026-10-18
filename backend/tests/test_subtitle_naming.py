"""
Subtitle files must stay named after their video, or Plex/Jellyfin stop showing them.

Covers the name splitter (language/forced tags vs. release names like "Opus 2.0"),
the rename planner (never overwrite, only unambiguous matches), export resolving an
upgraded video file, and disk sync never treating an unreachable share as deletion.
"""

from pathlib import Path

from utils import storage
from utils.subtitle_naming import split_subtitle_name, episode_tag, plan_directory, apply_plan


def _touch(d: Path, *names):
    for n in names:
        (d / n).write_text("x", encoding="utf-8")


class TestNameSplitting:

    def test_release_names_keep_their_dots(self):
        assert split_subtitle_name("Show - S03E01 - Smoke Signal HDTV-1080p [JA+EN] AAC 2.0.el.ass") == \
            ("Show - S03E01 - Smoke Signal HDTV-1080p [JA+EN] AAC 2.0", ".el.ass")
        assert split_subtitle_name("Movie (2020) Bluray-1080p.AAC.srt") == ("Movie (2020) Bluray-1080p.AAC", ".srt")

    def test_language_and_flags_are_peeled(self):
        assert split_subtitle_name("Show.S01E01.WEB.h264-NTb.en.forced.srt") == ("Show.S01E01.WEB.h264-NTb", ".en.forced.srt")
        assert split_subtitle_name("X.ell.sdh.srt") == ("X", ".ell.sdh.srt")

    def test_episode_tags(self):
        assert episode_tag("Show - s1e2 - x.mkv") == "S01E02"
        assert episode_tag("Show - 3x07 - x.mkv") == "S03E07"
        assert episode_tag("Movie (2020).mkv") is None


class TestRenamePlan:

    def test_upgrade_rename_conflict_and_unmatched(self, tmp_path):
        _touch(tmp_path,
               "Show - S01E01 - A Bluray Opus 2.0.mkv",
               "Show - S01E01 - A Bluray Opus 2.0.en.ass",          # already right
               "Show - S01E01 - A HDTV AAC 2.0.el.ass",             # orphan -> rename
               "Show - S01E01 - A HDTV AAC 2.0.en.forced.srt",      # orphan, keep tags
               "Show - S01E02 - B Opus.mkv",
               "Show - S01E02 - B AAC.el.ass",                      # target name taken
               "Show - S01E02 - B Opus.el.ass",
               "Show - S01E03 - C.el.srt")                          # no video
        plan = {Path(e["old"]).name: (e["status"], Path(e["new"]).name if e["new"] else "") for e in plan_directory(tmp_path)}
        assert plan == {
            "Show - S01E01 - A HDTV AAC 2.0.el.ass": ("rename", "Show - S01E01 - A Bluray Opus 2.0.el.ass"),
            "Show - S01E01 - A HDTV AAC 2.0.en.forced.srt": ("rename", "Show - S01E01 - A Bluray Opus 2.0.en.forced.srt"),
            "Show - S01E02 - B AAC.el.ass": ("conflict", "Show - S01E02 - B Opus.el.ass"),
            "Show - S01E03 - C.el.srt": ("unmatched", ""),
        }
        renamed, errors = apply_plan(plan_directory(tmp_path))
        assert len(renamed) == 2 and not errors
        assert (tmp_path / "Show - S01E02 - B AAC.el.ass").exists()  # conflicts are never overwritten
        assert plan_directory(tmp_path)[0]["status"] == "conflict"

    def test_two_videos_for_one_episode_is_ambiguous(self, tmp_path):
        _touch(tmp_path, "S - S01E01 - a.mkv", "S - S01E01 - b.mkv", "S - S01E01 - old.el.srt")
        assert [e["status"] for e in plan_directory(tmp_path)] == ["ambiguous"]

    def test_movie_folder_single_video(self, tmp_path):
        _touch(tmp_path, "Film (2020) Bluray.mkv", "Film (2020) WEB.el.srt")
        (entry,) = plan_directory(tmp_path)
        assert entry["status"] == "rename" and Path(entry["new"]).name == "Film (2020) Bluray.el.srt"


class TestExportNaming:

    def test_export_follows_upgraded_video_and_drops_old_name(self, tmp_path):
        from services.translation.export_formatter import auto_export_translated_subtitle
        project = "NamingExportProj"
        storage.create_project(project, {"show_name": project, "target_language": "Greek"})
        cues = [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hello", "translated": "Γεια"}]
        storage.save_episode(project, "S01E01", cues)
        media = tmp_path / "Season 1"
        media.mkdir()
        _touch(media, "Show - S01E01 - A Bluray Opus 2.0.mkv", "Show - S01E01 - A HDTV AAC 2.0.el.srt",
               "Show - S01E01 - A HDTV AAC 2.0.el.ass")
        meta = {"arr_media_path": str(media / "Show - S01E01 - A HDTV AAC 2.0.mkv"),   # replaced by the upgrade
                "arr_target_path": str(media / "Show - S01E01 - A HDTV AAC 2.0.el.srt"),
                "translation_source": "agent"}  # our own earlier export
        storage.save_episode_metadata(project, "S01E01", meta)

        out = auto_export_translated_subtitle(project, "S01E01", cues, meta)
        assert Path(out).name == "Show - S01E01 - A Bluray Opus 2.0.el.srt"
        names = sorted(p.name for p in media.iterdir())
        assert "Show - S01E01 - A HDTV AAC 2.0.el.srt" not in names       # our superseded export
        assert "Show - S01E01 - A HDTV AAC 2.0.el.ass" in names           # different format: not ours to delete
        saved = storage.load_episode_metadata(project, "S01E01")
        assert saved["arr_media_path"].endswith("Bluray Opus 2.0.mkv")


class TestDiskSyncSafety:

    def test_unreachable_media_folder_does_not_wipe_translations(self, tmp_path):
        from services.disk_sync_service import disk_sync_service
        project = "UnreachableShareProj"
        storage.create_project(project, {"show_name": project, "target_language": "Greek"})
        storage.save_episode(project, "S01E01", [{"id": "1", "original": "Hello", "translated": "Γεια",
                                                  "translations": {"el": "Γεια"}}])
        gone = tmp_path / "offline-share" / "Season 1"
        storage.save_episode_metadata(project, "S01E01", {
            "translated": True, "translation_status": "completed", "arr_has_target": True,
            "arr_media_path": str(gone / "Show - S01E01.mkv"),
            "arr_target_path": str(gone / "Show - S01E01.el.srt"),
        })
        res = disk_sync_service.sync_project_disk(project, re_queue_removed=True, auto_add_new_episodes=False)
        assert res["translations_removed"] == 0
        assert storage.load_episode_data(project, "S01E01")[0]["translated"] == "Γεια"
        assert storage.load_episode_metadata(project, "S01E01")["translated"] is True


class TestPlainSrtCompanion:

    def test_ass_export_also_writes_dialogue_only_srt(self, tmp_path):
        from services.translation.export_formatter import auto_export_translated_subtitle
        project = "SrtCompanionProj"
        storage.create_project(project, {"show_name": project, "target_language": "Greek"})
        raw = (
            "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n"
            "Style: Signs,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hello\n"
            "Dialogue: 1,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hello\n"
            "Dialogue: 0,0:00:03.00,0:00:04.00,Signs,,0,0,0,,{\pos(5,5)}Wall\n"
        )
        storage.save_original_subtitle(project, "S01E01", raw, filename="original.ass")
        from utils.subtitle_io import parse_subtitle
        cues = parse_subtitle(raw, filename="x.ass")
        for c, t in zip(cues, ["Γεια", "Γεια", "Τείχος"]):
            c["translated"] = t
            c["translations"] = {"el": t}
        storage.save_episode(project, "S01E01", cues)
        media = tmp_path / "S"
        media.mkdir()
        (media / "Show - S01E01 Opus.mkv").touch()
        meta = {"arr_media_path": str(media / "Show - S01E01 Opus.mkv")}
        storage.save_episode_metadata(project, "S01E01", meta)
        auto_export_translated_subtitle(project, "S01E01", cues, meta)
        assert (media / "Show - S01E01 Opus.el.ass").is_file()
        srt = (media / "Show - S01E01 Opus.el.srt").read_text(encoding="utf-8-sig")
        assert srt.count("Γεια") == 1 and "Τείχος" not in srt and srt.startswith("1\n")

    def test_srt_companion_turns_ass_line_breaks_into_newlines(self):
        from services.translation.export_formatter import _ass_rows_to_srt
        rows = [{"_ass": {"kind": "dialogue", "translatable": True}, "timecode": "00:00:01,000 --> 00:00:02,000",
                 "translated": r"a\Nb\hc", "translations": {"el": r"a\Nb\hc"}}]
        assert _ass_rows_to_srt(rows, "el") == "1\n00:00:01,000 --> 00:00:02,000\na\nb c\n"
