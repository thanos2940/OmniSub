"""
Translation Memory — Vector-backed subtitle translation cache.

Stores source->target pairs with embeddings for semantic retrieval.
High-similarity matches bypass the LLM entirely, saving API calls.
Lower-similarity matches serve as few-shot examples in the prompt.

Uses LanceDB (file-based, no server) for vector storage and
sentence-transformers for embedding generation.
"""

import hashlib
import time
import logging
import os
import warnings
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime

import numpy as np

logger = logging.getLogger(__name__)

TM_DIR = Path(__file__).resolve().parent.parent / "translation_memory"

# ---------------------------------------------------------------------------
# Singleton embedding model cache
# ---------------------------------------------------------------------------

_EMBEDDER_CACHE: Dict[str, object] = {}


def get_embedder(model_name: str = "all-MiniLM-L6-v2"):
    """Load or retrieve a cached SentenceTransformer model.

    The model is ~80 MB and loads once per process. All TM instances share it.
    """
    if model_name not in _EMBEDDER_CACHE:
        try:
            # Suppress HF Hub warnings and telemetry for a cleaner console experience
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
            
            # Specifically filter the 'unauthenticated requests' warning
            warnings.filterwarnings("ignore", message=".*unauthenticated requests to the HF Hub.*")
            
            from sentence_transformers import SentenceTransformer
            _EMBEDDER_CACHE[model_name] = SentenceTransformer(model_name)
            logger.info(f"Loaded embedding model: {model_name}")
        except ImportError:
            logger.warning(
                "sentence-transformers not installed. "
                "TM semantic search will be unavailable. "
                "Install with: pip install sentence-transformers"
            )
            return None
    return _EMBEDDER_CACHE[model_name]


def _text_hash(text: str) -> str:
    """Stable content hash for deduplication."""
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# TranslationMemory
# ---------------------------------------------------------------------------

