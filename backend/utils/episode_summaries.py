"""
Episode Summary Manager — Rolling context for multi-episode shows.

After each episode is translated, a compact summary (~50 words) is generated
and stored. Before translating the next episode, the last N summaries are
injected into every scene prompt as "Previously in this show:" context.

This gives the translator cross-episode continuity without bloating the
context window — critical for long-running anime and serialised shows where
character relationships, arc terminology, and plot state evolve over time.

Storage:
    projects/{project_name}/episode_summaries.json
    Format: {"S01E01 - Title": "summary text...", ...}
"""

import json
import logging
from typing import Dict, List, Optional, Any

from utils import storage

logger = logging.getLogger(__name__)


class EpisodeSummaryManager:
    """Manage per-episode summaries for a project.

    Summaries are stored in a single JSON file, keyed by episode name.
    Episode names are assumed to sort chronologically (S01E01, S01E02, ...),
    which matches the naming convention used throughout Omnisub.
    """

    def __init__(self, project_name: str):
        self._project = project_name
        self._file = storage.project_dir(project_name) / "episode_summaries.json"

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def load_all(self) -> Dict[str, str]:
        """Load all summaries. Returns {episode_name: summary_text}."""
        if not self._file.exists():
            return {}
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {k: v for k, v in data.items() if isinstance(v, str) and v.strip()}
        except Exception as e:
            logger.warning(f"Failed to load episode summaries for {self._project}: {e}")
            return {}

    def save_summary(self, episode_name: str, summary: str) -> None:
        """Persist a single episode's summary (upsert)."""
        all_summaries = self.load_all()
        all_summaries[episode_name] = summary.strip()
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(all_summaries, f, indent=2, ensure_ascii=False)

    def delete_summary(self, episode_name: str) -> bool:
        """Delete a single episode's summary. Returns True if it existed."""
        all_summaries = self.load_all()
        if episode_name not in all_summaries:
            return False
        del all_summaries[episode_name]
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(all_summaries, f, indent=2, ensure_ascii=False)
        return True

    def get_summary(self, episode_name: str) -> Optional[str]:
        """Get a single episode's summary, or None if not found."""
        return self.load_all().get(episode_name)

    # ------------------------------------------------------------------
    # Context injection
    # ------------------------------------------------------------------

    def get_previous_summaries(
        self,
        episode_name: str,
        count: int = 2,
    ) -> List[Dict[str, str]]:
        """Get the last N episode summaries before episode_name.

        Assumes episode names sort chronologically (S01E01, S01E02, ...).
        Returns a list of {"episode": ep_name, "summary": summary_text}.
        """
        all_summaries = self.load_all()
        if not all_summaries:
            return []

        sorted_episodes = sorted(all_summaries.keys())

        # Find position of current episode in the sorted list
        try:
            current_idx = sorted_episodes.index(episode_name)
        except ValueError:
            # Not summarised yet: its place in episode order, so going back to fill in
            # an early episode never gets later episodes' plot as "previously".
            import bisect
            current_idx = bisect.bisect_left(sorted_episodes, episode_name)

        start = max(0, current_idx - count)
        recent = sorted_episodes[start:current_idx]
        return [{"episode": ep, "summary": all_summaries[ep]} for ep in recent]

    def format_summaries_for_prompt(self, summaries: Any) -> str:
        """Format a list of summary dicts or a summary mapping into a prompt-ready context block."""
        if not summaries:
            return ""

        if isinstance(summaries, str):
            return summaries

        if isinstance(summaries, dict):
            summaries = [{"episode": k, "summary": v} for k, v in summaries.items()]

        lines = ["Previously in this show:"]
        for item in summaries:
            if isinstance(item, dict):
                ep = item.get("episode", "")
                text = item.get("summary", "")
                if ep and text:
                    lines.append(f"[{ep}] {text}")
                elif text:
                    lines.append(text)
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                lines.append(f"[{item[0]}] {item[1]}")

        if len(lines) <= 1:
            return ""
        return "\n".join(lines)

    def get_recent_summaries(
        self,
        current_episode: str,
        window: int = 3,
    ) -> str:
        """Get the last N episode summaries before the current episode.

        Returns a formatted context string ready for prompt injection.
        """
        prev = self.get_previous_summaries(current_episode, count=window)
        return self.format_summaries_for_prompt(prev)

    def has_summary(self, episode_name: str) -> bool:
        """Check whether a summary exists for an episode."""
        return episode_name in self.load_all()

