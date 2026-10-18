from pydantic import BaseModel
from typing import List, Dict, Optional, Any, Union

class CreateProjectRequest(BaseModel):
    name: str
    target_language: str = "English"
    parent_project: Optional[str] = None
    type: str = "show"


class ImportRequest(BaseModel):
    source_project: str
    import_glossary: bool = True
    import_context: bool = True


class PromoteTermRequest(BaseModel):
    term: str
    translation: str
    type: Optional[str] = "other"
    gender: Optional[str] = "unknown"
    case_sensitive: Optional[bool] = True
    keep_original: Optional[bool] = False
    description: Optional[str] = ""


class ScanRequest(BaseModel):
    model: Optional[str] = None


class TranslateRequest(BaseModel):
    model: Optional[str] = None
    enhance_glossary: bool = False
    force: bool = False  # If True, clears existing translation before retranslating
    translation_type: Optional[str] = "saved"  # "original" or "saved"
    lang: Optional[str] = None
    target_language_name: Optional[str] = None


class BatchTranslateRequest(BaseModel):
    episode_names: List[str]
    model: Optional[str] = None
    enhance_glossary: bool = False
    force: bool = False  # If True, clears existing translation before retranslating
    translation_type: Optional[str] = "saved"  # "original" or "saved"


class EnhanceGlossaryRequest(BaseModel):
    episode_names: Optional[List[str]] = None


class SaveEpisodeRequest(BaseModel):
    data: List[Dict]
    lang: Optional[str] = None


class DeleteLinesRequest(BaseModel):
    indexes: List[int]  # zero-based indexes into the episode's cleaned data list


class ApplyFixesRequest(BaseModel):
    track: str = "source"          # "source" (original) or "target" (translation)
    lang: Optional[str] = None     # target language name/code; defaults to primary


class BatchApplyFixesRequest(BaseModel):
    episode_names: List[str]
    track: str = "source"          # "source", "target", or "both"
    lang: Optional[str] = None     # target language name/code; defaults to primary


class BatchDownloadRequest(BaseModel):
    episodes: Optional[List[str]] = None


class PipelineRequest(BaseModel):
    mode: str = "auto"  # "auto" or "step"
    skip_context: bool = False
    skip_glossary: bool = False
    episode_names: Optional[List[str]] = None
    model: Optional[str] = None
    context_model: Optional[str] = None
    glossary_model: Optional[str] = None
    translation_model: Optional[str] = None
    enhance_glossary: bool = True