class TranslationMemory:
    """Per-project translation memory backed by LanceDB.

    Each project gets its own LanceDB directory under ``translation_memory/``.
    Records contain the source text, target text, an embedding vector,
    and metadata (episode, character, whether it was user-edited, etc.).
    """

    TABLE_NAME = "translations"
    EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 output dimension

    def __init__(
        self,
        project_name: str,
        embedder_model: str = "all-MiniLM-L6-v2",
        lang_code: Optional[str] = None,
    ):
        self._project = project_name
        self._lang_code = lang_code.lower() if lang_code else None
        
        if self._lang_code:
            self._db_path = TM_DIR / f"{project_name}_{self._lang_code}"
        else:
            self._db_path = TM_DIR / project_name
            
        self._db_path.mkdir(parents=True, exist_ok=True)
        self._embedder_model = embedder_model
        self._db = None
        self._table = None

    # -- lazy init ----------------------------------------------------------

    def _get_db(self):
        if self._db is None:
            import lancedb
            self._db = lancedb.connect(str(self._db_path))
        return self._db

    def _get_table(self):
        """Return the translations table, or None if it doesn't exist yet."""
        if self._table is None:
            db = self._get_db()
            if self.TABLE_NAME in db.list_tables():
                self._table = db.open_table(self.TABLE_NAME)
        return self._table

    def _reopen_table(self):
        """Force a fresh connection + direct table open.

        Defeats a stale cached table listing: when a *concurrent* writer creates the
        table after this instance connected, our connection's ``list_tables()`` view
        can be stale, so ``_get_table()`` returns None. Reconnecting and opening the
        table directly reads current on-disk state. Returns the table, or None if it
        genuinely does not exist.
        """
        self._db = None
        self._table = None
        db = self._get_db()
        try:
            self._table = db.open_table(self.TABLE_NAME)
        except Exception:
            self._table = None
        return self._table

    def _get_embedder(self):
        return get_embedder(self._embedder_model)

    # -- write --------------------------------------------------------------

    def add_translations(
        self,
        source_lines: List[str],
        target_lines: List[str],
        episode_name: str,
        character: str = "",
        is_user_edited: bool = False,
    ) -> int:
        """Store translated pairs with embeddings.

        Deduplicates by source text hash. If a record already exists for the
        same source text, it is updated (user edits always overwrite).

        Returns the number of records added/updated.
        """
        embedder = self._get_embedder()
        if embedder is None:
            return 0

        # Filter out empty pairs
        pairs = [
            (src.strip(), tgt.strip())
            for src, tgt in zip(source_lines, target_lines)
            if src.strip() and tgt.strip()
        ]
        if not pairs:
            return 0

        sources = [p[0] for p in pairs]
        targets = [p[1] for p in pairs]

        # Batch embed all source lines
        embeddings = embedder.encode(sources, show_progress_bar=False, normalize_embeddings=True)

        now = datetime.now().isoformat()
        records = []
        for src, tgt, emb in zip(sources, targets, embeddings):
            records.append({
                "source_hash": _text_hash(src),
                "source": src,
                "target": tgt,
                "vector": emb.tolist(),
                "episode": episode_name,
                "character": character,
                "is_user_edited": is_user_edited,
                "timestamp": now,
            })

        db = self._get_db()
        created = False
        table = None

        if self.TABLE_NAME not in db.list_tables():
            try:
                # create_table writes `records` as the initial data.
                table = db.create_table(self.TABLE_NAME, data=records)
                created = True
            except Exception as e:
                if "already exists" not in str(e).lower():
                    raise
                # Lost a concurrent create race (another episode created the table
                # after we connected). Our cached table listing is stale — reopen on
                # a fresh connection and append below.
                table = self._reopen_table()
        else:
            table = self._get_table()

        if table is None:
            # Defensive: a stale table listing on this connection. Force a fresh
            # reopen rather than calling .add on None.
            table = self._reopen_table()
        if table is None:
            raise RuntimeError(
                f"TM [{self._project}]: table '{self.TABLE_NAME}' could not be opened "
                "for write (concurrent-create race did not resolve)."
            )
        self._table = table

        if not created:
            # For user edits, overwrite existing entries (LanceDB has no native
            # upsert), so delete matching hashes first. Skipped when we just created
            # the table — there is nothing pre-existing to replace.
            if is_user_edited:
                hashes = [r["source_hash"] for r in records]
                try:
                    hash_list = ", ".join(f"'{h}'" for h in hashes)
                    table.delete(f"source_hash IN ({hash_list})")
                except Exception:
                    pass  # Table may be empty or filter syntax may vary
            table.add(records)

        logger.info(
            f"TM [{self._project}]: added {len(records)} records "
            f"(episode={episode_name}, user_edited={is_user_edited})"
        )

        # Propagate user edits to global shared TM
        if self._project != "global_shared" and is_user_edited:
            try:
                global_tm = TranslationMemory("global_shared", self._embedder_model, lang_code=self._lang_code)
                global_tm.add_translations(
                    source_lines,
                    target_lines,
                    episode_name=f"{self._project}:{episode_name}",
                    character=character,
                    is_user_edited=True
                )
            except Exception as e:
                logger.error(f"Failed to add translations to global shared TM: {e}")

        return len(records)

    # -- read ---------------------------------------------------------------

    def search(
        self,
        source_lines: List[str],
        top_k: int = 3,
        similarity_threshold: float = 0.80,
    ) -> Dict[int, List[Dict]]:
        """For each source line, find similar past translations.

        Args:
            source_lines: Lines to search for.
            top_k: Max results per line.
            similarity_threshold: Minimum cosine similarity (0-1).

        Returns:
            ``{line_index: [{source, target, score, episode, is_user_edited}]}``
            Only includes lines with at least one match above threshold.
        """
        table = self._get_table()
        embedder = self._get_embedder()
        if embedder is None:
            return {}

        # Filter empties
        indexed_lines = [
            (i, line.strip())
            for i, line in enumerate(source_lines)
            if line.strip()
        ]
        if not indexed_lines:
            return {}

        indices = [il[0] for il in indexed_lines]
        texts = [il[1] for il in indexed_lines]

        # Batch embed
        embeddings = embedder.encode(texts, show_progress_bar=False, normalize_embeddings=True)

        results: Dict[int, List[Dict]] = {}

        # 1. Search local project table if it exists
        if table is not None:
            for idx, emb in zip(indices, embeddings):
                try:
                    hits = (
                        table.search(emb.tolist())
                        .limit(top_k)
                        .to_list()
                    )
                    matches = []
                    for hit in hits:
                        l2_dist = hit.get("_distance", 2.0)
                        score = 1.0 - (l2_dist ** 2 / 2.0)
                        score = max(0.0, min(1.0, score))  # clamp to [0, 1]

                        if score >= similarity_threshold:
                            matches.append({
                                "source": hit.get("source", ""),
                                "target": hit.get("target", ""),
                                "score": round(score, 4),
                                "episode": hit.get("episode", ""),
                                "is_user_edited": hit.get("is_user_edited", False),
                                "character": hit.get("character", ""),
                                "is_global": False,
                            })
                    if matches:
                        results[idx] = matches
                except Exception as e:
                    logger.debug(f"Local TM search error for line {idx}: {e}")

        # 2. Search global shared TM if not self
        if self._project != "global_shared":
            try:
                global_tm = TranslationMemory("global_shared", self._embedder_model, lang_code=self._lang_code)
                global_table = global_tm._get_table()
                if global_table is not None:
                    for idx, emb in zip(indices, embeddings):
                        try:
                            hits = (
                                global_table.search(emb.tolist())
                                .limit(top_k)
                                .to_list()
                            )
                            global_matches = []
                            for hit in hits:
                                l2_dist = hit.get("_distance", 2.0)
                                score = 1.0 - (l2_dist ** 2 / 2.0)
                                score = max(0.0, min(1.0, score))

                                if score >= similarity_threshold:
                                    global_matches.append({
                                        "source": hit.get("source", ""),
                                        "target": hit.get("target", ""),
                                        "score": round(score, 4),
                                        "episode": hit.get("episode", ""),
                                        "is_user_edited": hit.get("is_user_edited", False),
                                        "character": hit.get("character", ""),
                                        "is_global": True,
                                    })
                            if global_matches:
                                # Merge local and global
                                local_matches = results.get(idx, [])
                                merged = {m["source"]: m for m in local_matches}
                                for gm in global_matches:
                                    src = gm["source"]
                                    if src not in merged or gm["score"] > merged[src]["score"]:
                                        merged[src] = gm
                                results[idx] = list(merged.values())
                        except Exception as e:
                            logger.debug(f"Global TM search error for line {idx}: {e}")
            except Exception as e:
                logger.debug(f"Global TM database load error: {e}")

        # Post-process: score boosting for user-edited, sort and cap
        for idx in list(results.keys()):
            matches = results[idx]
            for m in matches:
                if m["is_user_edited"]:
                    m["score"] = min(m["score"] * 1.1, 1.0)
            
            # Re-sort by boosted score descending
            matches.sort(key=lambda m: -m["score"])
            results[idx] = matches[:top_k]

        return results

    def get_exact_matches(
        self,
        source_lines: List[str],
        threshold: float = 0.95,
        max_words: Optional[int] = None,
        require_same_speaker: bool = False,
        speakers: Optional[List[str]] = None,
    ) -> Dict[int, Dict]:
        """Return near-exact matches that can skip the LLM.

        Args:
            source_lines: Lines to check.
            threshold: Minimum similarity for a match to be considered "exact".
            max_words: Max words allowed for exact match reuse.
            require_same_speaker: If True, exact match must have the same speaker.
            speakers: Speaker names corresponding to source_lines.

        Returns:
            ``{line_index: {"target": target_text, "is_user_edited": bool, "episode": str}}``
        """
        results = self.search(source_lines, top_k=1, similarity_threshold=threshold)
        exact = {}
        for idx, matches in results.items():
            if matches:
                best = matches[0]
                src_line = source_lines[idx]
                
                # Check word count gate
                if max_words is not None:
                    word_count = len(src_line.split())
                    if word_count > max_words:
                        continue
                        
                # Check speaker gate
                if require_same_speaker:
                    current_speaker = speakers[idx] if (speakers and idx < len(speakers)) else ""
                    stored_speaker = best.get("character", "")
                    if current_speaker.strip().lower() != stored_speaker.strip().lower():
                        continue
                
                exact[idx] = {
                    "target": best["target"],
                    "is_user_edited": best["is_user_edited"],
                    "episode": best.get("episode", "")
                }
        return exact

    def build_few_shot_block(
        self,
        source_lines: List[str],
        target_language: str = "Greek",
        max_examples: int = 5,
        similarity_threshold: float = 0.80,
        exact_threshold: float = 0.95,
        reused_indices: Optional[set] = None,
    ) -> str:
        """Build a few-shot examples block for prompt injection.

        Searches TM for similar past translations (excluding exact matches
        which are handled separately) and formats them as reference examples.

        Returns an empty string if no suitable examples are found.
        """
        results = self.search(
            source_lines, top_k=2, similarity_threshold=similarity_threshold
        )

        seen: set = set()
        examples: List[str] = []

        for idx, matches in results.items():
            # If this line was actually reused, we skip its matches in few-shot block
            if reused_indices and idx in reused_indices:
                continue
            for match in matches:
                # Skip near-exact matches if they were reused
                if match["score"] >= exact_threshold and (reused_indices is None or idx in reused_indices):
                    continue
                key = match["source"]
                if key not in seen and len(examples) < max_examples:
                    seen.add(key)
                    edited_tag = " [verified]" if match["is_user_edited"] else ""
                    examples.append(
                        f"  EN: {match['source']}\n"
                        f"  {target_language[:2].upper()}: {match['target']}{edited_tag}"
                    )

        if not examples:
            return ""

        return "Previous translations for reference:\n" + "\n".join(examples)

    # -- stats --------------------------------------------------------------

    def get_stats(self) -> Dict:
        """Return record count, unique episodes, user edit count."""
        table = self._get_table()
        if table is None:
            return {
                "total_records": 0,
                "unique_episodes": 0,
                "user_edited_count": 0,
                "project": self._project,
            }

        try:
            arrow_table = table.to_arrow()
            episodes = arrow_table["episode"].to_pylist()
            is_user_edited = arrow_table["is_user_edited"].to_pylist()
            return {
                "total_records": len(arrow_table),
                "unique_episodes": len(set(episodes)),
                "user_edited_count": sum(1 for x in is_user_edited if x),
                "project": self._project,
            }
        except Exception as e:
            logger.debug(f"TM stats error: {e}")
            return {
                "total_records": 0,
                "unique_episodes": 0,
                "user_edited_count": 0,
                "project": self._project,
                "error": str(e),
            }

    def clear(self):
        """Delete all records (useful for testing or reset)."""
        db = self._get_db()
        if self.TABLE_NAME in db.list_tables():
            db.drop_table(self.TABLE_NAME)
            self._table = None
            logger.info(f"TM [{self._project}]: cleared all records")


