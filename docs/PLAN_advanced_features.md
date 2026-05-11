# Advanced Features Implementation Plan

## Overview

Six implementation plans that transform OmbiSub from a manual translator into an autonomous, self-improving subtitle translation pipeline integrated with the *arr stack. Each plan is self-contained with exact file paths, line numbers, and code patterns.

**Dependency order:**

1. Plan A — Translation Memory + Vector DB *(foundation — everything else reads/writes the TM)*
2. Plan B — Bazarr Integration *(enables autonomous trigger)*
3. Plan C — Character Voice Profiles *(critical for Greek gender/formality)*
4. Plan D — Reviewer Agent *(autonomous quality gate)*
5. Plan E — Rolling Episode Summaries *(long-show coherence)*
6. Plan F — User-Edit Feedback Loop *(self-improvement)*

---

# Plan A: Translation Memory + Vector DB

## Purpose

Store every translated line as a searchable record. Before calling Gemini, retrieve similar past translations as few-shot examples. High-similarity matches (>0.95) skip the LLM entirely.

## Dependencies

Add to `backend/requirements.txt`:

```
lancedb>=0.4.0
sentence-transformers>=2.2.0
```

LanceDB is file-based (no server), stores vectors + metadata in a single directory. sentence-transformers provides the embedding model — `all-MiniLM-L6-v2` is 80MB, fast, and good enough for subtitle similarity.

## File: `backend/utils/translation_memory.py` (CREATE)

```python
"""
Translation Memory — Vector-backed subtitle translation cache.

Stores source→target pairs with embeddings for semantic retrieval.
High-similarity matches bypass the LLM entirely.
"""

import lancedb
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from sentence_transformers import SentenceTransformer
from datetime import datetime

TM_DIR = Path(__file__).resolve().parent.parent / "translation_memory"

class TranslationMemory:
    """Per-project translation memory backed by LanceDB."""

    def __init__(self, project_name: str, model_name: str = "all-MiniLM-L6-v2"):
        self._project = project_name
        self._db_path = TM_DIR / project_name
        self._db_path.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(self._db_path))
        self._embedder = None  # Lazy-load
        self._model_name = model_name
        self._table_name = "translations"

    def _get_embedder(self) -> SentenceTransformer:
        if self._embedder is None:
            self._embedder = SentenceTransformer(self._model_name)
        return self._embedder

    def _ensure_table(self):
        """Create the table if it doesn't exist."""
        if self._table_name not in self._db.table_names():
            # Create with schema via initial empty-ish insert then delete,
            # or just create on first real insert
            pass  # Table created on first add_translations call

    def add_translations(
        self,
        source_lines: List[str],
        target_lines: List[str],
        episode_name: str,
        character: str = "",
        is_user_edited: bool = False,
    ):
        """Store translated pairs with embeddings."""
        # ... implementation details below

    def search(
        self,
        source_lines: List[str],
        top_k: int = 3,
        similarity_threshold: float = 0.80,
    ) -> Dict[int, List[Dict]]:
        """For each source line, find similar past translations.

        Returns: {line_index: [{source, target, score, episode, is_edited}]}
        """
        # ... implementation details below

    def get_exact_matches(
        self,
        source_lines: List[str],
        threshold: float = 0.95,
    ) -> Dict[int, str]:
        """Return near-exact matches that can skip the LLM.

        Returns: {line_index: target_text} for lines above threshold.
        """
        # ... implementation details below

    def get_stats(self) -> Dict:
        """Return record count, unique episodes, etc."""
```

### Key implementation details

**`add_translations` method:**

- Embed each source line using the sentence transformer
- Build records: `{source, target, embedding, episode, character, is_user_edited, timestamp}`
- Upsert into LanceDB table (use source text hash as dedup key)
- If `is_user_edited=True`, set a `priority` field higher — these are gold examples

**`search` method:**

- Batch-embed all source lines in one call (faster than one-by-one)
- For each embedding, query LanceDB with `table.search(embedding).limit(top_k)`
- Filter results below `similarity_threshold`
- Return grouped by line index

**`get_exact_matches` method:**

- Call `search` with threshold=0.95, top_k=1
- For each result above threshold, return the target text directly
- These lines will skip the LLM call entirely

### Embedding model loading (singleton)

The sentence-transformers model should load once per process, not per-project. Create a module-level singleton:

```python
_EMBEDDER_CACHE = {}

def get_embedder(model_name: str = "all-MiniLM-L6-v2") -> SentenceTransformer:
    if model_name not in _EMBEDDER_CACHE:
        _EMBEDDER_CACHE[model_name] = SentenceTransformer(model_name)
    return _EMBEDDER_CACHE[model_name]
```

Pass this to TranslationMemory instead of creating a new one per instance.

## Wire into translation flow

### Modify `_translate_episode_atomic()` in `backend/main.py` (line 1723)

**Before scene translation (after line 1748):**

```python
# Load TM for this project
from utils.translation_memory import TranslationMemory
tm = TranslationMemory(project_name)

# Check for exact matches across all lines
all_source_lines = [line.get("original", "") for line in parsed_srt]
exact_matches = tm.get_exact_matches(all_source_lines, threshold=0.95)

# Pre-fill translated_map with exact matches (these skip the LLM)
for idx, target_text in exact_matches.items():
    translated_map[idx] = target_text

if exact_matches:
    update_job(job_id, log=f"{episode_name}: {len(exact_matches)} lines reused from TM (skipped LLM)")
```

**Modify scene prompt building to include TM examples:**

Inside `_translate_scene_task` (line 1756), before building the prompt (line 1780):

