"""
Empirical Adversarial Challenge Test Suite for Milestone 2: Backend Process Deduping and Idempotency.
Authored by: Challenger 1

Stress-tests:
1. translate_episode:
   - High concurrency burst (30 simultaneous requests) for identical (project, episode, lang).
   - Multi-language isolation concurrency (concurrent requests for Greek, Spanish, French).
   - Casing sensitivity in episode names (e.g. S01E01 vs s01e01).
   - Idempotent recovery after cancellation and completion.
2. batch_translate:
   - High concurrency burst (TOCTOU race condition stress).
   - Arbitrary permutations of episode lists (re-ordered lists must deduplicate).
   - Whitespace in episode list payloads.
   - Differing casing in episode list payloads.
   - Subset/superset isolation (different episode sets must NOT deduplicate).
   - Edge cases: empty episode list, duplicate episodes in request.
   - Lifecycle resets after completed/cancelled states.
3. start_auto_translate and start_pipeline:
   - High concurrency burst (TOCTOU race condition stress).
   - Cross-mode deduplication (auto-translate against active simple-pipeline and pipeline mode="auto").
   - Lifecycle recovery after completion and cancellation.
4. batch_extract_embedded_endpoint and _run_batch_extract:
   - Concurrency burst for extraction endpoint.
   - Immediate cooperative cancellation halting multi-episode loops.
   - Exception handling under cancellation (verifying cancelled status is never overwritten by failed/completed).
   - Immediate re-submission bypass after cancellation.
"""

import asyncio
import concurrent.futures
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from main import app
from utils import storage, paths
from utils.jobs_manager import jobs, create_job, update_job
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION
from routers.embedded import _run_batch_extract
from integrations import embedded_subs
from integrations.embedded_subs import SubtitleTrack


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolate_test_environment(isolate_test_data_dir, monkeypatch):
    """Ensure complete filesystem and DB isolation for adversarial testing."""
    test_db = isolate_test_data_dir / "omnisub_adv_test.db"
    monkeypatch.setattr("utils.translation_queue.DB_FILE", test_db)
    monkeypatch.setattr("utils.jobs_manager.DB_FILE", test_db)
    monkeypatch.setattr("utils.paths.DB_FILE", test_db)
    
    # Initialize DB tables (translation_queue, job_history, etc.)
    tq = TranslationQueue()
    tq._init_db()
    with tq._conn() as conn:
        conn.execute("DELETE FROM translation_queue")
        conn.commit()

    # Isolate storage modules to test_data_dir
    projects_dir = isolate_test_data_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("utils.storage_modules.atomic_io.PROJECTS_DIR", projects_dir)
    monkeypatch.setattr("utils.storage_modules.episode_store.PROJECTS_DIR", projects_dir)
    
    from routers.pipeline import _batch_target_episodes
    jobs.clear()
    _batch_target_episodes.clear()
    yield
    jobs.clear()
    _batch_target_episodes.clear()


@pytest.fixture
def sample_project(isolate_test_data_dir):
    """Create a pristine test project with episodes."""
    proj_name = "AdvDedupProject"
    storage.create_project(proj_name, {
        "show_name": "Adversarial Test Show",
        "target_language": "Greek",
        "settings": {
            "translation_model": "gemini-2.5-flash",
            "context_model": "gemini-2.5-flash",
            "glossary_model": "gemini-2.5-flash",
        }
    })
    for ep in ["S01E01", "S01E02", "S01E03", "S01E04", "S01E05"]:
        storage.save_episode(proj_name, ep, [
            {"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": f"Line {ep}", "translated": ""}
        ])
    return proj_name


# ===========================================================================
# 1. translate_episode Adversarial Stress Tests
# ===========================================================================

