# Plan F: User-Edit Feedback Loop

## Purpose

When a user edits a translation in the UI, store the `(source, model_output, user_edit)` triple. These edits become high-priority few-shot examples in the TM, teaching the model the user's style over time. After 200-300 edits, the model's first-pass output starts matching the user's preferences.

## Implementation

This plan has the fewest new files — it mostly wires existing components together.

### Step 1: Track edits at save time

The existing `/projects/{project_name}/episodes/{episode_name}/save` endpoint (line 581 in main.py) receives the full episode data when the user saves. Currently it just overwrites.

**Modify** to detect which lines changed and store edit records.

In `main.py`, modify the save endpoint (line 581):

```python
@app.post("/projects/{project_name}/episodes/{episode_name}/save")
async def save_episode_data(project_name: str, episode_name: str, request: SaveEpisodeRequest):
    # Load previous data to detect edits
    previous = storage.load_episode(project_name, episode_name)

    # Save new data
    storage.save_episode(project_name, episode_name, request.data)

    # Detect and store edits
    if previous and previous.get("data"):
        await _record_user_edits(project_name, episode_name, previous["data"], request.data)

    return {"status": "saved"}
```

### Step 2: Edit recording function

Add to `main.py` or create `backend/utils/edit_tracker.py`:

```python
async def _record_user_edits(
    project_name: str, episode_name: str,
    old_data: List[Dict], new_data: List[Dict]
):
    """Detect user edits and store them in the Translation Memory as gold examples."""
    from utils.translation_memory import TranslationMemory
    tm = TranslationMemory(project_name)

    edits = []
    for old_line, new_line in zip(old_data, new_data):
        old_trans = old_line.get("translated", "")
        new_trans = new_line.get("translated", "")
        original = new_line.get("original", "")

        # Only record if:
        # 1. Translation actually changed
        # 2. There was a previous translation (not initial fill)
        # 3. The new translation is non-empty
        if old_trans and new_trans and old_trans != new_trans and original:
            edits.append({
                "source": original,
                "old_translation": old_trans,
                "new_translation": new_trans,
            })

    if edits:
        # Store user edits as high-priority TM entries
        source_lines = [e["source"] for e in edits]
        target_lines = [e["new_translation"] for e in edits]
        tm.add_translations(
            source_lines, target_lines, episode_name,
            is_user_edited=True,  # Higher priority in TM search results
        )
        print(f"Recorded {len(edits)} user edits for {project_name}/{episode_name}")
```

### Step 3: TM priority for user edits

In `TranslationMemory.search()` (Plan A), results with `is_user_edited=True` should rank higher:

```python
def search(self, source_lines, top_k=3, similarity_threshold=0.80):
    # ... existing search ...

    # Boost user-edited results: multiply score by 1.1 so they sort higher
    for result in results:
        if result.get("is_user_edited"):
            result["score"] = min(result["score"] * 1.1, 1.0)

    # Re-sort by boosted score
    results.sort(key=lambda r: -r["score"])
```

This means if both a model translation and a user edit match a new source line, the user edit wins.

### Step 4: Edit statistics endpoint

```python
@app.get("/projects/{project_name}/edit-stats")
async def get_edit_stats(project_name: str):
    """Show how many user edits have been recorded and their impact."""
    tm = TranslationMemory(project_name)
    stats = tm.get_stats()
    return {
        "total_records": stats.get("total_records", 0),
        "user_edited_records": stats.get("user_edited_count", 0),
        "edit_ratio": stats.get("user_edited_count", 0) / max(stats.get("total_records", 1), 1),
    }
```

### Step 5: `is_edited` flag on individual lines

The existing data schema already has `"is_edited": bool` per line. Make sure the frontend sets this when the user modifies a translation. Then use it as an additional signal:

- Lines with `is_edited=True` in existing episodes are gold standards
- When loading TM data from past episodes, prioritize these

### Frontend change: batch edit review

In the `EpisodeView.jsx` or `EditorView.jsx`, add a visual indicator showing when a line has TM matches:

- Small icon/badge on lines where the TM provided the translation
- Different color for lines where a user edit from a past episode was reused
- This builds user trust in the system and encourages them to edit when they see issues (feeding the loop)

---

# Cross-cutting concerns

## Prompt structure for maximum cache efficiency

All plans inject context into the translation prompt. Order matters for Gemini's implicit caching. The final prompt structure should be:

```
[AGENT INSTRUCTION — static per project]
  ├── Translation rules
  ├── Full glossary (compact pipe format)
  └── Context guide

[EPISODE-LEVEL CONTEXT — same for all scenes in an episode]
  ├── Rolling episode summaries (Plan E)
  └── Episode-specific notes

[SCENE-LEVEL CONTEXT — varies per scene]
  ├── Character profiles for this scene (Plan C)
  ├── TM few-shot examples (Plan A)
  ├── Previous scene context line
  └── Numbered source lines to translate
```

The agent instruction + glossary is set once when `shared_agent` is created (line 1890 in main.py). Everything from the episode-level down goes into the per-scene prompt via `build_translation_prompt()`.

## Stable prefix: move episode context into agent instruction

For maximum cache hits, consider a two-level approach:

1. Create the shared agent (with glossary + context guide) once per batch — this is the stable prefix
2. Before each episode, update the agent's instruction to include that episode's summaries — semi-stable, changes per episode but stays constant across all scenes in that episode
3. Scene-level variable content in the prompt

Currently the `shared_agent` is created once for the entire batch (line 1890). You'd need to either recreate it per-episode (losing some caching) or find a way to inject episode-level context through the prompt rather than the instruction.

**Recommendation**: keep the agent instruction stable (glossary + context guide only). Put episode summaries at the START of the per-scene prompt. Gemini's caching will still benefit because the prompt prefix (summaries + character profiles) stays constant within an episode.

## Config additions (all plans combined)

Add to `config.json` / `SettingsRequest`:

```json
{
  "tm_enabled": true,
  "tm_similarity_threshold": 0.80,
  "tm_exact_match_threshold": 0.95,
  "bazarr_url": "http://localhost:6767",
  "bazarr_api_key": "",
  "bazarr_enabled": false,
  "bazarr_poll_interval": 30,
  "character_profiles_enabled": true,
  "enable_reviewer": false,
  "review_model": "gemini-flash-latest",
  "review_threshold": 0.70,
  "review_max_pct": 0.25,
  "episode_summaries_enabled": true,
  "summary_window": 2
}
```

## New dependencies (requirements.txt additions)

```
lancedb>=0.4.0
sentence-transformers>=2.2.0
httpx>=0.25.0
```

`httpx` is for async HTTP calls to Bazarr's API. It may already be installed as a transitive dependency.

## File summary

| File | Action | Plan |
|------|--------|------|
| `backend/utils/translation_memory.py` | CREATE | A |
| `backend/utils/character_profiles.py` | CREATE | C |
| `backend/utils/review_pipeline.py` | CREATE | D |
| `backend/utils/episode_summaries.py` | CREATE | E |
| `backend/utils/edit_tracker.py` | CREATE (or inline in main.py) | F |
| `backend/adk_agents/reviewer_agent.py` | CREATE | D |
| `backend/adk_agents/profile_agent.py` | CREATE | C |
| `backend/integrations/__init__.py` | CREATE | B |
| `backend/integrations/bazarr.py` | CREATE | B |
| `backend/integrations/auto_translator.py` | CREATE | B |
| `backend/main.py` | MODIFY — wire all plans into translation flow | ALL |
| `backend/adk_agents/translator_agent.py` | MODIFY — extend `build_translation_prompt()` | A, C, E |
| `backend/adk_agents/operations.py` | MODIFY — add `generate_episode_summary_adk()` | E |
| `backend/utils/srt_parser.py` | MODIFY — add `build_srt_from_data()` | B |
| `backend/utils/storage.py` | No changes needed (metadata is freeform dict) | — |
| `backend/requirements.txt` | MODIFY — add lancedb, sentence-transformers, httpx | A, B |
| Frontend components | MODIFY — Bazarr settings, review queue, TM stats, character editor | ALL |