```python
# Retrieve similar past translations as few-shot examples
source_texts = [line.get("original", "") for line in lines]
tm_results = tm.search(source_texts, top_k=2, similarity_threshold=0.80)

# Build few-shot block (max 5 examples to avoid bloating prompt)
few_shot_examples = []
seen = set()
for line_idx, matches in tm_results.items():
    for match in matches:
        key = match["source"]
        if key not in seen and len(few_shot_examples) < 5:
            seen.add(key)
            few_shot_examples.append(f"  EN: {match['source']}\n  EL: {match['target']}")

few_shot_block = ""
if few_shot_examples:
    few_shot_block = "Previous translations for reference:\n" + "\n".join(few_shot_examples) + "\n\n"
```

Then prepend `few_shot_block` to the prompt in `build_translation_prompt()`. This requires modifying `build_translation_prompt` in `translator_agent.py` (line 91) to accept an optional `few_shot_context` parameter.

**After episode succeeds (after line 1868):**

```python
# Store translations in TM
source_lines = [line.get("original", "") for line in parsed_srt]
target_lines = [line.get("translated", "") for line in parsed_srt]
tm.add_translations(source_lines, target_lines, episode_name)
```

### Modify `build_translation_prompt()` in `backend/adk_agents/translator_agent.py` (line 91)

```python
def build_translation_prompt(
    lines: List[str],
    target_language: str,
    context_line: str = "",
    few_shot_context: str = "",   # NEW parameter
) -> str:
    numbered = "\n".join(
        f"{i + 1}| {line.replace(chr(10), '<br>')}" for i, line in enumerate(lines)
    )
    ctx = f"[prev] {context_line}\n" if context_line else ""
    fs = f"{few_shot_context}\n" if few_shot_context else ""
    return f"Translate to {target_language}:\n\n{fs}{ctx}{numbered}"
```

## Skip already-matched lines from scene translation

Lines that got exact TM matches should be excluded from the prompt sent to Gemini. In `_translate_scene_task`, filter out lines whose `global_idx` is already in `translated_map`:

```python
lines_to_translate = []
original_indices = []
for j, line in enumerate(lines):
    global_idx = scene["start_index"] + j
    if global_idx not in translated_map:  # Not already matched by TM
        lines_to_translate.append(line)
        original_indices.append(j)

if not lines_to_translate:
    # All lines matched by TM — skip API call entirely
    update_job(job_id, scene_status={scene_label: "completed"})
    return
```

Then adjust the numbered output parsing to map back to original indices.

## API endpoint

Add to `backend/main.py`:

```python
@app.get("/projects/{project_name}/tm/stats")
async def tm_stats(project_name: str):
    tm = TranslationMemory(project_name)
    return tm.get_stats()
```

## Glossary-as-RAG (enhancement to Plan A)

Instead of injecting the full glossary into every prompt, retrieve only relevant terms.

### Modify `_build_glossary_context()` in `translator_agent.py` (line 18)

Add a new function that filters the glossary by what's actually in the current chunk:

```python
def _filter_glossary_for_chunk(glossary: Dict, source_lines: List[str]) -> Dict:
    """Return only glossary terms that appear in the source lines."""
    if not glossary or not glossary.get("terms"):
        return glossary

    source_text = " ".join(source_lines).lower()
    relevant_terms = []
    for term in glossary["terms"]:
        term_text = term.get("term", "").lower()
        if term_text and term_text in source_text:
            relevant_terms.append(term)

    return {"terms": relevant_terms}
```

Call this in `_translate_scene_task` before creating the agent or building the prompt. Pass the filtered glossary instead of the full one.

**For the current architecture** where `shared_agent` is created once with the full glossary baked into its instruction (line 1890-1901 in main.py), this means either:

- Option A: Create per-scene agents with filtered glossary (more API overhead from lost caching)
- Option B: Keep shared agent with full glossary, but add filtered glossary to the per-scene **prompt** as a priority override section

**Recommendation: Option B.** The shared agent's instruction contains the full glossary (cached by Gemini's prompt caching). The per-scene prompt prepends a small "Priority terms for this scene:" block with only the 3-5 relevant terms. The model sees both but prioritizes the closer, scene-specific block.

## Disk layout

```
backend/translation_memory/
└── {project_name}/
    ├── translations.lance/   # LanceDB table files
    └── _versions/            # LanceDB internal versioning
```

---

# Plan B: Bazarr Integration

## Purpose

When Bazarr fails to find Greek subtitles, automatically trigger OmbiSub to translate the English subtitle file. The translated `.el.srt` file is placed where Sonarr/Radarr expect it, and Plex/Jellyfin picks it up automatically.

## How Bazarr works

Bazarr has a **post-processing** feature under Settings → Subtitles → Post-Processing. It can run a command after downloading subtitles. However, for the "no subtitle found" case, we need a different approach:

**Option 1: Bazarr Custom Provider (complex)**
Write a custom subtitle provider for Bazarr that calls OmbiSub. This is the most integrated option but requires forking/extending Bazarr.

**Option 2: Webhook/polling approach (recommended)**

- OmbiSub polls Bazarr's API for episodes/movies with missing Greek subtitles
- When found, fetches the English subtitle, translates, and drops the file
- Bazarr re-scans and sees the new subtitle

**Option 3: Direct Sonarr/Radarr webhook**

- Sonarr/Radarr send a webhook on new episode/movie import
- OmbiSub checks if Greek sub exists, if not, translates the English one

**Recommendation: Option 2** — it's the simplest, doesn't require modifying Bazarr, and naturally handles both new and existing content.

## File: `backend/integrations/__init__.py` (CREATE — empty)

## File: `backend/integrations/bazarr.py` (CREATE)