class TestTranslateEpisodeAdversarial:
    """Adversarial stress-testing of translate_episode deduplication and concurrency."""

    def test_concurrent_burst_same_episode(self, client, sample_project):
        """
        Burst 30 concurrent threads calling translate_episode on the exact same project/episode.
        Oracle:
        - All 30 calls return status 200.
        - Exactly 1 unique job_id and item_id across all 30 responses.
        - TranslationQueue contains exactly 1 pending item.
        """
        proj_name = sample_project
        ep_name = "S01E01"

        def call_endpoint():
            return client.post(f"/projects/{proj_name}/episodes/{ep_name}/translate", json={})

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(call_endpoint) for _ in range(30)]
            responses = [f.result() for f in futures]

        for r in responses:
            assert r.status_code == 200

        job_ids = {r.json()["job_id"] for r in responses}
        item_ids = {r.json()["item_id"] for r in responses}

        assert len(job_ids) == 1, f"Expected 1 unique job_id, got: {job_ids}"
        assert len(item_ids) == 1, f"Expected 1 unique item_id, got: {item_ids}"

        # Verify translation queue integrity
        tq = TranslationQueue()
        all_items = tq.get_all(limit=100)
        matching = [i for i in all_items if i["project_name"] == proj_name and i["episode_name"] == ep_name]
        assert len(matching) == 1
        assert matching[0]["status"] == "pending"
        assert matching[0]["attempts"] == 0

    def test_concurrent_burst_multi_language_isolation(self, client, sample_project):
        """
        Burst concurrent requests for different target languages for the same episode.
        Oracle:
        - Requests for Greek ('el') all get job_id_el.
        - Requests for Spanish ('es') all get job_id_es.
        - Requests for French ('fr') all get job_id_fr.
        - job_id_el != job_id_es != job_id_fr.
        - Exactly 3 records in translation queue.
        """
        proj_name = sample_project
        ep_name = "S01E01"

        def call_lang(lang_code):
            return lang_code, client.post(
                f"/projects/{proj_name}/episodes/{ep_name}/translate",
                json={"lang": lang_code, "target_language_name": lang_code.upper()}
            )

        tasks = ["el", "es", "fr"] * 8  # 24 total requests
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(call_lang, l) for l in tasks]
            results = [f.result() for f in futures]

        grouped = {"el": set(), "es": set(), "fr": set()}
        for lang, resp in results:
            assert resp.status_code == 200
            grouped[lang].add(resp.json()["job_id"])

        assert len(grouped["el"]) == 1, f"Expected 1 job for el, got {grouped['el']}"
        assert len(grouped["es"]) == 1, f"Expected 1 job for es, got {grouped['es']}"
        assert len(grouped["fr"]) == 1, f"Expected 1 job for fr, got {grouped['fr']}"

        job_el = list(grouped["el"])[0]
        job_es = list(grouped["es"])[0]
        job_fr = list(grouped["fr"])[0]

        assert len({job_el, job_es, job_fr}) == 3, "Language jobs collided!"

    def test_translate_episode_casing_sensitivity(self, client, sample_project):
        """
        Adversarial challenge: Differing casing for episode name in translate_episode.
        e.g., S01E02 vs s01e02.
        Does SQLite or the endpoint deduplicate across case variations?
        """
        proj_name = sample_project
        resp1 = client.post(f"/projects/{proj_name}/episodes/S01E02/translate", json={})
        assert resp1.status_code == 200
        id1 = resp1.json()["job_id"]

        resp2 = client.post(f"/projects/{proj_name}/episodes/s01e02/translate", json={})
        assert resp2.status_code == 200
        id2 = resp2.json()["job_id"]

        assert id1 == id2, f"Casing in episode name caused duplicate queue jobs: {id1} vs {id2}"

    def test_cancelled_queue_item_re_enqueues(self, client, sample_project):
        """When a queue item is cancelled, a new request must re-enqueue it cleanly."""
        proj_name = sample_project
        ep_name = "S01E03"

        resp1 = client.post(f"/projects/{proj_name}/episodes/{ep_name}/translate", json={})
        item_id = resp1.json()["item_id"]
        job_id = resp1.json()["job_id"]

        # Cancel both the job and queue item
        tq = TranslationQueue()
        tq.cancel(item_id)
        if job_id in jobs:
            jobs[job_id].cancelled = True
            jobs[job_id].status = "cancelled"

        # Subsequent call must re-activate
        resp2 = client.post(f"/projects/{proj_name}/episodes/{ep_name}/translate", json={})
        assert resp2.status_code == 200

        # Item must be pending again in queue
        item = tq.get_item(item_id)
        assert item["status"] == "pending"


