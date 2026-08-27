// Quality presets — a coherent group of quality flags applied in one click, so
// users don't have to discover the right combination of ~10 individual toggles.
// Shared between SettingsPage.jsx and the setup wizard (SetupWizard.jsx) so
// there's one definition of what "Fast/Balanced/Best" actually means.
export const QUALITY_PRESETS = {
    fast: {
        label: 'Fast', desc: 'Whole-episode request, minimal extra passes — quickest, cheapest drafts.',
        values: {
            episode_request_mode: 'auto', tm_enabled: true, glossary_enforce_enabled: true,
            repair_pass_enabled: false, intra_episode_reconcile_enabled: false,
            character_profiles_enabled: false, episode_summaries_enabled: false,
            conformance_enabled: false, condense_enabled: false, enable_reviewer: false,
            sequential_scenes: false, new_terms_suggest_enabled: true,
        },
    },
    balanced: {
        label: 'Balanced', desc: 'Whole-episode + deterministic QC + one repair call. No extra LLM review cost.',
        values: {
            episode_request_mode: 'auto', tm_enabled: true, glossary_enforce_enabled: true,
            repair_pass_enabled: true, intra_episode_reconcile_enabled: true,
            character_profiles_enabled: true, episode_summaries_enabled: true,
            conformance_enabled: true, condense_enabled: false, enable_reviewer: false,
            sequential_scenes: false, new_terms_suggest_enabled: true,
        },
    },
    best: {
        label: 'Best', desc: 'Every quality pass on, including the sampled LLM reviewer (slower, costs more).',
        values: {
            episode_request_mode: 'auto', tm_enabled: true, glossary_enforce_enabled: true,
            repair_pass_enabled: true, intra_episode_reconcile_enabled: true,
            character_profiles_enabled: true, episode_summaries_enabled: true,
            conformance_enabled: true, condense_enabled: true, enable_reviewer: true,
            sequential_scenes: false, new_terms_suggest_enabled: true,
        },
    },
};

export const activePresetFor = (settings) => {
    for (const [name, preset] of Object.entries(QUALITY_PRESETS)) {
        if (Object.entries(preset.values).every(([k, v]) => settings[k] === v)) return name;
    }
    return null;
};
