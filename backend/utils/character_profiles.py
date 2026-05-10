"""
Character Voice Profiles — Per-character speech pattern tracking.

Stores and retrieves character-specific translation guidance for each project:
- Grammatical gender (masculine/feminine/neuter) — critical for Greek
- Formality level (formal/informal/mixed) — determines pronoun choices
- Speech patterns and verbal tics
- Established phrase translations (consistency across episodes)

Greek-specific motivation:
    Greek adjectives, past tenses, and articles are all gendered. "I was tired"
    translates to "Ήμουν κουρασμένος" (m) vs "Ήμουν κουρασμένη" (f). Without
    knowing the speaker's gender, the model guesses randomly. Character profiles
    solve this by injecting per-scene gender/formality context.

Storage:
    projects/{project_name}/character_profiles.json
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projects"


@dataclass
class CharacterProfile:
    """Voice profile for a single character."""
    name: str
    gender: str = "unknown"           # masculine, feminine, neuter, unknown
    formality: str = "informal"       # formal, informal, mixed
    speech_patterns: str = ""         # Free-text description of how they talk
    verbal_tics: List[str] = field(default_factory=list)  # Catchphrases, repeated words
    established_phrases: Dict[str, str] = field(default_factory=dict)  # EN -> target lang
    episode_first_seen: str = ""      # When this character first appeared
    notes: str = ""                   # Any additional translator notes

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "CharacterProfile":
        return cls(
            name=data.get("name", ""),
            gender=data.get("gender", "unknown"),
            formality=data.get("formality", "informal"),
            speech_patterns=data.get("speech_patterns", ""),
            verbal_tics=data.get("verbal_tics", []),
            established_phrases=data.get("established_phrases", {}),
            episode_first_seen=data.get("episode_first_seen", ""),
            notes=data.get("notes", ""),
        )


class CharacterProfileManager:
    """Manage character profiles for a project.

    Profiles are stored as a single JSON file per project. The manager
    handles CRUD operations and provides methods to inject relevant
    profile context into translation prompts.
    """

    def __init__(self, project_name: str):
        self._project = project_name
        self._file = PROJECTS_DIR / project_name / "character_profiles.json"

    def load_all(self) -> Dict[str, CharacterProfile]:
        """Load all character profiles. Returns {name: CharacterProfile}."""
        if not self._file.exists():
            return {}
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {
                name: CharacterProfile.from_dict({**data, "name": name})
                for name, data in raw.items()
            }
        except Exception as e:
            logger.warning(f"Failed to load character profiles for {self._project}: {e}")
            return {}

    def save_all(self, profiles: Dict[str, CharacterProfile]):
        """Save all character profiles."""
        self._file.parent.mkdir(parents=True, exist_ok=True)
        raw = {}
        for name, profile in profiles.items():
            d = profile.to_dict()
            d.pop("name", None)  # Name is the key, don't duplicate
            raw[name] = d
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)

    def get_profile(self, name: str) -> Optional[CharacterProfile]:
        """Get a single character's profile."""
        profiles = self.load_all()
        return profiles.get(name)

    def update_profile(self, name: str, updates: Dict):
        """Update a character's profile (merge, not replace).

        Creates the profile if it doesn't exist.
        """
        profiles = self.load_all()
        if name in profiles:
            existing = profiles[name]
            for key, value in updates.items():
                if key == "established_phrases" and isinstance(value, dict):
                    existing.established_phrases.update(value)
                elif key == "verbal_tics" and isinstance(value, list):
                    existing.verbal_tics = list(set(existing.verbal_tics + value))
                elif hasattr(existing, key):
                    setattr(existing, key, value)
        else:
            profiles[name] = CharacterProfile(name=name, **updates)
        self.save_all(profiles)

    def delete_profile(self, name: str) -> bool:
        """Delete a character profile. Returns True if it existed."""
        profiles = self.load_all()
        if name in profiles:
            del profiles[name]
            self.save_all(profiles)
            return True
        return False

    def get_profiles_for_chunk(
        self,
        source_lines: List[str],
        glossary: Dict,
    ) -> List[CharacterProfile]:
        """Return profiles for characters that appear in these source lines.

        Detection strategy:
        1. Get all glossary terms of type 'person'
        2. Check if each person's name appears in any source line
        3. Return matching profiles (only those we have profiles for)
        """
        profiles = self.load_all()
        if not profiles:
            return []

        # Build set of person names from glossary
        person_names = set()
        for term in glossary.get("terms", []):
            if term.get("type") == "person":
                person_names.add(term.get("term", ""))

        # Also include all profile names (they might not all be in the glossary)
        person_names.update(profiles.keys())

        # Check which names appear in source text
        source_text = " ".join(source_lines)
        matching = []
        for name in person_names:
            if name and name in source_text and name in profiles:
                matching.append(profiles[name])

        return matching

    def build_profile_context(self, profiles: List[CharacterProfile]) -> str:
        """Format relevant profiles as a compact prompt section.

        Output format:
            Characters in this scene:
            - Frieren (f, informal): calm, understated. "That takes me back" -> "..."
            - Fern (f, mixed): formal with elders, direct.

        Designed to be concise — one line per character with the essential
        translation-relevant info only.
        """
        if not profiles:
            return ""

        lines = ["Characters in this scene:"]
        for p in profiles:
            gender_tag = {"masculine": "m", "feminine": "f", "neuter": "n"}.get(
                p.gender, "?"
            )
            parts = [f"{p.name} ({gender_tag}, {p.formality})"]

            if p.speech_patterns:
                # Truncate to keep prompt compact
                pattern_text = p.speech_patterns[:80]
                parts.append(pattern_text)

            if p.verbal_tics:
                tics = ", ".join(p.verbal_tics[:3])
                parts.append(f"tics: {tics}")

            # Include up to 2 established phrases as examples
            if p.established_phrases:
                phrase_examples = list(p.established_phrases.items())[:2]
                for src, tgt in phrase_examples:
                    parts.append(f'"{src}" -> "{tgt}"')

            lines.append("- " + ": ".join(parts[:2]) + (". " + ". ".join(parts[2:]) if len(parts) > 2 else ""))

        return "\n".join(lines)

    def get_all_as_dicts(self) -> Dict[str, Dict]:
        """Return all profiles as plain dicts (for API responses)."""
        return {name: p.to_dict() for name, p in self.load_all().items()}