```python
"""
Bazarr Integration — Poll for missing subtitles, auto-translate.

Connects to Bazarr's API to:
1. Find episodes/movies missing Greek (target language) subtitles
2. Check if English subtitles exist for those items
3. Trigger OmbiSub translation for items with English but no Greek
4. Deliver translated .srt files to the media directory
"""

import httpx
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass

@dataclass
class BazarrConfig:
    base_url: str = "http://localhost:6767"
    api_key: str = ""
    target_language_code: str = "el"      # ISO 639-1 for Greek
    source_language_code: str = "en"       # English
    enabled: bool = False
    poll_interval_minutes: int = 30
    auto_translate: bool = True            # Translate automatically or queue for review
    media_type: str = "both"               # "series", "movies", "both"
```

### Required methods

**`async def get_missing_subtitles(config: BazarrConfig) -> List[Dict]`**

- Call Bazarr API: `GET /api/episodes/wanted` (for series) and `GET /api/movies/wanted` (for movies)
- These endpoints return items where Bazarr has been unable to find subtitles in the target language
- Filter for items that have English subs but no Greek subs
- Return list of: `{title, season, episode, sonarr_series_id, english_sub_path, media_path, media_type}`

**Bazarr API endpoints to use:**

```
GET {base_url}/api/series              # List all series (has subtitle info)
GET {base_url}/api/episodes?seriesid=X # Episodes for a series
GET {base_url}/api/movies              # List all movies
GET {base_url}/api/system/languages    # Available languages

# Each episode/movie has a 'subtitles' array with:
# [{path, language, code2, code3, forced, hi}]
```

**`async def fetch_english_subtitle(config: BazarrConfig, sub_path: str) -> str`**

- Read the English .srt file from the filesystem path Bazarr reports
- Since OmbiSub runs on the same machine as the *arr stack, direct file access works
- Return the file contents as a string

**`async def deliver_translated_subtitle(media_path: str, srt_content: str, language_code: str = "el") -> str`**

- Determine output path: same directory as the media file, with language suffix
- Naming convention: `{media_filename}.el.srt` (Plex/Jellyfin auto-detect this)
- Example: `Frieren.S01E01.1080p.mkv` → `Frieren.S01E01.1080p.el.srt`
- Write the SRT content to that path
- Return the output path

**`async def trigger_bazarr_rescan(config: BazarrConfig, sonarr_series_id: int = None, radarr_movie_id: int = None)`**

- After delivering the subtitle, tell Bazarr to re-scan so it picks up the new file
- `POST {base_url}/api/series/action` with action=scan for series
- `POST {base_url}/api/movies/action` with action=scan for movies

## File: `backend/integrations/auto_translator.py` (CREATE)

This is the orchestrator that ties Bazarr → OmbiSub → file delivery together.

```python
"""
Auto-Translator — Autonomous translation pipeline for *arr stack.

Polls Bazarr for missing subtitles, translates via OmbiSub,
delivers to media directory, triggers rescan.
"""

import asyncio
from typing import Dict, List
from datetime import datetime
from .bazarr import BazarrConfig, get_missing_subtitles, fetch_english_subtitle, deliver_translated_subtitle, trigger_bazarr_rescan
from utils import storage
from utils.srt_parser import parse_srt
from utils.translation_memory import TranslationMemory

class AutoTranslator:
    """Background service that auto-translates missing subtitles."""

    def __init__(self, bazarr_config: BazarrConfig):
        self.config = bazarr_config
        self._running = False
        self._task: asyncio.Task = None

    async def start(self):
        """Start the polling loop."""
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self):
        """Stop the polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()

    async def _poll_loop(self):
        """Main polling loop."""
        while self._running:
            try:
                await self._check_and_translate()
            except Exception as e:
                print(f"AutoTranslator error: {e}")
            await asyncio.sleep(self.config.poll_interval_minutes * 60)

    async def _check_and_translate(self):
        """Single poll iteration."""
        missing = await get_missing_subtitles(self.config)
        for item in missing:
            await self._process_item(item)

    async def _process_item(self, item: Dict):
        """Process a single missing-subtitle item."""
        # 1. Determine or create OmbiSub project for this show/movie
        project_name = self._resolve_project(item)

        # 2. Fetch the English subtitle content
        srt_content = await fetch_english_subtitle(self.config, item["english_sub_path"])

        # 3. Parse and upload as episode to OmbiSub
        parsed = parse_srt(srt_content)
        episode_name = self._make_episode_name(item)
        storage.save_episode(project_name, episode_name, parsed)
        storage.save_original_srt(project_name, episode_name, srt_content)

        # 4. Trigger translation (reuse existing batch translate logic)
        # This calls _translate_episode_atomic under the hood
        await self._translate_and_deliver(project_name, episode_name, item)

    def _resolve_project(self, item: Dict) -> str:
        """Find or create the OmbiSub project for this show/movie."""
        # Map show name to project name (sanitize for filesystem)
        show_name = item["title"]
        project_name = show_name.replace(" ", "_").lower()

        existing = storage.list_projects()
        if project_name not in existing:
            storage.create_project(project_name, {
                "show_name": show_name,
                "target_language": "Greek",  # From config
                "type": "show" if item["media_type"] == "series" else "movie",
            })
        return project_name

    def _make_episode_name(self, item: Dict) -> str:
        """Generate episode name from Bazarr metadata."""
        if item["media_type"] == "series":
            return f"S{item['season']:02d}E{item['episode']:02d}"
        else:
            return item["title"].replace(" ", "_")

    async def _translate_and_deliver(self, project_name: str, episode_name: str, item: Dict):
        """Translate episode and deliver the SRT file."""
        # Import here to avoid circular deps
        from main import _translate_episode_atomic, create_job, update_job, translation_rate_limiter

        metadata = storage.load_project_metadata(project_name)
        global_config = storage.load_global_config()
        model = global_config.get("default_translation_model", "gemini-flash-latest")

        # Create the shared agent (same as batch translate)
        from adk_agents.translation_pipeline import create_translation_pipeline
        shared_agent = create_translation_pipeline(
            project_name=project_name,
            target_language=metadata.get("target_language", "Greek"),
            glossary=metadata.get("glossary", {"terms": []}),
            context_guide=metadata.get("context_guide", ""),
            translator_model=model,
            skip_glossary_step=True,
        )

        job_id = create_job("auto_translate")
        semaphore = asyncio.Semaphore(global_config.get("concurrent_scenes", 3))

        success = await _translate_episode_atomic(
            job_id=job_id,
            project_name=project_name,
            episode_name=episode_name,
            target_lang=metadata.get("target_language", "Greek"),
            shared_agent=shared_agent,
            rate_limiter=translation_rate_limiter,
            semaphore=semaphore,
            global_config=global_config,
            total_episodes=1,
            episode_idx=0,
            start_progress=0.0,
        )

        if success:
            # Build translated SRT and deliver to media directory
            from utils.srt_parser import build_srt_from_data
            episode_data = storage.load_episode(project_name, episode_name)
            srt_output = build_srt_from_data(episode_data["data"])
            output_path = await deliver_translated_subtitle(
                item["media_path"], srt_output, self.config.target_language_code
            )
            update_job(job_id, status="completed",
                       log=f"Delivered translated subtitle to {output_path}")

            # Trigger Bazarr rescan
            await trigger_bazarr_rescan(
                self.config,
                sonarr_series_id=item.get("sonarr_series_id"),
                radarr_movie_id=item.get("radarr_movie_id"),
            )
```

