"""TDD Verification for Task 1.1:
1. Single Export Seam in translation_item_worker.py (F-02)
2. Pass target_lang in reconstruct_cleaned_srt / export_ass (source_clean.py)
3. Sync ownership.db on Sidecar Rename (subtitle_naming.py)
4. Safe Delete Translation with Disk Sidecar Prompt & Ownership Reporting (routers/episodes.py, D-01)
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from main import app
from utils import storage
from utils.source_clean import reconstruct_cleaned_srt, export_ass
from utils.subtitle_naming import apply_plan, rename_episode_subtitles_to_match_video
from services.autopilot import ownership
from routers.episodes import _delete_episode_translation


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    """Isolated environment with PROJECTS_DIR and DATA_DIR."""
    proj_dir = tmp_path / "projects"
    proj_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage, "PROJECTS_DIR", proj_dir)

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    from utils import paths
    monkeypatch.setattr(paths, "DATA_DIR", str(data_dir))
    ownership._initialised.clear()

    project_name = "TestProject"
    storage.create_project(project_name, {"show_name": project_name, "target_language": "Greek"})
    return {"project_name": project_name, "tmp_path": tmp_path}


# ---------------------------------------------------------------------------
# 1. Single Export Seam in translation_item_worker.py
# ---------------------------------------------------------------------------

class TestSingleExportSeam:

    @pytest.mark.asyncio
    async def test_worker_routes_through_auto_export_translated_subtitle(self, test_env, monkeypatch):
        """Worker must route exports strictly through auto_export_translated_subtitle."""
        from services.worker.translation_item_worker import process_translation_item

        project_name = test_env["project_name"]
        episode_name = "S01E01"
        media_file = test_env["tmp_path"] / "Show.S01E01.mkv"
        media_file.touch()

        storage.save_original_subtitle(project_name, episode_name, "1\n00:00:01,000 --> 00:00:02,000\nHello\n", filename="Show.S01E01.en.srt")
        cues = [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hello", "translated": "Γεια"}]
        storage.save_episode(project_name, episode_name, cues, {
            "arr_media_path": str(media_file),
            "arr_source": True,
            "has_source_sub": True,
            "line_count": 1,
        })

        target_sub = test_env["tmp_path"] / "Show.S01E01.el.srt"

        mock_auto_export = MagicMock(return_value=str(target_sub))

        def fake_auto_export(proj, ep, data, meta):
            target_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nΓεια\n", encoding="utf-8")
            return str(target_sub)

        mock_auto_export.side_effect = fake_auto_export

        async def fake_atomic(*args, **kwargs):
            return True

        async def fake_bootstrap(*args, **kwargs):
            return {}, ""

        monkeypatch.setattr("services.worker.translation_item_worker.translate_episode_atomic", fake_atomic)
        monkeypatch.setattr("services.project_bootstrap.ensure_project_context", fake_bootstrap)
        monkeypatch.setattr("services.translation.export_formatter.auto_export_translated_subtitle", mock_auto_export)

        item = {
            "id": 1,
            "project_name": project_name,
            "episode_name": episode_name,
            "force_new": False,
            "translation_type": "new",
            "attempts": 0,
        }

        mock_queue = MagicMock()
        await process_translation_item(queue=mock_queue, item=item, global_config={})

        # Verify auto_export_translated_subtitle was invoked
        assert mock_auto_export.called
        meta = storage.load_episode_metadata(project_name, episode_name)
        assert meta.get("arr_has_target") is True
        assert meta.get("arr_target_path") == str(target_sub)

    @pytest.mark.asyncio
    async def test_worker_queues_retry_on_unreachable_export_path(self, test_env, monkeypatch):
        """When destination is unreachable, enqueue_export_retry is scheduled and warning stored."""
        from services.worker.translation_item_worker import process_translation_item

        project_name = test_env["project_name"]
        episode_name = "S01E02"
        unreachable = test_env["tmp_path"] / "nonexistent_dir" / "Show.S01E02.mkv"

        storage.save_original_subtitle(project_name, episode_name, "1\n00:00:01,000 --> 00:00:02,000\nHello\n", filename="Show.S01E02.en.srt")
        cues = [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hello", "translated": "Γεια"}]
        storage.save_episode(project_name, episode_name, cues, {
            "arr_media_path": str(unreachable),
            "arr_source": True,
            "has_source_sub": True,
            "line_count": 1,
        })

        async def fake_atomic(*args, **kwargs):
            return True

        async def fake_bootstrap(*args, **kwargs):
            return {}, ""

        monkeypatch.setattr("services.worker.translation_item_worker.translate_episode_atomic", fake_atomic)
        monkeypatch.setattr("services.project_bootstrap.ensure_project_context", fake_bootstrap)

        retry_mock = MagicMock()
        monkeypatch.setattr("services.queue_service.enqueue_export_retry", retry_mock)

        item = {
            "id": 2,
            "project_name": project_name,
            "episode_name": episode_name,
            "force_new": False,
            "translation_type": "new",
            "attempts": 0,
        }
        mock_queue = MagicMock()
        await process_translation_item(queue=mock_queue, item=item, global_config={})

        assert retry_mock.called
        meta = storage.load_episode_metadata(project_name, episode_name)
        assert "translation_warning" in meta


# ---------------------------------------------------------------------------
# 2. Target Language Propagation in reconstruct_cleaned_srt / export_ass
# ---------------------------------------------------------------------------

class TestTargetLanguagePropagation:

    def test_reconstruct_cleaned_srt_forwards_target_lang_to_export_ass(self, test_env, monkeypatch):
        """reconstruct_cleaned_srt must forward target_lang to export_ass for ASS format."""
        project_name = test_env["project_name"]
        episode_name = "S01E01"

        raw_ass = (
            "[Script Info]\nTitle: Test\nScriptType: v4.00+\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize\nStyle: Default, Arial, 20\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hello world\n"
        )
        storage.save_original_subtitle(project_name, episode_name, raw_ass, filename="S01E01.ass")

        cues = [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hello world", "translated": "Γεια σου κόσμε"}]
        storage.save_episode(project_name, episode_name, cues, {"clean_to_orig_map": {"0": 0}})

        out = reconstruct_cleaned_srt(project_name, episode_name, cues, target_lang="el")
        assert "Γεια σου κόσμε" in out
        # ASS reconstruct with Greek target_lang scales font size (20 * 0.85 = 17)
        assert "17" in out

    def test_reconstruct_cleaned_srt_alternate_six_param_signature(self, tmp_path):
        """Supports reconstruct_cleaned_srt(cleaned_data, clean_to_orig_map, original_srt_path, ... target_lang=...)."""
        ass_file = tmp_path / "test.ass"
        ass_file.write_text(
            "[Script Info]\nTitle: Test\nScriptType: v4.00+\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize\nStyle: Default, Arial, 20\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hello world\n",
            encoding="utf-8-sig"
        )

        cleaned_data = [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hello world", "translated": "Γεια σου κόσμε"}]
        clean_map = {0: 0}

        out = reconstruct_cleaned_srt(
            cleaned_data,
            clean_map,
            str(ass_file),
            source_ass_path=str(ass_file),
            target_lang="el"
        )
        assert "Γεια σου κόσμε" in out
        assert "17" in out

    def test_export_ass_directly(self):
        """export_ass wrapper works with target_lang and font_scale."""
        entries = [{
            "id": "1",
            "timecode": "00:00:01,000 --> 00:00:02,000",
            "original": "Test",
            "translated": "Δοκιμή",
            "_ass": {"kind": "dialogue", "style": "Default", "start_ms": 1000, "end_ms": 2000, "text": "Δοκιμή"}
        }]
        out = export_ass(entries, target_lang="el", font_scale=0.85)
        assert "Δοκιμή" in out


# ---------------------------------------------------------------------------
# 3. Sync ownership.db on Sidecar Rename (subtitle_naming.py)
# ---------------------------------------------------------------------------

class TestOwnershipSyncOnRename:

    def test_rename_syncs_ownership_ledger(self, test_env):
        """When an owned sidecar is renamed, ownership.db is updated to the new path."""
        folder = test_env["tmp_path"] / "media"
        folder.mkdir()

        old_sub = folder / "Show.S01E01.HDTV.AAC.el.srt"
        new_sub = folder / "Show.S01E01.Bluray.Opus.el.srt"
        old_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nΓεια\n", encoding="utf-8")

        # Register ownership on old_sub
        ownership.register_file(old_sub, project_name=test_env["project_name"], episode_name="S01E01")
        assert ownership.is_ours(old_sub) is True
        assert ownership.is_ours(new_sub) is False

        # Execute rename via rename_episode_subtitles_to_match_video
        success = rename_episode_subtitles_to_match_video(
            old_sub,
            new_sub,
            project_name=test_env["project_name"],
            episode_name="S01E01"
        )
        assert success is True
        assert not old_sub.exists()
        assert new_sub.exists()

        # Ownership must now follow the new path!
        assert ownership.is_ours(new_sub) is True
        assert ownership.is_ours(old_sub) is False

    def test_apply_plan_syncs_ownership_when_renaming(self, test_env):
        """apply_plan updates ownership for any owned sidecar in the plan."""
        folder = test_env["tmp_path"] / "media_plan"
        folder.mkdir()

        old_sub = folder / "Show.S01E02.HDTV.el.ass"
        new_sub = folder / "Show.S01E02.Bluray.el.ass"
        old_sub.write_text("[Script Info]\nTitle: Test\n", encoding="utf-8")

        ownership.register_file(old_sub, project_name=test_env["project_name"], episode_name="S01E02")
        assert ownership.is_ours(old_sub) is True

        plan = [{"old": str(old_sub), "new": str(new_sub), "status": "rename"}]
        renamed, errors = apply_plan(plan, project_name=test_env["project_name"], episode_name="S01E02")

        assert len(renamed) == 1
        assert not errors
        assert not old_sub.exists()
        assert new_sub.exists()
        assert ownership.is_ours(new_sub) is True


# ---------------------------------------------------------------------------
# 4. Safe Delete Translation with Disk Sidecar Prompt (routers/episodes.py)
# ---------------------------------------------------------------------------

class TestSafeDeleteTranslation:

    def test_delete_translation_without_disk_deletion(self, test_env):
        """delete_disk_file=False clears data.json and checkpoints, keeps disk files intact."""
        project_name = test_env["project_name"]
        episode_name = "S01E01"

        target_srt = test_env["tmp_path"] / "Show.S01E01.el.srt"
        target_ass = test_env["tmp_path"] / "Show.S01E01.el.ass"
        target_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nΓεια\n", encoding="utf-8")
        target_ass.write_text("[Script Info]\n", encoding="utf-8")

        ownership.register_file(target_srt, project_name=project_name, episode_name=episode_name)

        cues = [
            {"index": 1, "original": "Hello", "translated": "Γεια", "translations": {"el": "Γεια"}},
            {"index": 2, "original": "World", "translated": "Κόσμε", "translations": {"el": "Κόσμε"}},
        ]
        storage.save_episode(project_name, episode_name, cues, {
            "arr_source": True,
            "translated": True,
            "arr_has_target": True,
            "arr_target_path": str(target_srt),
        })

        # Call with delete_disk_file=False
        res = _delete_episode_translation(project_name, episode_name, delete_disk_file=False)

        assert isinstance(res, dict)
        assert res["status"] == "translation-deleted"
        assert str(target_srt) in res["detected_disk_files"]
        assert len(res["deleted_disk_files"]) == 0
        assert res["disk_file_ownership"][str(target_srt)] is True

        # Disk files must still exist!
        assert target_srt.exists()
        assert target_ass.exists()

        # In-app data cleared so re-translation can begin
        data = storage.load_episode(project_name, episode_name)
        assert all(line.get("translated") == "" and not line.get("translations") for line in data["data"])
        meta = storage.load_episode_metadata(project_name, episode_name)
        assert meta.get("translated") is False
        assert meta.get("translation_status") == "pending"

    def test_delete_translation_with_disk_deletion(self, test_env):
        """delete_disk_file=True deletes the detected sidecars from disk."""
        project_name = test_env["project_name"]
        episode_name = "S01E02"

        target_srt = test_env["tmp_path"] / "Show.S01E02.el.srt"
        target_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nΓεια\n", encoding="utf-8")
        ownership.register_file(target_srt, project_name=project_name, episode_name=episode_name)

        cues = [{"index": 1, "original": "Hello", "translated": "Γεια", "translations": {"el": "Γεια"}}]
        storage.save_episode(project_name, episode_name, cues, {
            "arr_source": True,
            "translated": True,
            "arr_has_target": True,
            "arr_target_path": str(target_srt),
        })

        res = _delete_episode_translation(project_name, episode_name, delete_disk_file=True)
        assert isinstance(res, dict)
        assert str(target_srt) in res["deleted_disk_files"]
        assert not target_srt.exists()
        assert ownership.is_ours(target_srt) is False

    def test_delete_translation_api_endpoint(self, test_env):
        """DELETE /projects/{project}/episodes/{ep}/translation endpoint tests."""
        client = TestClient(app)
        project_name = test_env["project_name"]
        episode_name = "S01E03"

        target_srt = test_env["tmp_path"] / "Show.S01E03.el.srt"
        target_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nΓεια\n", encoding="utf-8")

        cues = [{"index": 1, "original": "Hi", "translated": "Γεια", "translations": {"el": "Γεια"}}]
        storage.save_episode(project_name, episode_name, cues, {
            "arr_source": True,
            "translated": True,
            "arr_has_target": True,
            "arr_target_path": str(target_srt),
        })

        # 1. Calling without delete_disk_file (defaults to false)
        resp1 = client.delete(f"/projects/{project_name}/episodes/{episode_name}/translation")
        assert resp1.status_code == 200
        body1 = resp1.json()
        assert body1["status"] == "translation-deleted"
        assert target_srt.exists()  # Disk file preserved!

        # Re-populate translation
        cues[0]["translated"] = "Γεια"
        storage.save_episode(project_name, episode_name, cues, {
            "arr_source": True,
            "translated": True,
            "arr_has_target": True,
            "arr_target_path": str(target_srt),
        })

        # 2. Calling with ?delete_disk_file=true
        resp2 = client.delete(f"/projects/{project_name}/episodes/{episode_name}/translation?delete_disk_file=true")
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert str(target_srt) in body2["deleted_disk_files"]
        assert not target_srt.exists()  # Disk file deleted!
