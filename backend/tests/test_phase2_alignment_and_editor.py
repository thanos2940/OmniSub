import time
import pytest
from utils.source_clean import align_and_carry_translations
from routers.schemas import SettingsRequest, ProjectSettings, ProjectMetadata


def test_align_and_carry_translations_fast_window_10k():
    """Verify windowed LCS matching completes in < 0.5s for 10k rows without OOM."""
    n_cues = 10000
    old_lines = []
    new_lines = []

    for i in range(n_cues):
        text = f"Dialogue line {i} with some content here."
        old_lines.append({
            "original": text,
            "translated": f"Μεταφρασμένη γραμμή {i}",
            "translations": {"el": f"Μεταφρασμένη γραμμή {i}"},
            "from_tm": True if i % 10 == 0 else False,
            "tm_user_edited": True if i % 10 == 0 else False,
            "tm_origin_episode": "Ep01" if i % 10 == 0 else "",
            "needs_review": True if i % 20 == 0 else False,
            "review_issues": ["Issue"] if i % 20 == 0 else [],
            "previous_translated": f"Παλιά {i}" if i % 20 == 0 else "",
            "timecode": f"00:00:{i:02d},000 --> 00:00:{i:02d},500"
        })
        new_lines.append({
            "original": text,
            "timecode": f"00:00:{i:02d},000 --> 00:00:{i:02d},500"
        })

    # Benchmark windowed matching (10,000 x 10,000 would be 100M matrix cells if full DP)
    start_time = time.perf_counter()
    result_lines, carried = align_and_carry_translations(old_lines, new_lines, min_unchanged_ratio=0.5)
    elapsed = time.perf_counter() - start_time

    assert carried is True
    assert len(result_lines) == n_cues
    # Should complete well under 0.5s with linear windowed scan
    assert elapsed < 0.5, f"Execution took {elapsed:.4f}s, expected < 0.5s"

    # Spot-check carried metadata
    assert result_lines[0]["translated"] == "Μεταφρασμένη γραμμή 0"
    assert result_lines[0]["translations"] == {"el": "Μεταφρασμένη γραμμή 0"}
    assert result_lines[0]["from_tm"] is True
    assert result_lines[0]["needs_review"] is True
    assert result_lines[1]["from_tm"] is False
    assert result_lines[1]["needs_review"] is False
    assert result_lines[9999]["translated"] == "Μεταφρασμένη γραμμή 9999"


def test_align_and_carry_translations_translatable_filtering():
    """Verify non-translatable cues (translatable=False or _ass.translatable=False) are ignored during matching."""
    old_lines = [
        {"original": "Sign text", "translatable": False, "_ass": {"translatable": False}},
        {"original": "Hello world", "translated": "Γεια σου κόσμε", "translations": {"el": "Γεια σου κόσμε"}},
        {"original": "Karaoke FX", "translatable": False},
        {"original": "Goodbye", "translated": "Αντίο", "translations": {"el": "Αντίο"}}
    ]

    new_lines = [
        {"original": "Sign text updated", "translatable": False, "_ass": {"translatable": False}},
        {"original": "Hello world"},
        {"original": "Karaoke FX updated", "translatable": False},
        {"original": "Goodbye"}
    ]

    result_lines, carried = align_and_carry_translations(old_lines, new_lines, min_unchanged_ratio=0.5)

    assert carried is True
    assert len(result_lines) == 4
    # Non-translatable lines should not have translated field carried over
    assert "translated" not in result_lines[0]
    assert "translated" not in result_lines[2]
    # Translatable lines should have translated carried over
    assert result_lines[1]["translated"] == "Γεια σου κόσμε"
    assert result_lines[3]["translated"] == "Αντίο"


def test_align_and_carry_translations_low_ratio_rejected():
    """Verify changes below min_unchanged_ratio are rejected."""
    old_lines = [
        {"original": "Line A", "translated": "Γραμμή Α"},
        {"original": "Line B", "translated": "Γραμμή Β"},
    ]
    new_lines = [
        {"original": "Totally different 1"},
        {"original": "Totally different 2"},
    ]

    result_lines, carried = align_and_carry_translations(old_lines, new_lines, min_unchanged_ratio=0.5)
    assert carried is False
    assert "translated" not in result_lines[0]


def test_project_settings_schemas_translate_lyrics():
    """Verify schemas define and default translate_lyrics=True."""
    settings_req = SettingsRequest()
    assert settings_req.translate_lyrics is True

    proj_settings = ProjectSettings()
    assert proj_settings.translate_lyrics is True

    proj_meta = ProjectMetadata()
    assert proj_meta.translate_lyrics is True

    # Check override
    custom_settings = ProjectSettings(translate_lyrics=False)
    assert custom_settings.translate_lyrics is False

    custom_req = SettingsRequest(translate_lyrics=False)
    assert custom_req.translate_lyrics is False