### Missing utility: `build_srt_from_data()`

Add to `backend/utils/srt_parser.py`:

```python
def build_srt_from_data(parsed_data: List[Dict]) -> str:
    """Convert parsed subtitle data back to SRT format string."""
    blocks = []
    for entry in parsed_data:
        text = entry.get("translated") or entry.get("original", "")
        blocks.append(f"{entry['id']}\n{entry['timecode']}\n{text}\n")
    return "\n".join(blocks)
```

**Note:** Check if this function already exists elsewhere. The `/download` endpoint (line 1214 in main.py) likely has inline SRT reconstruction logic that should be extracted into this utility.

## Wire into main.py

### Startup/shutdown hooks

```python
from integrations.auto_translator import AutoTranslator
from integrations.bazarr import BazarrConfig

auto_translator: Optional[AutoTranslator] = None

@app.on_event("startup")
async def start_auto_translator():
    global auto_translator
    config = storage.load_global_config()
    bazarr_config = BazarrConfig(
        base_url=config.get("bazarr_url", "http://localhost:6767"),
        api_key=config.get("bazarr_api_key", ""),
        enabled=config.get("bazarr_enabled", False),
        poll_interval_minutes=config.get("bazarr_poll_interval", 30),
    )
    if bazarr_config.enabled and bazarr_config.api_key:
        auto_translator = AutoTranslator(bazarr_config)
        await auto_translator.start()

@app.on_event("shutdown")
async def stop_auto_translator():
    if auto_translator:
        await auto_translator.stop()
```

### Config/control endpoints

```python
@app.get("/integrations/bazarr/status")
async def bazarr_status():
    """Check Bazarr connection and auto-translator status."""
    config = storage.load_global_config()
    return {
        "enabled": config.get("bazarr_enabled", False),
        "connected": auto_translator is not None and auto_translator._running,
        "poll_interval_minutes": config.get("bazarr_poll_interval", 30),
    }

@app.post("/integrations/bazarr/configure")
async def configure_bazarr(
    base_url: str, api_key: str, enabled: bool = True,
    poll_interval_minutes: int = 30
):
    """Configure Bazarr integration."""
    config = storage.load_global_config()
    config["bazarr_url"] = base_url
    config["bazarr_api_key"] = api_key
    config["bazarr_enabled"] = enabled
    config["bazarr_poll_interval"] = poll_interval_minutes
    storage.save_global_config(config)
    # Restart auto-translator with new config
    # ...

@app.post("/integrations/bazarr/scan-now")
async def bazarr_scan_now(background_tasks: BackgroundTasks):
    """Manually trigger a scan for missing subtitles."""
    if auto_translator:
        background_tasks.add_task(auto_translator._check_and_translate)
        return {"status": "scan_triggered"}
    return {"status": "not_configured"}

@app.get("/integrations/bazarr/missing")
async def bazarr_missing():
    """Preview what would be translated without triggering it."""
    if not auto_translator:
        return {"items": [], "error": "Not configured"}
    missing = await get_missing_subtitles(auto_translator.config)
    return {"items": missing, "count": len(missing)}
```

### SettingsRequest update (line 137)

Add Bazarr fields:

```python
class SettingsRequest(BaseModel):
    # ... existing fields ...
    bazarr_url: Optional[str] = None
    bazarr_api_key: Optional[str] = None
    bazarr_enabled: Optional[bool] = None
    bazarr_poll_interval: Optional[int] = None
```

## Headless mode flag

For Bazarr-triggered jobs, the pipeline should never pause for review. Add a `headless` parameter to `_process_batch_translation` (line 1871):

```python
async def _process_batch_translation(
    job_id: str, project_name: str, episode_names: List[str],
    model: str, enhance_glossary_flag: bool,
    is_simple_pipeline: bool = False,
    headless: bool = False,  # NEW — skips all review pauses
):
```