class SettingsRequest(BaseModel):
    ai_provider: Optional[str] = "cloud"
    daily_limit_policy: Optional[str] = "auto_fallback"
    default_local_model: Optional[str] = "local"
    default_target_language: Optional[str] = "Greek"
    default_scan_model: Optional[str] = "gemini-flash-lite-latest"
    default_translation_model: Optional[str] = "gemini-flash-lite-latest"
    fallback_translation_model: Optional[str] = "gemini-3.1-flash-lite"
    default_context_model: Optional[str] = "gemini-flash-lite-latest"
    default_glossary_model: Optional[str] = "gemini-flash-lite-latest"
    subtitle_edit_path: Optional[str] = ""
    apply_subtitle_edit_fixes: Optional[bool] = False
    concurrent_scenes: Optional[int] = 3
    max_lines_per_scene: Optional[int] = 200
    temperature: Optional[float] = 0.3
    top_k: Optional[int] = 64
    top_p: Optional[float] = 0.95
    tm_enabled: Optional[bool] = True
    tm_similarity_threshold: Optional[float] = 0.80
    tm_exact_match_threshold: Optional[float] = 0.95
    character_profiles_enabled: Optional[bool] = True
    enable_reviewer: Optional[bool] = False
    review_model: Optional[str] = "gemini-flash-lite-latest"
    review_threshold: Optional[float] = 0.7
    review_max_pct: Optional[float] = 0.25
    episode_summaries_enabled: Optional[bool] = True
    episode_summary_window: Optional[int] = 3
    sonarr_url: Optional[str] = "http://localhost:8989"
    sonarr_api_key: Optional[str] = ""
    sonarr_enabled: Optional[bool] = False
    radarr_url: Optional[str] = "http://localhost:7878"
    radarr_api_key: Optional[str] = ""
    radarr_enabled: Optional[bool] = False
    arr_path_mappings: Optional[List[Dict[str, str]]] = []
    path_mappings: Optional[List[Dict[str, str]]] = None
    arr_sync_interval: Optional[int] = 0  # Minutes. 0 = manual only
    arr_source_language: Optional[str] = "en"
    arr_auto_translate: Optional[Union[bool, str]] = "smart"  # Auto-translate on webhook (False, True, "smart")
    arr_non_anime_delay_minutes: Optional[int] = 30  # Delay in minutes before auto-translating non-anime
    # --- Embedded ASS extraction (docs/PLAN_embedded_ass_extraction.md) ---
    embedded_extraction_enabled: Optional[bool] = True
    # Substrings that mark a track as signs/songs/partial. These DEPRIORITIZE a track,
    # they never exclude it — some releases ship one track carrying signs, songs and
    # dialogue together, and dropping it would make the episode silently unavailable.
    embedded_deprioritize_keywords: Optional[str] = (
        "signs, songs, s&s, signs & songs, forced, commentary, karaoke"
    )
    # Extraction streams the whole container, so parallel jobs over one share just
    # slow each other down. Kept separate from concurrent_episodes on purpose.
    embedded_extraction_concurrency: Optional[int] = 1
    # Most episodes one on-demand request (MorpiceHub) may cover. Each is a full
    # LLM translation, so this bounds what a single viewer click can spend.
    on_demand_max_episodes: Optional[int] = 30
    ffmpeg_path: Optional[str] = ""
    discord_webhook_url: Optional[str] = ""
    api_key: Optional[str] = None
    # --- Worker / concurrency ---
    concurrent_episodes: Optional[int] = 2
    export_retry_concurrency: Optional[int] = 2
    adaptive_concurrency_enabled: Optional[bool] = False   # Plan 10
    concurrency_max: Optional[int] = 6                      # Plan 10
    # --- Batch API (Plan 01) ---
    batch_api_enabled: Optional[bool] = False
    batch_window_size: Optional[int] = 200
    batch_poll_seconds: Optional[int] = 120
    # --- Context caching (Plan 02) ---
    context_cache_enabled: Optional[bool] = False
    context_cache_ttl_seconds: Optional[int] = 3600
    # --- Job registry eviction (Plan 04) ---
    job_retention_seconds: Optional[int] = 1800
    job_registry_max: Optional[int] = 500
    # --- Webhook auth (Plan 05) ---
    webhook_secret: Optional[str] = ""
    # --- CORS (Plan 06) ---
    cors_allow_origins: Optional[List[str]] = None
    # --- Translation engine: "agent" (default) = the agent runner translates through the agent
    # CLI (Antigravity first; the Gemini API only for lines its safety filter refuses);
    # "model" = the background worker translates with the configured Gemini/local model.
    # Extraction and export lanes run either way. ---
    translation_engine: Optional[str] = "agent"
    # An agent claim with no workspace activity for this long goes back to the queue.
    agent_claim_ttl_hours: Optional[float] = 12
    # Gemini API content filter for every Gemini request (utils/gemini_safety.py):
    # "off" (subtitles of films and series: violence, insults, crude language are dialogue),
    # "only_high" or "default" (Google's own thresholds).
    gemini_safety_filter: Optional[str] = "off"
    # --- Off-peak scheduling (Plan 09) ---
    worker_schedule_enabled: Optional[bool] = False
    worker_window_start: Optional[str] = "01:00"
    worker_window_end: Optional[str] = "08:00"
    worker_schedule_priority_cutoff: Optional[int] = 1     # PRIORITY_MANUAL
    # --- Daily-limit recovery: how often to auto-probe an exhausted model's quota ---
    daily_limit_recheck_minutes: Optional[int] = 30
    # --- Subtitle conformance (Plan 13) ---
    conformance_enabled: Optional[bool] = False
    max_cps: Optional[float] = 17.0
    max_chars_per_line: Optional[int] = 42
    max_lines: Optional[int] = 2
    # --- Semantic condensation for over-fast cues (Plan 24) ---
    condense_enabled: Optional[bool] = False
    # --- Phase 2: Plan 18 & 19 ---
    structured_output_enabled: Optional[bool] = True
    scene_lookahead_lines: Optional[int] = 2
    # --- Phase 2 Stage 1 (Plans 21 & 23) ---
    tm_exact_reuse_max_words: Optional[int] = 6
    tm_exact_reuse_require_same_speaker: Optional[bool] = False
    morphology_normalization: Optional[bool] = True
    # --- Auto-Updates (GitHub Sync) ---
    auto_update_enabled: Optional[bool] = False
    auto_update_interval_hours: Optional[int] = 6
    update_channel: Optional[str] = "releases"
    github_webhook_enabled: Optional[bool] = True
    github_webhook_secret: Optional[str] = ""
    # --- Agent Runner Rescue & Boot ---
    agent_runner_autostart: Optional[bool] = True
    api_rescue: Optional[bool] = True
    rescue_model: Optional[str] = None
    gemini_api_key: Optional[str] = None
    morphology_stemming: Optional[bool] = True
    # --- Intra-episode consistency reconciliation (Plan 20) ---
    intra_episode_reconcile_enabled: Optional[bool] = True
    reconcile_min_freq: Optional[int] = 2
    reconcile_max_lines: Optional[int] = 30
    # Cap on per-line context-aware fallback calls during atomic translation of an episode
    # with partial (sub-20%) API failures; beyond this, remaining gaps are flagged for review.
    missing_fallback_max_lines: Optional[int] = 50
    atomic_failure_threshold: Optional[float] = 0.20
    # --- Source subtitle cleaning (Plan 25) ---
    source_clean_enabled: Optional[bool] = True
    strip_sdh: Optional[bool] = True
    preserve_italics: Optional[bool] = True
    merge_split_cues: Optional[bool] = True
    # --- Line-level incremental re-translation (Plan 26) ---
    incremental_retranslate_enabled: Optional[bool] = True
    incremental_min_unchanged_ratio: Optional[float] = 0.5
    # --- Write throttling during translation ---
    incremental_save_throttle_seconds: Optional[float] = 8.0
    # --- Manual source-subtitle search (Plan 17) ---
    opensubtitles_api_key: Optional[str] = ""
    # ====================== v2 plan (PLAN_v2_gemini_first) ======================
    # D2 — per-role thinking budgets (0 = off; -1/absent = model default).
    thinking_budgets: Optional[Dict[str, int]] = None
    # D1 — unit of work: auto = whole-episode for Gemini under the cue cap, scenes otherwise.
    episode_request_mode: Optional[str] = "auto"   # "auto" | "whole" | "scenes"
    whole_episode_max_cues: Optional[int] = 1200
    # D6 — QC funnel.
    glossary_enforce_enabled: Optional[bool] = True
    repair_pass_enabled: Optional[bool] = True
    # Wrong-alphabet characters: fix the unambiguous visual twins for free, ticket
    # the rest for the repair pass (utils/script_guard.py).
    script_guard_enabled: Optional[bool] = True
    review_episode_sample_pct: Optional[float] = 1.0  # fraction of episodes the reviewer runs on
    # Source-echo alignment guard: whole-episode requests ask the model to echo the
    # first words of each source line; a mismatch drops the line to a safe gap.
    source_echo_enabled: Optional[bool] = True
    # Save every whole-episode model response to the episode's debug/ dir (not just
    # anomalous ones, which are always saved) — for diagnosing alignment issues.
    debug_save_llm_responses: Optional[bool] = False
    # D7 — glossary growth piggybacked on translation responses.
    new_terms_suggest_enabled: Optional[bool] = True
    # D8 — cost controls.
    daily_budget_usd: Optional[float] = 0.0  # 0 = no cap
    # D12 — local lane: sequential scene order (quality over latency).
    sequential_scenes: Optional[bool] = False
    # First-run setup wizard (docs/PLAN_onboarding_wizard.md) — not a secret,
    # safe to expose through the generic settings endpoint.
    setup_completed: Optional[bool] = False
    # --- Autopilot (docs/PLAN_autopilot.md): unattended Greek subtitles for new arr imports ---
    autopilot_enabled: Optional[bool] = False
    autopilot_grace_minutes: Optional[float] = 5          # Bazarr's window before OmbiSub acts
    autopilot_poll_seconds: Optional[int] = 60            # Sonarr/Radarr history polling
    autopilot_reconcile_hours: Optional[float] = 6        # library reconcile interval
    autopilot_backlog_mode: Optional[str] = "off"         # "off" | "newest" | "oldest"
    autopilot_backlog_daily_limit: Optional[int] = 20
    autopilot_count_embedded_greek: Optional[bool] = True
    autopilot_skip_original_greek: Optional[bool] = True
    autopilot_skip_tags: Optional[str] = "ombisub-skip,no-greek"
    autopilot_monitored_only: Optional[bool] = True
    autopilot_protect_foreign_subs: Optional[bool] = True
    autopilot_repair_foreign_encoding: Optional[bool] = True
    autopilot_notify_success: Optional[bool] = False
    autopilot_digest_hour: Optional[int] = 9              # local hour of the daily digest, -1 = off
    autopilot_enabled_at: Optional[str] = None            # set when switched on; reconcile cut-off
    plex_refresh_enabled: Optional[bool] = False
    plex_url: Optional[str] = "http://localhost:32400"
    plex_token: Optional[str] = ""
    translate_lyrics: Optional[bool] = True


