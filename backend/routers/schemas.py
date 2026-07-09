from pydantic import BaseModel
from typing import List, Dict, Optional, Any

class CreateProjectRequest(BaseModel):
    name: str
    target_language: str = "English"
    parent_project: Optional[str] = None
    type: str = "show"
    ai_provider: Optional[str] = "cloud"


class ImportRequest(BaseModel):
    source_project: str
    import_glossary: bool = True
    import_context: bool = True


class ScanRequest(BaseModel):
    model: Optional[str] = None


class TranslateRequest(BaseModel):
    model: Optional[str] = None
    enhance_glossary: bool = False
    force: bool = False  # If True, clears existing translation before retranslating
    translation_type: Optional[str] = "saved"  # "original" or "saved"


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


class SettingsRequest(BaseModel):
    ai_provider: Optional[str] = "cloud"  # "cloud", "local", "hybrid"
    default_target_language: Optional[str] = "English"
    default_scan_model: Optional[str] = "gemini-flash-lite-latest"
    default_translation_model: Optional[str] = "gemini-flash-lite-latest"
    default_context_model: Optional[str] = "gemini-flash-lite-latest"
    default_glossary_model: Optional[str] = "gemini-flash-lite-latest"
    local_llm_base_url: Optional[str] = "http://localhost:11434"
    local_scan_model: Optional[str] = None
    local_translation_model: Optional[str] = None
    local_context_model: Optional[str] = None
    local_glossary_model: Optional[str] = None
    local_review_model: Optional[str] = None
    local_summary_model: Optional[str] = None
    subtitle_edit_path: Optional[str] = ""
    apply_subtitle_edit_fixes: Optional[bool] = False
    concurrent_scenes: Optional[int] = 3
    max_lines_per_scene: Optional[int] = 200
    temperature: Optional[float] = 0.3
    top_k: Optional[int] = 40
    top_p: Optional[float] = 1.0
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
    arr_sync_interval: Optional[int] = 0  # Minutes. 0 = manual only
    arr_source_language: Optional[str] = "en"
    arr_auto_translate: Optional[bool] = False  # Auto-translate on webhook
    discord_webhook_url: Optional[str] = ""
    # --- Worker / concurrency ---
    concurrent_episodes: Optional[int] = 2
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
    experimental_local_structured_output: Optional[bool] = False
    scene_lookahead_lines: Optional[int] = 2
    # --- Phase 2 Stage 1 (Plans 21 & 23) ---
    tm_exact_reuse_max_words: Optional[int] = 6
    tm_exact_reuse_require_same_speaker: Optional[bool] = False
    morphology_normalization: Optional[bool] = True
    morphology_stemming: Optional[bool] = True
    # --- Intra-episode consistency reconciliation (Plan 20) ---
    intra_episode_reconcile_enabled: Optional[bool] = True
    reconcile_min_freq: Optional[int] = 2
    reconcile_max_lines: Optional[int] = 30
    # Cap on per-line context-aware fallback calls during atomic translation of an episode
    # with partial (sub-20%) API failures; beyond this, remaining gaps are flagged for review.
    missing_fallback_max_lines: Optional[int] = 50
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


class ApiKeyRequest(BaseModel):
    api_key: str


class SimplePipelineRequest(BaseModel):
    model: Optional[str] = None
    context_model: Optional[str] = None
    glossary_model: Optional[str] = None
    translation_model: Optional[str] = None


class ConfirmContextRequest(BaseModel):
    context_guide: str


class ConfirmGlossaryRequest(BaseModel):
    glossary: Dict


class ArrTestRequest(BaseModel):
    url: Optional[str] = None
    api_key: Optional[str] = None


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
    context_model: Optional[str] = None
    glossary_model: Optional[str] = None
    translation_model: Optional[str] = None
    episode_names: Optional[List[str]] = None
    enhance_glossary: bool = False


class SyncImportRequest(BaseModel):
    terms: List[Dict] = []
    characters: List[Dict] = []