When `headless=True`, skip `_pipeline_pause()` calls and auto-accept generated context/glossary.

## Frontend: Settings page integration panel

Add a "Bazarr Integration" section to `frontend/src/components/SettingsPage.jsx`:

- Bazarr URL input
- API key input
- Enable/disable toggle
- Poll interval slider (5-120 minutes)
- "Test Connection" button (calls `/integrations/bazarr/status`)
- "Scan Now" button (calls `/integrations/bazarr/scan-now`)
- "Preview Missing" list (calls `/integrations/bazarr/missing`)

---

# Plan C: Character Voice Profiles

## Purpose

Track per-character speech patterns, formality level, and grammatical gender. Greek requires gendered adjectives, past tense forms, and articles that differ by speaker gender. Without character profiles, Gemini guesses randomly.

## File: `backend/utils/character_profiles.py` (CREATE)

```python
"""
Character Voice Profiles — Per-character speech pattern tracking.

Stores and retrieves character-specific translation guidance:
- Grammatical gender (masculine/feminine/neuter)
- Formality level (formal εσείς / informal εσύ)
- Speech patterns (verbal tics, catchphrases)
- Established translations for character-specific phrases
"""

from typing import Dict, List, Optional
from pathlib import Path
import json

PROFILES_DIR = Path(__file__).resolve().parent.parent / "projects"

@dataclass
class CharacterProfile:
    name: str
    gender: str = "unknown"           # masculine, feminine, neuter, unknown
    formality: str = "informal"       # formal, informal, mixed
    speech_patterns: str = ""         # Free-text description
    verbal_tics: List[str] = field(default_factory=list)
    established_phrases: Dict[str, str] = field(default_factory=dict)  # EN → EL
    episode_first_seen: str = ""
    notes: str = ""

class CharacterProfileManager:
    """Manage character profiles for a project."""

    def __init__(self, project_name: str):
        self._project = project_name
        self._file = PROFILES_DIR / project_name / "character_profiles.json"

    def load_all(self) -> Dict[str, CharacterProfile]:
        """Load all character profiles."""

    def save_all(self, profiles: Dict[str, CharacterProfile]):
        """Save all character profiles."""

    def get_profile(self, name: str) -> Optional[CharacterProfile]:
        """Get a single character's profile."""

    def update_profile(self, name: str, updates: Dict):
        """Update a character's profile (merge, not replace)."""

    def get_profiles_for_chunk(
        self, source_lines: List[str], glossary: Dict
    ) -> List[CharacterProfile]:
        """Return profiles for characters that appear in these lines.

        Detection strategy:
        1. Check glossary terms of type 'person' against source lines
        2. Return matching profiles
        """

    def build_profile_context(self, profiles: List[CharacterProfile]) -> str:
        """Format relevant profiles as a compact prompt section.

        Output format:
        Characters in this scene:
        - Frieren (f): informal, calm/understated. "Rin" → "Ριν"
        - Fern (f): formal with elders, informal with peers.
        """
```

## Storage: `projects/{project_name}/character_profiles.json`

```json
{
  "Frieren": {
    "gender": "feminine",
    "formality": "informal",
    "speech_patterns": "Calm, understated, occasionally deadpan humor. Uses simple sentence structures.",
    "verbal_tics": [],
    "established_phrases": {
      "That takes me back": "Αυτό μου θυμίζει παλιές εποχές"
    },
    "episode_first_seen": "S01E01",
    "notes": "Elf, over 1000 years old. Despite her age, speaks casually."
  },
  "Fern": {
    "gender": "feminine",
    "formality": "mixed",
    "speech_patterns": "Formal with elders (Frieren), stern but caring. Direct.",
    "verbal_tics": ["Frieren-sama"],
    "established_phrases": {},
    "episode_first_seen": "S01E01",
    "notes": "Adopted daughter figure to Frieren. Serious personality."
  }
}
```

## Auto-generation agent

For new projects, automatically generate initial profiles from glossary + first episode.

### File: `backend/adk_agents/profile_agent.py` (CREATE)

```python
"""Agent that analyzes subtitle text to generate character profiles."""

from google.adk.agents import Agent
from .llm_factory import create_model

def create_profile_agent(model_name: str = "gemini-flash-latest") -> Agent:
    return Agent(
        name="CharacterProfileAgent",
        model=create_model(model_name),
        instruction="""Analyze these subtitles and generate character profiles.
For each character mentioned, determine:
- gender (masculine/feminine/neuter/unknown)
- formality (formal/informal/mixed — how they speak)
- speech_patterns (brief description of their speaking style)
- verbal_tics (catchphrases or repeated expressions)

Output JSON array:
[{"name": "...", "gender": "...", "formality": "...", "speech_patterns": "...", "verbal_tics": [...]}]

Only include characters with enough dialogue to assess. Skip characters mentioned by name only.""",
        tools=[],
    )
```

Add an endpoint:

```python
@app.post("/projects/{project_name}/characters/generate")
async def generate_character_profiles(project_name: str, model: str = "gemini-flash-latest"):
    """Auto-generate character profiles from existing episodes."""
```

And a CRUD endpoint:

```python
@app.get("/projects/{project_name}/characters")
@app.put("/projects/{project_name}/characters/{name}")
```

## Wire into translation

### Modify `_translate_scene_task` in `main.py` (line 1756)

After loading source texts and before building the prompt:

```python
from utils.character_profiles import CharacterProfileManager
profile_mgr = CharacterProfileManager(project_name)
scene_profiles = profile_mgr.get_profiles_for_chunk(source_texts, glossary)
profile_context = profile_mgr.build_profile_context(scene_profiles) if scene_profiles else ""
```