class ProjectSettings(BaseModel):
    translate_lyrics: bool = True
    translation_model: Optional[str] = None
    context_model: Optional[str] = None
    glossary_model: Optional[str] = None
    fallback_translation_model: Optional[str] = None
    auto_export_enabled: Optional[bool] = False
    auto_export_dir: Optional[str] = ""
    apply_subtitle_edit_fixes: Optional[bool] = True
    translate_all_source_formats: Optional[bool] = False
    prefer_ass_format: Optional[str] = "auto"
    embedded_deprioritize_keywords: Optional[str] = ""
    temperature: Optional[float] = 0.3
    top_k: Optional[int] = 64
    top_p: Optional[float] = 0.95


class ProjectMetadata(BaseModel):
    show_name: Optional[str] = None
    target_language: str = "Greek"
    target_languages: Optional[List[str]] = []
    translate_lyrics: bool = True
    settings: Optional[ProjectSettings] = None


class ApiKeyRequest(BaseModel):
    api_key: Optional[str] = None


class SimplePipelineRequest(BaseModel):
    model: Optional[str] = None
    context_model: Optional[str] = None
    glossary_model: Optional[str] = None
    translation_model: Optional[str] = None
    enhance_glossary: bool = True


class ConfirmContextRequest(BaseModel):
    context_guide: str


