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
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projects"

# Bookkeeping fields that are never user-settable overrides.
_OVERRIDE_IGNORE_FIELDS = ("name", "inherited", "inherited_from", "overridden_fields")


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
    inherited: bool = False
    inherited_from: Optional[str] = None
    # For a child that overrides an inherited character: the field names the user
    # actually changed. Resolution overlays only these over the live parent profile,
    # so unspecified fields keep inheriting (instead of freezing a full parent copy).
    overridden_fields: List[str] = field(default_factory=list)

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
            inherited=data.get("inherited", False),
            inherited_from=data.get("inherited_from", None),
            overridden_fields=data.get("overridden_fields", []),
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

    @staticmethod
    def _apply_updates(profile: "CharacterProfile", updates: Dict,
                       merge_collections: bool = True) -> List[str]:
        """Apply ``updates`` onto ``profile`` in place; return the changed field names.

        ``merge_collections`` merges established_phrases (dict) / verbal_tics (list)
        into the existing values rather than replacing them. Single place that knows
        the per-field merge rules — shared by update_profile and _overlay_override.
        """
        changed = []
        for key, value in updates.items():
            if key == "established_phrases" and isinstance(value, dict):
                if merge_collections:
                    profile.established_phrases.update(value)
                else:
                    profile.established_phrases = dict(value)
                changed.append("established_phrases")
            elif key == "verbal_tics" and isinstance(value, list):
                base_tics = profile.verbal_tics if merge_collections else []
                profile.verbal_tics = list(dict.fromkeys((base_tics or []) + value))
                changed.append("verbal_tics")
            elif hasattr(profile, key) and key not in _OVERRIDE_IGNORE_FIELDS:
                setattr(profile, key, value)
                changed.append(key)
        return changed

    @staticmethod
    def _overlay_override(parent: "CharacterProfile", child: "CharacterProfile",
                          parent_name: str) -> "CharacterProfile":
        """Overlay a child's explicit field overrides onto the live parent profile.

        Only the fields in ``child.overridden_fields`` win; everything else keeps
        inheriting from the parent, so a one-field edit no longer freezes a full
        copy of the parent. Legacy child entries (no ``overridden_fields``) fully
        replace the parent, preserving the old behavior.
        """
        overridden = [f for f in (child.overridden_fields or []) if f not in _OVERRIDE_IGNORE_FIELDS]
        if not overridden:
            child.inherited = False
            return child
        # Cheap independent copy of the parent: replace() shallow-copies, and we hand
        # it fresh mutable containers so merges can't leak back into the parent.
        base = replace(parent,
                       established_phrases=dict(parent.established_phrases),
                       verbal_tics=list(parent.verbal_tics))
        child_values = {f: getattr(child, f) for f in overridden if hasattr(child, f)}
        CharacterProfileManager._apply_updates(base, child_values, merge_collections=True)
        base.name = child.name
        base.inherited = False
        base.inherited_from = parent_name
        base.overridden_fields = list(overridden)
        return base

    def load_all_resolved(self, _seen: Optional[set] = None) -> Dict[str, CharacterProfile]:
        """Load child profiles merged with parent profiles if parent_project is defined."""
        local_profiles = self.load_all()
        for p in local_profiles.values():
            p.inherited = False

        from utils import storage
        meta = storage.load_project_metadata(self._project)
        if not meta:
            return local_profiles

        parent_name = meta.get("parent_project")
        settings = meta.get("settings", {})
        inherit_chars = settings.get("inherit_characters", True)

        # Cycle guard: a self-parent or a parent loop would otherwise recurse forever.
        if _seen is None:
            _seen = set()
        if parent_name and parent_name in _seen:
            parent_name = None
        _seen.add(self._project)

        if parent_name and inherit_chars:
            parent_mgr = CharacterProfileManager(parent_name)
            parent_profiles = parent_mgr.load_all_resolved(_seen=_seen)

            merged = {}
            for name, profile in parent_profiles.items():
                profile.inherited = True
                if not profile.inherited_from:
                    profile.inherited_from = parent_name
                merged[name] = profile

            for name, profile in local_profiles.items():
                if name in merged:
                    # Child exists alongside an inherited parent character — overlay
                    # only the explicitly-overridden fields onto the parent.
                    merged[name] = self._overlay_override(merged[name], profile, parent_name)
                else:
                    profile.inherited = False
                    merged[name] = profile

            return merged

        return local_profiles

    def save_all(self, profiles: Dict[str, CharacterProfile]):
        """Save all character profiles."""
        self._file.parent.mkdir(parents=True, exist_ok=True)
        raw = {}
        for name, profile in profiles.items():
            if getattr(profile, "inherited", False):
                continue
            d = profile.to_dict()
            d.pop("name", None)  # Name is the key, don't duplicate
            d.pop("inherited", None)
            d.pop("inherited_from", None)
            # Keep overridden_fields only when it carries an actual override, so
            # plain local characters don't accumulate empty bookkeeping.
            if not d.get("overridden_fields"):
                d.pop("overridden_fields", None)
            raw[name] = d
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)

    def get_profile(self, name: str) -> Optional[CharacterProfile]:
        """Get a single character's profile."""
        profiles = self.load_all_resolved()
        return profiles.get(name)

    def update_profile(self, name: str, updates: Dict):
        """Update a character's profile (merge, not replace).

        Creates the profile if it doesn't exist.
        """
        profiles = self.load_all()
        if name in profiles:
            existing = profiles[name]
            changed = self._apply_updates(existing, updates, merge_collections=True)
            # Track the override so an inherited character only diverges on edited
            # fields (no-op for a plain local character — it has no parent twin).
            existing.overridden_fields = list(dict.fromkeys((existing.overridden_fields or []) + changed))
        elif name in self.load_all_resolved():
            # Override of an inherited character: store ONLY the changed fields so
            # everything else keeps inheriting live from the parent.
            new_profile = CharacterProfile(name=name)
            new_profile.overridden_fields = self._apply_updates(new_profile, updates, merge_collections=False)
            profiles[name] = new_profile
        else:
            clean = {k: v for k, v in updates.items() if k not in _OVERRIDE_IGNORE_FIELDS}
            profiles[name] = CharacterProfile(name=name, **clean)
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
        profiles = self.load_all_resolved()
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
        return {name: p.to_dict() for name, p in self.load_all_resolved().items()}