# ---------------------------------------------------------------------------
# Glossary-as-RAG: filter glossary by chunk relevance
# ---------------------------------------------------------------------------

def filter_glossary_for_chunk(
    glossary: Dict,
    source_lines: List[str],
) -> Dict:
    """Return only glossary terms that appear in the given source lines.

    This avoids injecting the entire glossary into every prompt when only
    a handful of terms are relevant. The full glossary stays in the agent
    instruction (cached by Gemini); this filtered subset goes into the
    per-scene prompt as a "priority terms" block for emphasis.

    Args:
        glossary: Full project glossary ``{"terms": [...]}``.
        source_lines: The source text lines for the current chunk/scene.

    Returns:
        Filtered glossary dict with the same structure, containing only
        terms whose source text appears in at least one source line.
    """
    if not glossary or not glossary.get("terms"):
        return {"terms": []}

    source_text = " ".join(source_lines).lower()
    relevant = []

    for term in glossary["terms"]:
        term_text = term.get("term", "").lower()
        if term_text and term_text in source_text:
            relevant.append(term)

    return {"terms": relevant}


def build_priority_glossary_block(
    filtered_glossary: Dict,
) -> str:
    """Format filtered glossary terms as a compact priority block for the prompt.

    Only emits a block if there are relevant terms. Designed to sit near the
    source text in the prompt so the model pays extra attention to these terms.

    Returns empty string if no terms are relevant.
    """
    terms = filtered_glossary.get("terms", [])
    if not terms:
        return ""

    lines = ["Priority glossary for this scene:"]
    for t in terms:
        src = t.get("term", "")
        tgt = t.get("translation", src) if not t.get("keep_original", False) else src
        gender = t.get("gender", "")
        gender_tag = f" ({gender[0]})" if gender and gender != "n/a" else ""
        lines.append(f"  {src} -> {tgt}{gender_tag}")

    return "\n".join(lines)


def build_priority_glossary_reminder_block(
    filtered_glossary: Dict,
) -> str:
    """Format filtered glossary terms as a compact list of names only,
    without translations or details. Used when context caching is enabled.
    """
    terms = filtered_glossary.get("terms", [])
    if not terms:
        return ""
    names = [t.get("term", "") for t in terms if t.get("term")]
    if not names:
        return ""
    return f"Glossary terms in this chunk: {', '.join(names)} — use their canonical targets."