class ConfirmGlossaryRequest(BaseModel):
    glossary: Dict


class ArrTestRequest(BaseModel):
    url: Optional[str] = None
    api_key: Optional[str] = None
    sonarr_url: Optional[str] = None
    sonarr_api_key: Optional[str] = None
    radarr_url: Optional[str] = None
    radarr_api_key: Optional[str] = None


class PathTestRequest(BaseModel):
    remote_path: str
    path_mappings: List[Dict[str, str]]


class MergeTranslationRequest(BaseModel):
    selected_lines: Dict[str, str]  # global_index -> new translation text


class TestTranslationRequest(BaseModel):
    lines: List[str]
    temperature: Optional[float] = None
    top_k: Optional[int] = None
    top_p: Optional[float] = None
    model_name: Optional[str] = None


class AutoPipelineRequest(BaseModel):
    skip_context: bool = False
    skip_glossary: bool = False
    model: Optional[str] = None
    model_name: Optional[str] = None
    context_model: Optional[str] = None
    glossary_model: Optional[str] = None
    translation_model: Optional[str] = None
    episode_names: Optional[List[str]] = None
    episodes: Optional[List[str]] = None
    enhance_glossary: bool = True
    force: bool = False
    translation_type: Optional[str] = "saved"



class SyncImportRequest(BaseModel):
    terms: List[Dict] = []
    characters: List[Dict] = []


class ExtractEmbeddedRequest(BaseModel):
    stream_index: Optional[int] = None
    force: bool = False
    migrate_srt: bool = True
    auto_translate: bool = False


class BatchExtractEmbeddedRequest(BaseModel):
    episode_names: Optional[List[str]] = None
    force: bool = False
    migrate_srt: bool = True


class ProbeMediaTestRequest(BaseModel):
    media_path: str


class ArrSyncRequest(BaseModel):
    scan_ass: bool = True
    extract_embedded_ass: Optional[bool] = None
    source: Optional[str] = "both"  # "both", "sonarr", "radarr"


class BatchPromoteTermsRequest(BaseModel):
    terms: List[Dict]


class BatchSuppressTermsRequest(BaseModel):
    terms: List[str]  # list of term names to suppress


class BatchRevertTermsRequest(BaseModel):
    terms: List[str]  # list of term names to revert (remove override and un-suppress)


class HarvestTermsRequest(BaseModel):
    episode_names: Optional[List[str]] = None
    dialogue_text: Optional[str] = None
    model: Optional[str] = None



class OnDemandSubtitleRequest(BaseModel):
    """An on-demand subtitle request/lookup from an external front-end (MorpiceHub).

    Identify the title by external id where possible (tmdb for movies, tvdb for
    series); *arr ids and title/year are fallbacks.
    """
    media_type: str  # "movie" | "series" (also accepts "show"/"tv")
    tmdb_id: Optional[int] = None
    tvdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    radarr_id: Optional[int] = None
    sonarr_id: Optional[int] = None
    title: Optional[str] = None
    year: Optional[int] = None
    season: Optional[int] = None
    episodes: Optional[List[int]] = None
    language: Optional[str] = None  # ISO code or name; defaults to the global target language
    requested_by: Optional[str] = None  # display name, for logs and the request record only
    client: Optional[str] = None
    client_ref: Optional[str] = None
