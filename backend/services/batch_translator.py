"""
Batch API backlog drainer (v2 plan, D3) — opt-in, guarded, quality-parity.

When ``batch_api_enabled``, periodically claims a window of BACKLOG queue items,
plans each episode through the same ``PromptAssembler`` the live lane uses (full
system instruction: glossary, context guide, formatting rules, structured schema),
chunks per the model-aware scene budget, and submits all scene requests as one
Gemini batch job (50% price, separate quotas). On completion the scenes are
reassembled, gap lines are flagged ``needs_review`` (never silently exported as
source language), and the target .srt is written.

Correlation: the Gemini inline batch API returns responses **index-aligned** to the
submitted requests (``InlinedResponse`` carries no echo key), so responses map back
by order; a count mismatch re-pends everything rather than risking misattribution.

The atomic claim means the normal worker never double-processes; on any failure the
items are re-pended so the normal worker still drains them — enabling batch can never
strand the backlog.
"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from utils.translation_queue import TranslationQueue
from utils import storage
from utils.language_codes import to_code
from utils.srt_parser import reconstruct_srt, build_scene_ast
from utils.structured_output import parse_structured, parse_new_terms
from utils.model_resolver import resolve_model
from adk_agents.llm_factory import get_recommended_chunk_size, resolve_thinking_budget
from services.prompt_assembler import compile_bible, assemble_scene_request, TranslationRequest

logger = logging.getLogger(__name__)


class _ItemPlan:
    """Per-episode reassembly state."""
    __slots__ = ("item", "rows", "target_code", "is_primary", "n_scenes",
                 "scenes_applied", "bible_fp", "model")

    def __init__(self, item, rows, target_code, is_primary, n_scenes, bible_fp="", model=""):
        self.item = item
        self.rows = rows
        self.target_code = target_code
        self.is_primary = is_primary
        self.n_scenes = n_scenes
        self.scenes_applied = 0
        self.bible_fp = bible_fp
        self.model = model


class BatchTranslator:
    def __init__(self):
        self.queue = TranslationQueue()

    # -- planning -----------------------------------------------------------

    def _plan_item(self, item: Dict, config: Dict):
        """Build the per-scene requests + reassembly plan for one queue item.

        Returns ``(plan, [TranslationRequest])`` or ``(None, [])`` if the episode
        can't be loaded (caller re-pends).
        """
        project, episode = item["project_name"], item["episode_name"]
        data = storage.load_episode(project, episode)
        if not data or not data.get("data"):
            return None, []
        rows = data["data"]

        proj_meta = storage.load_project_metadata(project) or {}

        import json
        options = {}
        if item.get("options"):
            try:
                options = json.loads(item["options"])
            except Exception:
                options = {}

        # Target language (honor the item's lang for multi-language projects).
        req_lang = item.get("lang") or ""
        primary_code = to_code(proj_meta.get("target_language", "Greek"))
        is_primary = (req_lang == "" or req_lang == primary_code)
        target_code = primary_code if is_primary else req_lang
        target_name = options.get("target_language_name") or proj_meta.get("target_language", "Greek")

        model = options.get("model") or resolve_model("translation", proj_meta) \
            or config.get("default_translation_model", "gemini-flash-lite-latest")

        bible = compile_bible(project, proj_meta, model, target_language=target_name)

        # ASS/SSA passthrough events (karaoke timing, vector drawings) are never
        # translated. Seed their raw text as the "translation" — same as the live worker
        # (translation_service pre-fills translated_map for these) — so gap detection
        # doesn't flag them and the editor/export stay consistent. _apply_scene skips
        # overwriting them, and reconstruct_ass emits their original markup regardless.
        for r in rows:
            if r.get("_ass", {}).get("translatable") is False:
                r["translated"] = r.get("original", "")
                r.setdefault("translations", {})[target_code] = r.get("original", "")

        # Scene chunking with the model-aware budget (same as the live worker).
        configured_max = config.get("max_lines_per_scene", 200)
        max_lines = max(10, min(configured_max, get_recommended_chunk_size(model)))
        scenes = build_scene_ast(rows, gap_threshold_ms=3000, max_lines_per_scene=max_lines)

        plan = _ItemPlan(item, rows, target_code, is_primary, len(scenes), bible.fingerprint, model=model)
        reqs: List[TranslationRequest] = []
        for scene in scenes:
            lines = scene.get("lines", [])
            indices = list(range(scene["start_index"], scene["start_index"] + len(lines)))
            source_texts = [l.get("original", "") for l in lines]
            from utils.translation_memory import filter_glossary_for_chunk, build_priority_glossary_block
            priority_block = ""
            filtered = filter_glossary_for_chunk(bible.glossary, source_texts)
            if filtered.get("terms"):
                priority_block = build_priority_glossary_block(filtered)
            reqs.append(assemble_scene_request(
                bible, source_texts, indices, priority_block=priority_block,
            ))
        return plan, reqs

    @staticmethod
    def _to_inline_request(req: "TranslationRequest", model: str):
        """Render one scene request into a typed InlinedRequest (per-request config
        carries the system instruction, schema, and thinking budget)."""
        from google.genai import types
        cfg_kwargs = {
            "system_instruction": req.system_instruction,
            "temperature": 0.3,
        }
        if req.structured and req.response_schema is not None:
            cfg_kwargs["response_mime_type"] = "application/json"
            cfg_kwargs["response_schema"] = req.response_schema
        budget = resolve_thinking_budget("translation")
        is_thinking_model = "2.5" in model or "3." in model or "thinking" in model.lower()
        if is_thinking_model and budget is not None and (budget > 0 or "pro" not in model.lower()):
            try:
                cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)
            except Exception:
                pass
        return types.InlinedRequest(
            model=model,
            contents=[{"role": "user", "parts": [{"text": req.prompt}]}],
            config=types.GenerateContentConfig(**cfg_kwargs),
        )

    # -- reassembly ---------------------------------------------------------

    @staticmethod
    def _apply_scene(plan: "_ItemPlan", req: "TranslationRequest", text: str,
                     project: Optional[str] = None) -> None:
        """Parse one scene response and write its lines into the episode rows."""
        parsed = parse_structured(text or "", expected_count=req.expected_count)
        for p in parsed:
            local_idx = p["index"] - 1
            if 0 <= local_idx < len(req.index_map):
                gi = req.index_map[local_idx]
                if 0 <= gi < len(plan.rows):
                    # Never overwrite ASS passthrough (karaoke/drawing) rows — they were
                    # pre-seeded with their raw text and must not take a model translation.
                    if plan.rows[gi].get("_ass", {}).get("translatable") is False:
                        continue
                    plan.rows[gi]["translated"] = p["text"]
                    tr = plan.rows[gi].setdefault("translations", {})
                    tr[plan.target_code] = p["text"]
        # D7: harvest new-term suggestions if the model emitted any.
        if project:
            try:
                terms = parse_new_terms(text)
                if terms:
                    from utils.consistency import save_suggestions
                    save_suggestions(project, terms)
            except Exception:
                pass
        plan.scenes_applied += 1

    @staticmethod
    def _finalize_item(plan: "_ItemPlan") -> Dict:
        """Flag gaps and compute completion stats for a fully-applied item.

        Returns ``{"ok": bool, "review_count": int, "gap_count": int}``. ``ok`` is
        False when too much is missing (>20%) — caller re-pends for the live worker.
        """
        rows = plan.rows
        total = len(rows)
        gap = 0
        for r in rows:
            if not (r.get("original") or "").strip():
                continue
            tgt = r.get("translations", {}).get(plan.target_code) or r.get("translated") or ""
            if not str(tgt).strip():
                gap += 1
                r["needs_review"] = True
                r["review_issues"] = "Translation missing — batch API gap"

        # Alignment drift (zero-token): the batch lane has no repair pass, so at
        # least surface lines whose translation belongs to a neighbouring source.
        try:
            from utils.alignment_audit import flag_drift
            flag_drift(rows, plan.target_code)
        except Exception:
            pass

        # Script guard (zero-token): fix the unambiguous wrong-alphabet characters
        # outright, then flag whatever is left — with no repair pass here, the
        # review queue is the only place the rest can surface.
        try:
            from utils.script_guard import scrub_rows, flag_rows
            scrub_rows(rows, plan.target_code, plan.target_code if plan.is_primary else None)
            flag_rows(rows, plan.target_code)
        except Exception:
            pass
        gap_pct = (gap / total) if total else 1.0
        ok = gap_pct <= 0.2
        review_count = sum(1 for r in rows if r.get("needs_review"))
        return {"ok": ok, "review_count": review_count, "gap_count": gap}

    def _write_target(self, plan: "_ItemPlan", stats: Optional[Dict] = None) -> int:
        """Persist the reassembled episode + write the .srt next to media.

        Returns the review_count for the queue item. ``stats`` may be a precomputed
        ``_finalize_item`` result so the caller doesn't pay for (and double-apply) the
        non-idempotent gap/drift flagging twice.
        """
        if stats is None:
            stats = self._finalize_item(plan)
        item = plan.item
        project, episode = item["project_name"], item["episode_name"]

        meta = storage.load_episode_metadata(project, episode) or {}
        if plan.is_primary:
            meta["translated"] = True
            meta["translation_status"] = "completed"
        meta["bible_fp"] = plan.bible_fp  # D4: which bible this run used
        storage.save_episode(project, episode, plan.rows, meta)

        media_path = meta.get("arr_media_path")
        if media_path:
            mf = Path(media_path)
            # Format-aware delivery: preserve the source subtitle's extension and route
            # through reconstruct_cleaned_srt so ASS keeps its styles (SRT path unchanged).
            from utils.source_clean import reconstruct_cleaned_srt
            from utils.language_codes import output_filename_from_original
            orig_filename = meta.get("original_filename") or ""
            ext = Path(orig_filename).suffix
            if not ext:
                _, ext_sub = storage.load_original_subtitle(project, episode)
                ext = f".{ext_sub}" if ext_sub else ".srt"
            out = mf.parent / output_filename_from_original(f"{mf.stem}{ext}", plan.target_code)
            try:
                # reconstruct prefers 'translated'; for non-primary langs make sure
                # 'translated' holds this language's text just for the write.
                if not plan.is_primary:
                    for r in plan.rows:
                        r["translated"] = r.get("translations", {}).get(plan.target_code, "") or r.get("original", "")
                out.write_text(reconstruct_cleaned_srt(project, episode, plan.rows, plan.target_code), encoding="utf-8-sig")
                if plan.is_primary:
                    meta["arr_has_target"] = True
                    meta["arr_target_path"] = str(out)
                    meta["arr_translated_from_fingerprint"] = meta.get("arr_sub_fingerprint")
                else:
                    targets = meta.setdefault("arr_targets", {})
                    targets[plan.target_code] = {
                        "has_target": True, "target_path": str(out),
                        "translated_from_fingerprint": meta.get("arr_sub_fingerprint"),
                    }
                storage.save_episode_metadata(project, episode, meta)
            except Exception as e:
                logger.warning(f"Batch: failed writing {out}: {e}")
        return stats["review_count"]

    # -- budget gate ---------------------------------------------------------

    @staticmethod
    def _over_budget(config: Dict) -> bool:
        """Daily budget cap (v2 D8): pause the batch lane once today's recorded
        spend crosses ``daily_budget_usd`` (0 = no cap)."""
        cap = float(config.get("daily_budget_usd", 0.0) or 0.0)
        if cap <= 0:
            return False
        try:
            from utils.telemetry import cost_today
            return cost_today() >= cap
        except Exception:
            return False

    # -- driver -------------------------------------------------------------

    async def run_once(self, config: Dict) -> int:
        if not config.get("batch_api_enabled", False):
            return 0
        if self._over_budget(config):
            logger.info("Batch: daily budget cap reached; deferring backlog drain.")
            return 0
        try:
            from google import genai
            client = genai.Client()
        except Exception as e:
            logger.warning(f"Batch API client unavailable ({e}); skipping batch run.")
            return 0

        items = self.queue.claim_backlog_window(config.get("batch_window_size", 200))
        if not items:
            return 0
        logger.info(f"Batch: claimed {len(items)} backlog item(s) for batch submission.")

        default_model = config.get("default_translation_model", "gemini-flash-lite-latest")

        plans: Dict[str, _ItemPlan] = {}
        ordered: List = []  # (item_id, TranslationRequest)
        src = []
        for it in items:
            try:
                plan, reqs = self._plan_item(it, config)
            except Exception as e:
                logger.warning(f"Batch: planning failed for {it['id']}: {e}")
                plan, reqs = None, []
            if not plan or not reqs:
                self.queue.repend(it["id"])
                continue
            plans[it["id"]] = plan
            # Each request rides on its own project-resolved model (honoring per-project
            # overrides), not the global default. The per-request InlinedRequest model
            # is authoritative; the top-level batch model is just a default.
            for req in reqs:
                ordered.append((it["id"], req))
                src.append(self._to_inline_request(req, plan.model or default_model))

        if not src:
            return 0

        # Top-level batch default; each InlinedRequest carries its own model anyway.
        submit_model = next((p.model for p in plans.values() if p.model), default_model)

        try:
            job = await asyncio.to_thread(client.batches.create, model=submit_model, src=src)
            name = getattr(job, "name", None)
        except Exception as e:
            err = str(e)
            if "FAILED_PRECONDITION" in err or "Precondition check failed" in err:
                logger.error(
                    "Batch API unavailable: FAILED_PRECONDITION — the Gemini Batch API "
                    "requires a Google Cloud project with billing enabled (not a simple API key). "
                    "Disable batch_api_enabled in Settings or upgrade to a Cloud project key. "
                    f"Re-pending {len(plans)} item(s) for the live worker."
                )
            else:
                logger.error(f"Batch submit failed ({e}); re-pending {len(plans)} item(s).")
            for k in plans:
                self.queue.repend(k)
            return 0

        # Poll (bounded). On terminal failure, re-pend everything.
        poll = max(30, config.get("batch_poll_seconds", 120))
        for _ in range(240):  # safety cap
            await asyncio.sleep(poll)
            try:
                job = await asyncio.to_thread(client.batches.get, name=name)
            except Exception as e:
                logger.warning(f"Batch poll error ({e}); will retry.")
                continue
            state = str(getattr(job, "state", "")).upper()
            if any(s in state for s in ("SUCCEEDED", "COMPLETED", "DONE")):
                break
            if any(s in state for s in ("FAILED", "CANCELLED", "EXPIRED", "ERROR")):
                logger.error(f"Batch {name} state {state}; re-pending.")
                for k in plans:
                    self.queue.repend(k)
                return 0
        else:
            for k in plans:
                self.queue.repend(k)
            return 0

        # Extract inline responses (ordered, index-aligned to `ordered`).
        dest = getattr(job, "dest", None)
        inlined = getattr(dest, "inlined_responses", None) if dest else None
        if inlined is None:
            inlined = getattr(job, "inlined_responses", None) or []
        inlined = list(inlined)

        if len(inlined) != len(ordered):
            logger.warning(
                f"Batch: response count {len(inlined)} != request count {len(ordered)}; "
                "re-pending all (cannot safely correlate by order)."
            )
            for k in plans:
                self.queue.repend(k)
            return 0

        for (item_id, req), r in zip(ordered, inlined):
            resp = getattr(r, "response", None) or (r.get("response") if isinstance(r, dict) else None)
            text = getattr(resp, "text", None) if resp is not None else None
            plan = plans.get(item_id)
            if plan is None:
                continue
            # Telemetry (D8) — batch lane usage rides on the response objects too.
            try:
                from utils.telemetry import extract_usage, record_usage, usage_ctx
                usage_ctx.set({"project": plan.item["project_name"],
                               "episode": plan.item["episode_name"], "lane": "batch"})
                if resp is not None:
                    record_usage(plan.model or submit_model, "translation", extract_usage(resp), lane="batch")
            except Exception:
                pass
            try:
                self._apply_scene(plan, req, text or "", project=plan.item["project_name"])
            except Exception as e:
                logger.warning(f"Batch apply failed for {item_id}: {e}")
                plan.scenes_applied += 1  # count it so the item can finalize/repend

        # Finalize each item: write if all scenes applied and gaps acceptable, else re-pend.
        applied = 0
        for item_id, plan in plans.items():
            if plan.scenes_applied < plan.n_scenes:
                self.queue.repend(item_id)
                continue
            stats = self._finalize_item(plan)
            if not stats["ok"]:
                logger.info(f"Batch: item {item_id} had {stats['gap_count']} gap line(s) (>20%); re-pending.")
                self.queue.repend(item_id)
                continue
            # Reuse the stats we just computed — avoids a second (non-idempotent)
            # finalize pass that would double-append drift notes to review_issues.
            review_count = self._write_target(plan, stats)
            self.queue.update_status(
                item_id, status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                review_count=review_count,
            )
            applied += 1

        logger.info(f"Batch: applied {applied}/{len(plans)} item(s).")
        return applied


batch_translator = BatchTranslator()