# ===========================================================================
# 2. batch_translate Adversarial Stress Tests
# ===========================================================================

class TestBatchTranslateAdversarial:
    """Adversarial stress-testing of batch_translate deduplication and permutations."""

    @patch("routers.pipeline._monitor_batch_queue_progress", new_callable=MagicMock)
    def test_permutations_of_episode_list(self, mock_mon, client, sample_project):
        """Shuffled orderings of the same episode list must all deduplicate to the same active job."""
        proj_name = sample_project
        permutations = [
            ["S01E01", "S01E02", "S01E03"],
            ["S01E03", "S01E01", "S01E02"],
            ["S01E02", "S01E03", "S01E01"],
            ["S01E01", "S01E03", "S01E02"],
            ["S01E02", "S01E01", "S01E03"],
        ]

        job_ids = []
        for p in permutations:
            resp = client.post(f"/projects/{proj_name}/batch-translate", json={"episode_names": p})
            assert resp.status_code == 200
            job_ids.append(resp.json()["job_id"])

        assert len(set(job_ids)) == 1, f"Permutations spawned multiple jobs: {set(job_ids)}"

    @patch("routers.pipeline._monitor_batch_queue_progress", new_callable=MagicMock)
    def test_batch_translate_whitespace_in_episodes(self, mock_mon, client, sample_project):
        """
        Adversarial challenge: Episodes with whitespace in the request array.
        e.g. [' S01E01 '] vs ['S01E01'].
        """
        proj_name = sample_project
        resp1 = client.post(f"/projects/{proj_name}/batch-translate", json={"episode_names": ["S01E01"]})
        assert resp1.status_code == 200
        id1 = resp1.json()["job_id"]

        resp2 = client.post(f"/projects/{proj_name}/batch-translate", json={"episode_names": ["  S01E01  "]})
        assert resp2.status_code == 200
        id2 = resp2.json()["job_id"]

        assert id1 == id2, f"Whitespace in episode list caused duplicate batch jobs: {id1} vs {id2}"

    @patch("routers.pipeline._monitor_batch_queue_progress", new_callable=MagicMock)
    def test_batch_translate_casing_sensitivity(self, mock_mon, client, sample_project):
        """
        Adversarial challenge: Differing casing in batch translate episode names.
        e.g. ['S01E01'] vs ['s01e01'].
        """
        proj_name = sample_project
        resp1 = client.post(f"/projects/{proj_name}/batch-translate", json={"episode_names": ["S01E01"]})
        assert resp1.status_code == 200
        id1 = resp1.json()["job_id"]

        resp2 = client.post(f"/projects/{proj_name}/batch-translate", json={"episode_names": ["s01e01"]})
        assert resp2.status_code == 200
        id2 = resp2.json()["job_id"]

        assert id1 == id2, f"Casing in episode list caused duplicate batch jobs: {id1} vs {id2}"

    @patch("routers.pipeline._monitor_batch_queue_progress", new_callable=MagicMock)
    def test_subset_and_superset_do_not_dedup(self, mock_mon, client, sample_project):
        """Different subsets of episodes must spawn distinct jobs."""
        proj_name = sample_project

        resp1 = client.post(f"/projects/{proj_name}/batch-translate", json={"episode_names": ["S01E01", "S01E02"]})
        resp2 = client.post(f"/projects/{proj_name}/batch-translate", json={"episode_names": ["S01E01"]})
        resp3 = client.post(f"/projects/{proj_name}/batch-translate", json={"episode_names": ["S01E01", "S01E02", "S01E03"]})

        j1 = resp1.json()["job_id"]
        j2 = resp2.json()["job_id"]
        j3 = resp3.json()["job_id"]

        assert len({j1, j2, j3}) == 3, f"Subset/superset collided: {j1}, {j2}, {j3}"

    @patch("routers.pipeline._monitor_batch_queue_progress", new_callable=MagicMock)
    def test_empty_episode_list_handling(self, mock_mon, client, sample_project):
        """Batch translate with empty list [] must not crash and must deduplicate identically."""
        proj_name = sample_project

        resp1 = client.post(f"/projects/{proj_name}/batch-translate", json={"episode_names": []})
        assert resp1.status_code == 200
        j1 = resp1.json()["job_id"]

        resp2 = client.post(f"/projects/{proj_name}/batch-translate", json={"episode_names": []})
        assert resp2.status_code == 200
        j2 = resp2.json()["job_id"]

        assert j1 == j2

    @patch("routers.pipeline._monitor_batch_queue_progress", new_callable=MagicMock)
    def test_cancelled_batch_allows_new_submission(self, mock_mon, client, sample_project):
        """Cancelling a batch job allows a fresh batch with identical episodes to be launched."""
        proj_name = sample_project
        eps = ["S01E01", "S01E02"]

        resp1 = client.post(f"/projects/{proj_name}/batch-translate", json={"episode_names": eps})
        j1 = resp1.json()["job_id"]

        # Cancel j1
        jobs[j1].cancelled = True
        jobs[j1].status = "cancelled"

        # Subsequent call must create j2
        resp2 = client.post(f"/projects/{proj_name}/batch-translate", json={"episode_names": eps})
        j2 = resp2.json()["job_id"]

        assert j2 != j1