### Modify `build_translation_prompt()` in `translator_agent.py` (line 91)

Add `character_context` parameter:

```python
def build_translation_prompt(
    lines: List[str],
    target_language: str,
    context_line: str = "",
    few_shot_context: str = "",
    character_context: str = "",  # NEW
) -> str:
    numbered = "\n".join(
        f"{i + 1}| {line.replace(chr(10), '<br>')}" for i, line in enumerate(lines)
    )
    ctx = f"[prev] {context_line}\n" if context_line else ""
    fs = f"{few_shot_context}\n" if few_shot_context else ""
    ch = f"{character_context}\n" if character_context else ""
    return f"Translate to {target_language}:\n\n{ch}{fs}{ctx}{numbered}"
```

## Incremental update after translation

After each episode is translated, update character profiles with newly observed patterns. This can be a lightweight post-processing step:

```python
async def _update_profiles_from_episode(project_name: str, episode_name: str, parsed_srt: List[Dict]):
    """Extract new character speech patterns from a freshly translated episode."""
    profile_mgr = CharacterProfileManager(project_name)
    existing = profile_mgr.load_all()

    # For each glossary person term found in dialogue,
    # store any new established_phrases (source → target pairs)
    metadata = storage.load_project_metadata(project_name)
    person_terms = [t for t in metadata.get("glossary", {}).get("terms", []) if t.get("type") == "person"]

    for term in person_terms:
        name = term["term"]
        if name in existing:
            # Find lines where this character likely speaks
            # (line contains their name or follows a name mention)
            # Add observed translations to established_phrases
            pass
```

This is intentionally lightweight — it stores phrases, not reinvents profiles each time.

---

# Plan D: Reviewer Agent

## Purpose

A second LLM pass that scores translation quality and flags problems. Runs selectively on ~20% of lines (those flagged by heuristics), keeps cost low. Lines scoring below threshold get re-translated with critique as guidance.

## Architecture decision: when to review

**Don't review every line.** Use heuristic pre-filters to select the ~20% of lines most likely to have issues:

1. Lines containing proper nouns (from glossary) — highest risk of mistranslation
2. Lines where Greek translation is >40% longer than English (CPS risk)
3. Lines containing dialogue markers, questions, or exclamations (formality/tone risk)
4. Lines where TM found a match at 0.80-0.94 similarity (similar but not identical — risk of wrong reuse)
5. The first and last line of each scene (context boundary errors)

## File: `backend/adk_agents/reviewer_agent.py` (CREATE)

```python
"""
Reviewer Agent — LLM-as-judge for translation quality assessment.

Scores translations on multiple dimensions and flags problems.
Uses a cheaper/smaller model than the translator for cost efficiency.
"""

from google.adk.agents import Agent
from .llm_factory import create_model
from typing import Dict, List, Optional

def create_reviewer_agent(
    model_name: str = "gemini-flash-latest",  # Use Flash, not Pro
    target_language: str = "Greek",
    glossary: Dict = None,
) -> Agent:
    glossary_terms = ""
    if glossary and glossary.get("terms"):
        glossary_terms = "\n".join(
            f"- {t['term']} → {t.get('translation', t['term'])} ({t.get('gender', 'n/a')})"
            for t in glossary["terms"][:30]  # Cap to avoid bloat
        )

    return Agent(
        name="ReviewerAgent",
        model=create_model(model_name, temperature=0.1),  # Low temp for consistent scoring
        instruction=f"""You are a {target_language} subtitle translation reviewer.

Score each translation on these dimensions (0.0 to 1.0):
- glossary: Does it use the correct glossary translations?
- naturalness: Does it read like natural {target_language}, not machine translation?
- gender: Are gendered forms (articles, adjectives, verbs) correct for the speaker?
- length: Is it appropriate length for subtitle display (not too long)?
- tone: Does the formality/register match the source?

Output JSON array — one object per line reviewed:
[
  {{"index": 1, "scores": {{"glossary": 0.9, "naturalness": 0.8, "gender": 1.0, "length": 0.7, "tone": 0.9}}, "issues": "Line is too long for subtitle display", "suggestion": "shorter alternative"}}
]

Only include entries where ANY score is below 0.85. If all scores are >=0.85, output empty array [].

Glossary reference:
{glossary_terms}""",
        tools=[],
    )
```

## File: `backend/utils/review_pipeline.py` (CREATE)

