"""SubtitleEdit fixes are export-only.

SE's SplitLongLines re-segments cues (one long cue -> several). Applying that to the
saved episode would break the editor's required 1:1 mapping with the source — the
cause of the translation/source drift. So SE must run on the EXPORTED FILE only; the
editor data (parsed_srt) must be left untouched.
"""

from unittest.mock import patch

from services import translation_service as ts


def _rows():
    return [
        {"original": "A long line that SE would split into two cues",
         "translated": "Μια πολύ μεγάλη γραμμή",
         "translations": {"el": "Μια πολύ μεγάλη γραμμή"}},
        {"original": "Second", "translated": "Δεύτερη", "translations": {"el": "Δεύτερη"}},
    ]


def test_se_fixes_apply_to_file_but_not_editor_data(tmp_path):
    media = tmp_path / "Show.S01E10.mkv"
    media.write_text("x", encoding="utf-8")
    parsed = _rows()
    metadata = {"arr_media_path": str(media)}

    # SE "splits" the long cue — the file ends up with more cues than the editor.
    fixed = "1\n00:00:01,000 --> 00:00:02,000\nΜια πολύ\nμεγάλη γραμμή\n\n2\n00:00:02,000 --> 00:00:03,000\nΔεύτερη\n"

    with patch("utils.storage.load_project_metadata", return_value={"target_language": "Greek"}), \
         patch("utils.storage.load_global_config", return_value={"apply_subtitle_edit_fixes": True}), \
         patch("utils.storage.save_episode_metadata"), \
         patch("utils.source_clean.reconstruct_cleaned_srt", return_value="RAW_SRT"), \
         patch("services.translation_service._run_se_cli", return_value=fixed) as se:
        ts.auto_export_translated_subtitle("proj", "S01E10", parsed, metadata)

    # SE was applied to the reconstructed file content...
    se.assert_called_once_with("RAW_SRT")
    written = (media.parent / "Show.S01E10.el.srt").read_text(encoding="utf-8-sig")
    assert written == fixed
    # ...but the editor data was NOT re-segmented: still exactly 2 rows, text intact.
    assert len(parsed) == 2
    assert parsed[0]["translations"]["el"] == "Μια πολύ μεγάλη γραμμή"


def test_conformance_wrapping_applies_to_export_copy_only(tmp_path):
    # Conformance is also output-only: wrapping must hit the exported copy, never the
    # editor's translations.
    media = tmp_path / "Show.S01E10.mkv"
    media.write_text("x", encoding="utf-8")
    long_txt = "Μια πάρα πολύ μεγάλη γραμμή που ξεπερνά κατά πολύ το όριο χαρακτήρων ανά σειρά"
    parsed = [{"original": "long", "translated": long_txt, "translations": {"el": long_txt}}]
    metadata = {"arr_media_path": str(media)}

    captured = {}

    def fake_reconstruct(p, e, rows, lang):
        captured["rows"] = rows
        return "RAW"

    cfg = {"conformance_enabled": True, "max_chars_per_line": 30, "max_lines": 2, "max_cps": 17.0}
    with patch("utils.storage.load_project_metadata", return_value={"target_language": "Greek"}), \
         patch("utils.storage.load_global_config", return_value=cfg), \
         patch("utils.storage.save_episode_metadata"), \
         patch("utils.source_clean.reconstruct_cleaned_srt", side_effect=fake_reconstruct):
        ts.auto_export_translated_subtitle("proj", "S01E10", parsed, metadata)

    # Editor data untouched — still one unwrapped line.
    assert parsed[0]["translations"]["el"] == long_txt
    assert "\n" not in parsed[0]["translations"]["el"]
    # The exported copy is a different object and was wrapped onto multiple lines.
    assert captured["rows"] is not parsed
    assert "\n" in captured["rows"][0]["translations"]["el"]


def test_se_skipped_when_disabled(tmp_path):
    media = tmp_path / "Show.S01E10.mkv"
    media.write_text("x", encoding="utf-8")
    parsed = _rows()
    metadata = {"arr_media_path": str(media)}

    with patch("utils.storage.load_project_metadata", return_value={"target_language": "Greek"}), \
         patch("utils.storage.load_global_config", return_value={"apply_subtitle_edit_fixes": False}), \
         patch("utils.storage.save_episode_metadata"), \
         patch("utils.source_clean.reconstruct_cleaned_srt", return_value="RAW_SRT"), \
         patch("services.translation_service._run_se_cli") as se:
        ts.auto_export_translated_subtitle("proj", "S01E10", parsed, metadata)

    se.assert_not_called()
    written = (media.parent / "Show.S01E10.el.srt").read_text(encoding="utf-8-sig")
    assert written == "RAW_SRT"
