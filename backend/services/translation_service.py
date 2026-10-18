"""
Translation Service (Modular Facade).

Provides high-level translation orchestration, batch translation, scene translation,
and subtitle export formatting. Decomposed into single-responsibility submodules under
``services.translation``.
"""

import logging

# 1. Output Conformance & Export Formatting
from services.translation.export_formatter import (
    _apply_output_conformance,
    auto_export_translated_subtitle,
    _run_se_cli,
    SubtitleEditUnavailable,
)

# 2. Helpers, Parsing, and Pipeline Synchronization
from services.translation.helpers import (
    _parse_json_response,
    _record_user_edits,
    _select_golden_ratio_files,
    _merge_glossaries,
    _pipeline_pause,
    _resume_pipeline,
    _gather_project_text,
    _pipeline_resume_events,
)

# 3. Line Recovery, Minority Reconciliation, and Missing Lines Repair
from services.translation.line_recovery import (
    _retranslate_minority_lines,
    _translate_missing_line_with_context,
    translate_project_missing_lines,
)

# 4. Atomic Episode Translation Engine
from services.translation.atomic_runner import (
    infer_speaker,
    _translate_episode_atomic,
)

# 5. Batch Translation, Targeted Retranslation, and Comparison
from services.translation.batch_runner import (
    _process_batch_translation,
    _process_targeted_retranslation,
    _process_retranslation_for_comparison,
)

# Re-export ADK pipeline creators and job manager helpers for caller compatibility
from adk_agents import (
    create_translation_pipeline,
    generate_glossary_adk,
    research_project_adk,
    enhance_context_guide_adk,
)
from utils.jobs_manager import create_job, update_job, jobs

logger = logging.getLogger(__name__)

__all__ = [
    "_apply_output_conformance",
    "auto_export_translated_subtitle",
    "_run_se_cli",
    "SubtitleEditUnavailable",
    "_parse_json_response",
    "_record_user_edits",
    "_select_golden_ratio_files",
    "_merge_glossaries",
    "_pipeline_pause",
    "_resume_pipeline",
    "_gather_project_text",
    "_pipeline_resume_events",
    "_retranslate_minority_lines",
    "_translate_missing_line_with_context",
    "translate_project_missing_lines",
    "infer_speaker",
    "_translate_episode_atomic",
    "_process_batch_translation",
    "_process_targeted_retranslation",
    "_process_retranslation_for_comparison",
    "create_translation_pipeline",
    "generate_glossary_adk",
    "research_project_adk",
    "enhance_context_guide_adk",
    "create_job",
    "update_job",
    "jobs",
]
