"""What a submitted episode adds to the show: new glossary terms (merged with judgment) and the context guide.

Part of the agent task service (services/agent_task_service.py): ``AgentTaskService`` inherits this mixin.
Moved here unchanged from that module; ``self`` is the service.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import storage
from utils.text_normalize import sanitize_translation_text, normalize_japanese_term
from services import agent_workspace as aw
from services.agent_tasks.common import logger, GUIDE_MAX_CHARS, GUIDE_ADDITIONS_HEADING


class KnowledgeMixin:
    @staticmethod
    def _vocab(project_name: str):
        """Everyday English words of the show (glossary terms that are also everyday words are judged by context)."""
        try:
            from utils.glossary_judgment import project_vocabulary
            return project_vocabulary(project_name)
        except Exception:
            return None

    @staticmethod
    def _terms_file_count(folder: Path) -> int:
        try:
            data = json.loads((folder / aw.TERMS_FILE).read_text(encoding="utf-8") or "[]")
            return len(data) if isinstance(data, (list, dict)) else 0
        except Exception:
            return -1

    def _extract_context_guide(self, raw_text: str, project_name: str) -> Optional[str]:
        """Substantive context-guide text, without the template header/comments/placeholders."""
        if not raw_text or not raw_text.strip():
            return None
        cleaned = re.sub(r'<!--.*?-->', '', raw_text, flags=re.DOTALL).strip()
        cleaned = re.sub(rf"^#\s*Context\s+Guide:\s*{re.escape(project_name)}\s*", '', cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r'\*\*Show\*\*:[^\n]*\n?', '', cleaned)
        cleaned = re.sub(r'\*\*Target Language\*\*:[^\n]*\n?', '', cleaned)
        cleaned = re.sub(r'\*\*Series Type\*\*:[^\n]*\n?', '', cleaned)
        cleaned = cleaned.strip()
        return cleaned if len(cleaned) > 15 else None

    def _normalize_harvested_terms(self, terms_raw: Any, target_code: str = "el",
                                   japanese_work: bool = False) -> List[Dict[str, Any]]:
        """Normalize harvested terms from lists, dicts or wrappers; Greek tonos fixes for Japanese names
        (known ones always; others only in a Japanese work with a romaji source term)."""
        if isinstance(terms_raw, str):
            try:
                terms_raw = json.loads(terms_raw)
            except Exception:
                return []
        if isinstance(terms_raw, dict):
            if isinstance(terms_raw.get("discovered_terms"), list):
                terms_list = terms_raw["discovered_terms"]
            elif isinstance(terms_raw.get("terms"), list):
                terms_list = terms_raw["terms"]
            else:
                terms_list = [{"term": k, "translation": v} for k, v in terms_raw.items() if isinstance(v, str)]
        elif isinstance(terms_raw, list):
            terms_list = terms_raw
        else:
            return []

        normalized: List[Dict[str, Any]] = []
        for item in terms_list:
            if not isinstance(item, dict):
                continue
            term = (item.get("term") or item.get("source") or item.get("en") or "").strip()
            translation = (item.get("translation") or item.get("target") or item.get("el") or "").strip()
            if not term or not translation:
                continue
            term_type = item.get("type", "general")
            if target_code == "el":
                translation = sanitize_translation_text(translation, source_text=term, target_lang_code="el")
                if term_type in ("character", "person", "name") or len(term.split()) <= 2:
                    translation = normalize_japanese_term(term, translation, japanese_work)
            entry: Dict[str, Any] = {"term": term, "translation": translation, "type": term_type}
            for key in ("gender", "notes", "sense"):
                if item.get(key):
                    entry[key] = item[key]
            if not entry.get("sense") and item.get("used_as"):
                entry["sense"] = item["used_as"]
            if item.get("update") or item.get("replace"):
                entry["_update"] = True
            normalized.append(entry)
        return normalized

    def _merge_terms(self, project_name: str, terms_raw: Any, target_code: str) -> Dict[str, Any]:
        """Add new glossary terms; existing terms change only when flagged ``update``."""
        from utils.work_origin import is_japanese_work
        proj_meta = storage.load_project_metadata(project_name) or {}
        normalized = self._normalize_harvested_terms(terms_raw, target_code=target_code,
                                                     japanese_work=is_japanese_work(proj_meta))
        if not normalized:
            return {"added": 0, "updated": 0, "conflicts": [], "skipped": 0}
        from utils.glossary_judgment import accept_new_term
        vocab = self._vocab(project_name)
        proj_meta = storage.load_project_metadata(project_name) or {}
        terms = proj_meta.setdefault("glossary", {}).setdefault("terms", [])
        by_key = {(t.get("term") or t.get("source") or "").strip().lower(): t for t in terms if isinstance(t, dict)}
        added, updated, conflicts, skipped = 0, 0, [], 0
        fresh: List[Dict[str, Any]] = []
        for t in normalized:
            key = t["term"].lower()
            wants_update = t.pop("_update", False)
            existing = by_key.get(key)
            if existing is None and not accept_new_term(t, vocab):
                skipped += 1  # an everyday word with a one-scene rendering ("petal", "Hold") is not a term
                continue
            if existing is None:
                terms.append(t)
                by_key[key] = t
                fresh.append(t)
                added += 1
            elif (existing.get("translation") or "").strip() != t["translation"]:
                if wants_update:
                    existing.update(t)
                    updated += 1
                else:
                    conflicts.append({"term": t["term"], "kept": existing.get("translation"), "proposed": t["translation"]})
        if added or updated:
            try:
                from utils.cache_manager import invalidate_cache
                invalidate_cache(proj_meta)
            except Exception:
                pass
            storage.save_project_metadata(project_name, proj_meta)
        if fresh:
            try:  # a character's gender also goes where the prompts read speakers from
                from utils.character_profiles import CharacterProfileManager
                CharacterProfileManager(project_name).seed_from_glossary(fresh)
            except Exception as e:
                logger.warning(f"Could not seed character profiles for {project_name}: {e}")
        return {"added": added, "updated": updated, "conflicts": conflicts, "skipped": skipped}

    @staticmethod
    def save_context_guide(project_name: str, guide: str) -> bool:
        """Store ``guide`` as the project's context guide; False when it is the guide already there."""
        guide = (guide or "").strip()
        proj_meta = storage.load_project_metadata(project_name) or {}
        if not guide or guide == (proj_meta.get("context_guide") or "").strip():
            return False
        proj_meta["context_guide"] = guide
        try:
            from utils.cache_manager import invalidate_cache
            invalidate_cache(proj_meta)
        except Exception:
            pass
        storage.save_project_metadata(project_name, proj_meta)
        return True

    def append_guide_additions(self, project_name: str, additions: List[str]) -> int:
        """Add short facts to the end of the context guide, under a "Story so far" heading. A fact the
        guide already has is skipped, and nothing is added once the guide is ``GUIDE_MAX_CHARS`` long.
        Returns how many were added."""
        guide = ((storage.load_project_metadata(project_name) or {}).get("context_guide") or "").strip()
        if not guide:
            return 0

        def key(text: str) -> str:
            return re.sub(r"[\W_]+", " ", text.casefold()).strip()

        known = {key(line) for line in guide.splitlines()}
        entries: List[str] = []
        used = len(guide) + (0 if GUIDE_ADDITIONS_HEADING in guide else len(GUIDE_ADDITIONS_HEADING) + 2)
        for raw in additions:
            fact = re.sub(r"^[-*•\s]+", "", (raw or "").strip())[:300].strip()
            if len(fact) < 8 or key(fact) in known:
                continue
            entry = f"- {fact}"
            if used + len(entry) + 1 > GUIDE_MAX_CHARS:
                break
            known.add(key(fact))
            entries.append(entry)
            used += len(entry) + 1
        if not entries:
            return 0
        if GUIDE_ADDITIONS_HEADING in guide:
            grown = guide + "\n" + "\n".join(entries)
        else:
            grown = f"{guide}\n\n{GUIDE_ADDITIONS_HEADING}\n" + "\n".join(entries)
        self.save_context_guide(project_name, grown)
        return len(entries)