```python
"""
Review Pipeline — Selective quality review for translated episodes.

Heuristic filter → Reviewer Agent → Re-translation of flagged lines.
"""

import re
from typing import List, Dict, Tuple
from dataclasses import dataclass

@dataclass
class ReviewResult:
    line_index: int
    scores: Dict[str, float]
    issues: str
    suggestion: str
    needs_retranslation: bool

def select_lines_for_review(
    parsed_srt: List[Dict],
    glossary: Dict,
    max_review_pct: float = 0.25,
) -> List[int]:
    """Heuristic filter to select lines worth reviewing.

    Returns list of global indices to send to the reviewer.
    """
    candidates = []
    person_terms = [
        t["term"].lower()
        for t in glossary.get("terms", [])
        if t.get("type") == "person"
    ]

    for i, line in enumerate(parsed_srt):
        score = 0
        original = line.get("original", "")
        translated = line.get("translated", "")
        if not original or not translated:
            continue

        # Proper noun present
        if any(term in original.lower() for term in person_terms):
            score += 3

        # Length ratio (Greek is typically 10-20% longer)
        if len(translated) > len(original) * 1.5:
            score += 2

        # Dialogue markers (questions, exclamations)
        if original.rstrip().endswith("?") or original.rstrip().endswith("!"):
            score += 1

        # Contains Latin characters in Greek text (potential untranslated words)
        greek_text = re.sub(r'[A-Za-z]', '', translated)
        latin_ratio = 1 - (len(greek_text) / max(len(translated), 1))
        if latin_ratio > 0.3:  # More than 30% Latin chars
            score += 3

        if score >= 2:
            candidates.append((i, score))

    # Sort by score descending, cap at max_review_pct
    candidates.sort(key=lambda x: -x[1])
    max_count = int(len(parsed_srt) * max_review_pct)
    return [idx for idx, _ in candidates[:max_count]]


async def review_lines(
    parsed_srt: List[Dict],
    line_indices: List[int],
    glossary: Dict,
    target_language: str,
    model_name: str = "gemini-flash-latest",
) -> List[ReviewResult]:
    """Send selected lines to the reviewer agent."""
    from adk_agents.reviewer_agent import create_reviewer_agent
    from adk_agents.operations import _create_session_and_runner, _collect_response_text

    agent = create_reviewer_agent(model_name, target_language, glossary)
    runner, session_id = await _create_session_and_runner(agent, "review")

    # Build review prompt
    review_lines_text = []
    for idx in line_indices:
        line = parsed_srt[idx]
        review_lines_text.append(
            f"{idx + 1}| EN: {line['original']}\n   EL: {line['translated']}"
        )

    prompt = f"Review these {target_language} subtitle translations:\n\n" + "\n".join(review_lines_text)

    response = await _collect_response_text(runner, session_id, prompt)

    # Parse JSON response
    results = _parse_review_response(response, line_indices)
    return results


def _parse_review_response(response: str, line_indices: List[int]) -> List[ReviewResult]:
    """Parse reviewer JSON output into ReviewResult objects."""
    import json
    # Extract JSON from response (may have markdown wrapping)
    json_match = re.search(r'\[.*\]', response, re.DOTALL)
    if not json_match:
        return []

    try:
        items = json.loads(json_match.group())
    except json.JSONDecodeError:
        return []

    results = []
    for item in items:
        avg_score = sum(item.get("scores", {}).values()) / max(len(item.get("scores", {})), 1)
        results.append(ReviewResult(
            line_index=item.get("index", 0) - 1,
            scores=item.get("scores", {}),
            issues=item.get("issues", ""),
            suggestion=item.get("suggestion", ""),
            needs_retranslation=avg_score < 0.70,
        ))
    return results
```

## Wire into `_translate_episode_atomic` in `main.py`

After translation succeeds but **before saving** (between lines 1856 and 1862):

```python
# --- Optional review pass ---
global_config_review = global_config.get("enable_reviewer", False)
if global_config_review:
    from utils.review_pipeline import select_lines_for_review, review_lines

    review_indices = select_lines_for_review(parsed_srt, glossary)
    if review_indices:
        update_job(job_id, log=f"{episode_name}: Reviewing {len(review_indices)} flagged lines...")

        review_results = await review_lines(
            parsed_srt, review_indices, glossary, target_lang,
            model_name=global_config.get("review_model", "gemini-flash-latest")
        )

        # Lines needing retranslation
        retranslate_indices = [r.line_index for r in review_results if r.needs_retranslation]

        if retranslate_indices:
            update_job(job_id, log=f"{episode_name}: Re-translating {len(retranslate_indices)} low-quality lines...")
            # Re-translate individual lines with critique context
            for result in review_results:
                if result.needs_retranslation and result.suggestion:
                    # Use suggestion as the translation (reviewer often provides a corrected version)
                    parsed_srt[result.line_index]["translated"] = result.suggestion
                    parsed_srt[result.line_index]["needs_review"] = True

        # Lines below threshold but above retranslation threshold → flag for user review
        user_review_indices = [
            r.line_index for r in review_results
            if not r.needs_retranslation and min(r.scores.values()) < 0.85
        ]
        for idx in user_review_indices:
            parsed_srt[idx]["needs_review"] = True

        update_job(job_id, log=f"{episode_name}: Review complete. {len(retranslate_indices)} re-translated, {len(user_review_indices)} flagged for user review.")
```

## Config additions

Add to `SettingsRequest` and `config.json`:

```python
enable_reviewer: Optional[bool] = None     # Default False
review_model: Optional[str] = None          # Default "gemini-flash-latest"
review_threshold: Optional[float] = None    # Default 0.70 — below this, retranslate
review_max_pct: Optional[float] = None      # Default 0.25 — max % of lines to review
```

## User review queue endpoint

```python
@app.get("/projects/{project_name}/review-queue")
async def get_review_queue(project_name: str):
    """Get all lines flagged for user review across all episodes."""
    flagged = []
    for ep_name in storage.list_episodes(project_name):
        data = storage.load_episode(project_name, ep_name)
        if not data:
            continue
        for i, line in enumerate(data["data"]):
            if line.get("needs_review"):
                flagged.append({
                    "episode": ep_name,
                    "index": i,
                    "original": line["original"],
                    "translated": line["translated"],
                    "timecode": line["timecode"],
                })
    return {"items": flagged, "count": len(flagged)}
```

---

# Plan E: Rolling Episode Summaries

## Purpose

After each episode is translated, generate a compact summary of events, character developments, and new terminology. Feed the last 2-3 summaries as context for the next episode's translation. This gives the translator cross-episode continuity without bloating the context window.

## File: `backend/utils/episode_summaries.py` (CREATE)