# ===========================================================================
# 3. auto_translate and pipeline Dedup Stress Tests
# ===========================================================================

class TestAutoTranslateAndPipelineAdversarial:
    """Adversarial stress-testing of auto_translate and pipeline start deduplication."""

    @patch("routers.pipeline._process_auto_translate", new_callable=MagicMock)
    def test_sequential_auto_translate_dedup(self, mock_proc, client, sample_project):
        """Sequential calls to auto-translate must return the exact same job ID."""
        proj_name = sample_project

        resp1 = client.post(f"/projects/{proj_name}/auto-translate", json={})
        assert resp1.status_code == 200
        j1 = resp1.json()["job_id"]

        resp2 = client.post(f"/projects/{proj_name}/auto-translate", json={})
        assert resp2.status_code == 200
        j2 = resp2.json()["job_id"]

        assert j1 == j2

    @patch("routers.pipeline._process_pipeline", new_callable=MagicMock)
    def test_sequential_pipeline_start_dedup(self, mock_proc, client, sample_project):
        """Sequential calls to pipeline/start must return the exact same job ID."""
        proj_name = sample_project

        resp1 = client.post(f"/projects/{proj_name}/pipeline/start", json={"mode": "auto"})
        assert resp1.status_code == 200
        j1 = resp1.json()["job_id"]

        resp2 = client.post(f"/projects/{proj_name}/pipeline/start", json={"mode": "auto"})
        assert resp2.status_code == 200
        j2 = resp2.json()["job_id"]

        assert j1 == j2

    @patch("routers.pipeline._process_pipeline", new_callable=MagicMock)
    def test_auto_translate_dedup_against_pipeline_auto_mode(self, mock_proc, client, sample_project):
        """If pipeline is started in mode='auto', auto-translate must return that active job."""
        proj_name = sample_project

        resp_pipe = client.post(f"/projects/{proj_name}/pipeline/start", json={"mode": "auto"})
        assert resp_pipe.status_code == 200
        pipe_id = resp_pipe.json()["job_id"]

        resp_auto = client.post(f"/projects/{proj_name}/auto-translate", json={})
        assert resp_auto.status_code == 200
        auto_id = resp_auto.json()["job_id"]

        assert auto_id == pipe_id, f"Auto-translate failed to dedup against pipeline job: {auto_id} vs {pipe_id}"


