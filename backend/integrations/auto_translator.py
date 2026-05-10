"""
Auto-Translator — Autonomous translation pipeline for *arr stack.

Polls Bazarr for missing subtitles, translates via OmbiSub's existing
batch pipeline, and delivers .srt files to the media directory for
Plex/Jellyfin auto-detection.

This is the background service that makes OmbiSub a "set-and-forget"
translation engine. It runs entirely headless — no user interaction
required per episode.

Architecture:
    AutoTranslator (this module)
        -> bazarr.py: discover missing subs, read English .srt, deliver output
        -> main.py: _translate_episode_atomic() for actual translation
        -> storage.py: project/episode persistence
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from .bazarr import (
    BazarrConfig,
    MissingSubtitleItem,
    get_missing_subtitles,
    read_subtitle_file,
    deliver_translated_subtitle,
    make_project_name,
    make_episode_name,
)

logger = logging.getLogger(__name__)


@dataclass
class AutoTranslateResult:
    """Result of translating a single item."""
    item: MissingSubtitleItem
    success: bool
    output_path: str = ""
    error: str = ""
    project_name: str = ""
    episode_name: str = ""


@dataclass
class AutoTranslatorStats:
    """Running statistics for the auto-translator."""
    total_scanned: int = 0
    total_translated: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    last_scan_time: Optional[str] = None
    last_scan_found: int = 0
    is_running: bool = False
    current_item: Optional[str] = None
    history: List[Dict] = field(default_factory=list)  # last N results


class AutoTranslator:
    """Background service that auto-translates missing subtitles.

    Lifecycle:
        1. start() — begins the polling loop as an asyncio task
        2. _poll_loop() — sleeps for poll_interval, then calls _check_and_translate()
        3. _check_and_translate() — queries Bazarr, processes each missing item
        4. _process_item() — creates project if needed, imports SRT, translates, delivers
        5. stop() — cancels the polling task

    The service can also be triggered manually via scan_now().
    """

    MAX_HISTORY = 50  # Keep last N results

    def __init__(self, config: BazarrConfig):
        self.config = config
        self.stats = AutoTranslatorStats()
        self._task: Optional[asyncio.Task] = None
        self._scan_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self):
        """Start the polling loop."""
        if self.is_running:
            logger.warning("AutoTranslator already running")
            return
        self._task = asyncio.create_task(self._poll_loop(), name="auto_translator")
        self.stats.is_running = True
        logger.info(
            f"AutoTranslator started — polling every {self.config.poll_interval_minutes}m "
            f"for {self.config.target_language} subs"
        )

    async def stop(self):
        """Stop the polling loop gracefully."""
        self.stats.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("AutoTranslator stopped")

    async def scan_now(self) -> List[AutoTranslateResult]:
        """Manually trigger a scan and translation pass. Returns results."""
        return await self._check_and_translate()

    async def preview(self) -> List[Dict]:
        """Preview what would be translated without triggering translation.

        Returns a list of items that Bazarr reports as missing target subs.
        """
        items = await get_missing_subtitles(self.config)
        return [
            {
                "title": item.title,
                "media_type": item.media_type,
                "season": item.season,
                "episode": item.episode,
                "episode_title": item.episode_title,
                "media_path": item.media_path,
                "english_sub_path": item.english_sub_path,
                "project_name": make_project_name(item.title),
                "episode_name": make_episode_name(item),
            }
            for item in items
        ]

    # -- internal -----------------------------------------------------------

    async def _poll_loop(self):
        """Main polling loop — runs until cancelled."""
        while True:
            try:
                await self._check_and_translate()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"AutoTranslator poll error: {e}", exc_info=True)

            await asyncio.sleep(self.config.poll_interval_minutes * 60)

    async def _check_and_translate(self) -> List[AutoTranslateResult]:
        """Single scan+translate iteration.

        Uses a lock to prevent overlapping scans from manual + scheduled triggers.
        """
        if self._scan_lock.locked():
            logger.info("Scan already in progress, skipping")
            return []

        async with self._scan_lock:
            self.stats.last_scan_time = datetime.now().isoformat()

            # Discover missing subs via Bazarr
            items = await get_missing_subtitles(self.config)
            self.stats.last_scan_found = len(items)
            self.stats.total_scanned += len(items)

            if not items:
                logger.info("No missing subtitles found")
                return []

            logger.info(f"Found {len(items)} items missing {self.config.target_language} subs")

            # Process each item
            results = []
            for item in items:
                label = f"{item.title}"
                if item.media_type == "series":
                    label += f" S{item.season:02d}E{item.episode:02d}"
                self.stats.current_item = label

                try:
                    result = await self._process_item(item)
                    results.append(result)

                    if result.success:
                        self.stats.total_translated += 1
                        logger.info(f"Translated: {label} -> {result.output_path}")
                    else:
                        self.stats.total_failed += 1
                        logger.warning(f"Failed: {label} — {result.error}")

                    # Record in history
                    self.stats.history.append({
                        "title": label,
                        "success": result.success,
                        "output_path": result.output_path,
                        "error": result.error,
                        "timestamp": datetime.now().isoformat(),
                    })
                    if len(self.stats.history) > self.MAX_HISTORY:
                        self.stats.history = self.stats.history[-self.MAX_HISTORY:]

                except Exception as e:
                    logger.error(f"Error processing {label}: {e}", exc_info=True)
                    self.stats.total_failed += 1
                    results.append(AutoTranslateResult(
                        item=item, success=False, error=str(e)
                    ))

            self.stats.current_item = None
            return results

    async def _process_item(self, item: MissingSubtitleItem) -> AutoTranslateResult:
        """Process a single missing-subtitle item end-to-end.

        1. Determine/create OmbiSub project
        2. Read the English subtitle file
        3. Parse SRT and save as episode
        4. Translate using existing batch infrastructure
        5. Build output SRT and deliver to media directory
        """
        # Lazy imports to avoid circular dependencies
        from utils import storage
        from utils.srt_parser import parse_srt, reconstruct_srt
        from utils.rate_limiter import DailyLimitExhausted

        project_name = make_project_name(item.title)
        episode_name = make_episode_name(item)

        # 1. Ensure project exists
        existing_projects = storage.list_projects()
        if project_name not in existing_projects:
            project_type = "show" if item.media_type == "series" else "movie"
            storage.create_project(project_name, {
                "show_name": item.title,
                "target_language": self.config.target_language,
                "type": project_type,
                "settings": {
                    "scan_model": "gemini-flash-lite-latest",
                    "translation_model": "gemini-flash-latest",
                    "apply_subtitle_edit_fixes": True,
                },
                "bazarr_source": True,
                "series_type": item.series_type,
            })
            logger.info(f"Created project: {project_name}")

        # 2. Check if episode already translated
        existing_meta = storage.load_episode_metadata(project_name, episode_name)
        if existing_meta and existing_meta.get("translated"):
            # Already done — just re-deliver in case the file was deleted
            ep_data = storage.load_episode(project_name, episode_name)
            if ep_data:
                srt_output = reconstruct_srt(ep_data["data"])
                output_path = deliver_translated_subtitle(
                    item.media_path, srt_output, self.config.target_language_code2
                )
                self.stats.total_skipped += 1
                return AutoTranslateResult(
                    item=item, success=True, output_path=output_path,
                    project_name=project_name, episode_name=episode_name,
                )

        # 3. Read English subtitle from disk
        srt_content = read_subtitle_file(item.english_sub_path)
        if not srt_content:
            return AutoTranslateResult(
                item=item, success=False,
                error=f"Cannot read English sub: {item.english_sub_path}",
                project_name=project_name, episode_name=episode_name,
            )

        # 4. Parse SRT and import into OmbiSub
        parsed = parse_srt(srt_content)
        if not parsed:
            return AutoTranslateResult(
                item=item, success=False,
                error="Failed to parse SRT file (invalid format)",
                project_name=project_name, episode_name=episode_name,
            )

        storage.save_original_srt(project_name, episode_name, srt_content)
        storage.save_episode(project_name, episode_name, parsed, {
            "original_filename": Path(item.english_sub_path).name,
            "line_count": len(parsed),
            "bazarr_media_path": item.media_path,
            "bazarr_source": True,
        })

        # 5. Translate using the existing atomic episode translator
        try:
            success = await self._translate_episode(
                project_name, episode_name, item
            )
        except DailyLimitExhausted:
            return AutoTranslateResult(
                item=item, success=False,
                error="Daily API rate limit reached",
                project_name=project_name, episode_name=episode_name,
            )

        if not success:
            return AutoTranslateResult(
                item=item, success=False,
                error="Translation failed (see job logs)",
                project_name=project_name, episode_name=episode_name,
            )

        # 6. Build output SRT and deliver to media directory
        ep_data = storage.load_episode(project_name, episode_name)
        if not ep_data:
            return AutoTranslateResult(
                item=item, success=False,
                error="Episode data missing after translation",
                project_name=project_name, episode_name=episode_name,
            )

        srt_output = reconstruct_srt(ep_data["data"])
        output_path = deliver_translated_subtitle(
            item.media_path, srt_output, self.config.target_language_code2
        )

        return AutoTranslateResult(
            item=item, success=True, output_path=output_path,
            project_name=project_name, episode_name=episode_name,
        )

    async def _translate_episode(
        self,
        project_name: str,
        episode_name: str,
        item: MissingSubtitleItem,
    ) -> bool:
        """Run translation using the existing _translate_episode_atomic infrastructure.

        Creates a minimal job for tracking, reuses the shared agent pattern.
        """
        # Lazy imports
        from utils import storage
        from utils.rate_limiter import RateLimiter
        from utils.translation_memory import TranslationMemory
        from adk_agents.translation_pipeline import create_translation_pipeline

        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            return False

        global_config = storage.load_global_config()
        target_lang = metadata.get("target_language", self.config.target_language)
        model = global_config.get("default_translation_model", "gemini-flash-latest")

        # Create the shared translation agent
        shared_agent = create_translation_pipeline(
            project_name=project_name,
            target_language=target_lang,
            glossary=metadata.get("glossary", {"terms": []}),
            context_guide=metadata.get("context_guide", ""),
            translator_model=model,
            skip_glossary_step=True,
            temperature=global_config.get("temperature"),
            top_k=global_config.get("top_k"),
            top_p=global_config.get("top_p"),
        )

        # Create a job for tracking (imports from main at call time)
        import main as main_module
        job_id = main_module.create_job("auto_translate")
        main_module.update_job(
            job_id, status="running", message=f"Auto-translating {item.title}"
        )

        concurrent_limit = global_config.get("concurrent_scenes", 3)
        semaphore = asyncio.Semaphore(concurrent_limit)

        try:
            success = await main_module._translate_episode_atomic(
                job_id=job_id,
                project_name=project_name,
                episode_name=episode_name,
                target_lang=target_lang,
                shared_agent=shared_agent,
                rate_limiter=main_module.translation_rate_limiter,
                semaphore=semaphore,
                global_config=global_config,
                total_episodes=1,
                episode_idx=0,
                start_progress=0.0,
            )

            status = "completed" if success else "failed"
            main_module.update_job(
                job_id, status=status, progress=100.0,
                message=f"Auto-translate {'done' if success else 'failed'}: {episode_name}",
            )
            return success

        except Exception as e:
            main_module.update_job(
                job_id, status="failed",
                message=f"Auto-translate error: {str(e)}",
                log=str(e),
            )
            raise

    def get_status(self) -> Dict:
        """Return current status and statistics as a dict."""
        return {
            "enabled": self.config.enabled,
            "is_running": self.is_running,
            "poll_interval_minutes": self.config.poll_interval_minutes,
            "target_language": self.config.target_language,
            "bazarr_url": self.config.base_url,
            "media_types": self.config.media_types,
            "stats": {
                "total_scanned": self.stats.total_scanned,
                "total_translated": self.stats.total_translated,
                "total_failed": self.stats.total_failed,
                "total_skipped": self.stats.total_skipped,
                "last_scan_time": self.stats.last_scan_time,
                "last_scan_found": self.stats.last_scan_found,
                "current_item": self.stats.current_item,
            },
            "history": self.stats.history[-10:],  # Last 10 for the API
        }