```python
"""
Episode Summary Manager — Rolling context for multi-episode shows.

After translation, generates a compact summary (150-200 tokens).
Before translation, retrieves recent summaries for context injection.
"""

from pathlib import Path
from typing import List, Dict, Optional
import json

SUMMARIES_DIR = Path(__file__).resolve().parent.parent / "projects"

class EpisodeSummaryManager:
    def __init__(self, project_name: str):
        self._project = project_name
        self._file = SUMMARIES_DIR / project_name / "episode_summaries.json"

    def load_all(self) -> Dict[str, str]:
        """Load all summaries: {episode_name: summary_text}"""
        if not self._file.exists():
            return {}
        with open(self._file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def save_summary(self, episode_name: str, summary: str):
        """Save/update a single episode's summary."""
        all_summaries = self.load_all()
        all_summaries[episode_name] = summary
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, 'w', encoding='utf-8') as f:
            json.dump(all_summaries, f, indent=2, ensure_ascii=False)

    def get_recent_summaries(
        self, current_episode: str, window: int = 3
    ) -> str:
        """Get the last N episode summaries before the current one.

        Returns formatted context string for prompt injection.
        Assumes episode names sort chronologically (S01E01, S01E02, ...).
        """
        all_summaries = self.load_all()
        sorted_episodes = sorted(all_summaries.keys())

        # Find position of current episode
        try:
            current_idx = sorted_episodes.index(current_episode)
        except ValueError:
            # Current episode not yet summarized — use last N
            current_idx = len(sorted_episodes)

        start = max(0, current_idx - window)
        recent = sorted_episodes[start:current_idx]

        if not recent:
            return ""

        lines = ["Previously in this show:"]
        for ep in recent:
            lines.append(f"[{ep}] {all_summaries[ep]}")
        return "\n".join(lines)
```

## Summary generation agent

### Add to `backend/adk_agents/operations.py`

```python
async def generate_episode_summary_adk(
    parsed_srt: List[Dict],
    episode_name: str,
    show_name: str,
    model_name: str = "gemini-flash-latest",
) -> str:
    """Generate a compact episode summary for context propagation.

    Target: 150-200 tokens. Focuses on:
    - Key events and plot points
    - Character developments or revelations
    - New terms/concepts introduced
    - Emotional tone shifts
    """
    # Sample ~50 lines spread across the episode for summary
    lines = [entry.get("translated") or entry.get("original", "") for entry in parsed_srt]
    sampled = _stratified_sample(lines, total=60)
    text_sample = "\n".join(sampled)

    prompt = f"""Summarize this episode ({episode_name}) of "{show_name}" in 2-3 sentences (max 50 words).
Focus on: key events, character developments, new terminology.
Do NOT describe dialogue — summarize what HAPPENED.

Subtitle text:
{text_sample}"""

    agent = Agent(
        name="SummaryAgent",
        model=create_model(model_name, temperature=0.2),
        instruction="Write extremely concise episode summaries for subtitle translation context. Output only the summary — no labels, no preamble.",
        tools=[],
    )

    runner, session_id = await _create_session_and_runner(agent, "summary")
    return await _collect_response_text(runner, session_id, prompt)
```

## Wire into translation flow

### After episode translation succeeds in `_translate_episode_atomic` (after line 1868, before return True)

```python
# Generate and store episode summary for future context
try:
    from utils.episode_summaries import EpisodeSummaryManager
    from adk_agents.operations import generate_episode_summary_adk

    metadata_proj = storage.load_project_metadata(project_name)
    summary = await generate_episode_summary_adk(
        parsed_srt, episode_name,
        show_name=metadata_proj.get("show_name", project_name),
        model_name=global_config.get("default_translation_model", "gemini-flash-latest"),
    )
    summary_mgr = EpisodeSummaryManager(project_name)
    summary_mgr.save_summary(episode_name, summary)
    update_job(job_id, log=f"{episode_name}: Summary generated for future context.")
except Exception as e:
    update_job(job_id, log=f"{episode_name}: Summary generation failed (non-critical): {e}")
```

### Before scene translation in `_translate_episode_atomic` (after line 1748, before scene tasks)

```python
# Load recent episode summaries for context
from utils.episode_summaries import EpisodeSummaryManager
summary_mgr = EpisodeSummaryManager(project_name)
episode_context = summary_mgr.get_recent_summaries(episode_name, window=3)
```

Then pass `episode_context` into the prompt. Add it to `build_translation_prompt()`:

```python
def build_translation_prompt(
    lines, target_language, context_line="",
    few_shot_context="", character_context="",
    episode_context="",  # NEW
):
    # ... existing code ...
    ep = f"{episode_context}\n\n" if episode_context else ""
    return f"Translate to {target_language}:\n\n{ep}{ch}{fs}{ctx}{numbered}"
```

**Important prompt ordering** for Gemini caching:

```
[agent instruction + glossary]           ← STABLE (cached across scenes)
[episode context from summaries]         ← SEMI-STABLE (same within episode)
[character profiles for this scene]      ← VARIABLE
[few-shot TM examples]                   ← VARIABLE
[previous scene context line]            ← VARIABLE
[numbered source lines]                  ← VARIABLE
```

Put the most stable content first to maximize prompt cache hits.

## Storage

```
backend/projects/{project_name}/
├── episode_summaries.json    # {"S01E01": "summary...", "S01E02": "summary..."}
└── ...
```

## Endpoint for viewing/editing summaries

```python
@app.get("/projects/{project_name}/summaries")
async def get_episode_summaries(project_name: str):
    mgr = EpisodeSummaryManager(project_name)
    return mgr.load_all()

@app.put("/projects/{project_name}/summaries/{episode_name}")
async def update_episode_summary(project_name: str, episode_name: str, summary: str):
    mgr = EpisodeSummaryManager(project_name)
    mgr.save_summary(episode_name, summary)
    return {"status": "updated"}
```

---