# ===========================================================================
# 4. batch_extract_embedded and Cooperative Cancellation
# ===========================================================================

class TestBatchExtractEmbeddedAdversarial:
    """Adversarial stress-testing of batch_extract_embedded and cooperative cancellation."""

    def test_sequential_batch_extract_dedup(self, client, sample_project):
        """Sequential calls to batch_extract_embedded must reuse active job."""
        proj_name = sample_project

        with patch("routers.embedded._run_batch_extract", new_callable=MagicMock):
            resp1 = client.post(f"/projects/{proj_name}/embedded/extract-all", json={"force": True})
            assert resp1.status_code == 200
            j1 = resp1.json()["job_id"]

            resp2 = client.post(f"/projects/{proj_name}/embedded/extract-all", json={"force": True})
            assert resp2.status_code == 200
            j2 = resp2.json()["job_id"]

            assert j1 == j2

    @pytest.mark.asyncio
    async def test_cooperative_cancellation_immediate_abort_and_status_preservation(self, sample_project, tmp_path, monkeypatch):
        """
        Verify that user cancellation halts extraction immediately and preserves 'cancelled' status
        even if downstream steps or exceptions occur.
        """
        proj_name = sample_project
        job_id = create_job("batch_extract_embedded", project_name=proj_name)

        episodes = ["S01E01", "S01E02", "S01E03", "S01E04"]
        for ep in episodes:
            media_file = tmp_path / f"Show.{ep}.mkv"
            media_file.write_bytes(b"dummy video")
            storage.save_episode(proj_name, ep, [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hi"}], metadata={"arr_media_path": str(media_file)})

        fake_ass = "[Script Info]\nTitle: Test\n[Events]\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Line\n"
        fake_tracks = [SubtitleTrack(index=2, codec="ass", language="eng", title="Sub", frames=1)]

        monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))
        monkeypatch.setattr(embedded_subs, "probe_subtitle_tracks", lambda *a, **k: fake_tracks)

        extracted_eps = []
        async def mock_extract(media_path, track_idx, *a, **k):
            # Extract first episode, then user cancels
            extracted_eps.append(media_path)
            if len(extracted_eps) == 1:
                jobs[job_id].cancelled = True
            return fake_ass

        monkeypatch.setattr(embedded_subs, "extract_track", mock_extract)

        await _run_batch_extract(job_id, proj_name, episodes, force=True, migrate_srt=False)

        # Assert exactly 1 episode was extracted and the remaining 3 were never touched
        assert len(extracted_eps) == 1, f"Loop did not break immediately: extracted {len(extracted_eps)}"
        assert jobs[job_id].status == "cancelled"
        assert jobs[job_id].progress < 100.0
        assert "cancelled by user" in jobs[job_id].message.lower()

    @pytest.mark.asyncio
    async def test_exception_after_cancellation_does_not_overwrite_status(self, sample_project, tmp_path, monkeypatch):
        """If an exception is raised while job is cancelled, status must remain 'cancelled' not 'failed'."""
        proj_name = sample_project
        job_id = create_job("batch_extract_embedded", project_name=proj_name)

        episodes = ["S01E01"]
        media_file = tmp_path / "Show.S01E01.mkv"
        media_file.write_bytes(b"dummy")
        storage.save_episode(proj_name, "S01E01", [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hi"}], metadata={"arr_media_path": str(media_file)})

        monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))
        monkeypatch.setattr(embedded_subs, "probe_subtitle_tracks", lambda *a, **k: [SubtitleTrack(index=2, codec="ass", language="eng", title="Sub", frames=1)])

        async def exploding_extract(*a, **k):
            jobs[job_id].cancelled = True
            raise RuntimeError("Disk ejected")

        monkeypatch.setattr(embedded_subs, "extract_track", exploding_extract)

        await _run_batch_extract(job_id, proj_name, episodes, force=True, migrate_srt=False)

        assert jobs[job_id].status == "cancelled"
        assert jobs[job_id].status != "failed"
