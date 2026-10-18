import React from 'react';
import { Sparkles, Check, Zap, Gauge, CheckCircle2 } from 'lucide-react';
import { QUALITY_PRESETS, activePresetFor } from './presets';

const PresetsSettings = ({ settings, onChange, onApplyPreset }) => {
    const currentPreset = activePresetFor(settings);

    const handleToggle = (key) => {
        onChange(key, !settings[key]);
    };

    return (
        <div className="space-y-6">
            {/* Presets Selection Cards */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-5">
                <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-4">
                    <Zap className="text-amber-500" size={20} />
                    <div>
                        <h3 className="text-base font-bold text-gray-900 dark:text-white">Translation Quality Presets</h3>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                            One-click presets that configure the Spherical Context QC pipeline for speed or fidelity.
                        </p>
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    {Object.entries(QUALITY_PRESETS).map(([key, preset]) => {
                        const isActive = currentPreset === key;
                        return (
                            <button
                                key={key}
                                type="button"
                                onClick={() => onApplyPreset(preset.values)}
                                className={`p-4 rounded-2xl border text-left transition-all relative ${
                                    isActive
                                        ? 'border-indigo-500 bg-indigo-50/60 dark:bg-indigo-950/30 ring-2 ring-indigo-500 shadow-sm'
                                        : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 bg-gray-50/50 dark:bg-gray-850'
                                }`}
                            >
                                <div className="flex items-center justify-between mb-2">
                                    <span className="font-bold text-base text-gray-900 dark:text-white">{preset.label}</span>
                                    {isActive && (
                                        <span className="p-1 bg-indigo-600 text-white rounded-full">
                                            <Check size={12} />
                                        </span>
                                    )}
                                </div>
                                <p className="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">
                                    {preset.desc}
                                </p>
                            </button>
                        );
                    })}
                </div>
            </div>

            {/* Individual Pipeline Stages & Quality Passes */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-4">
                <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-4">
                    <Gauge className="text-indigo-600 dark:text-indigo-400" size={20} />
                    <div>
                        <h3 className="text-base font-bold text-gray-900 dark:text-white">Individual Pipeline Stages (Spherical Context)</h3>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                            Fine-tune individual QA passes, memory lookups, and post-translation validators.
                        </p>
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {/* TM Lookup */}
                    <label className="flex items-start gap-3 p-3.5 rounded-xl border border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                        <input
                            type="checkbox"
                            checked={settings.tm_enabled ?? true}
                            onChange={() => handleToggle('tm_enabled')}
                            className="mt-1 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                        />
                        <div>
                            <span className="text-sm font-semibold text-gray-900 dark:text-white">Translation Memory (TM)</span>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Reuses verified recurring translations across episodes to preserve consistency.</p>
                        </div>
                    </label>

                    {/* Dynamic Glossary Enforcer */}
                    <label className="flex items-start gap-3 p-3.5 rounded-xl border border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                        <input
                            type="checkbox"
                            checked={settings.glossary_enforce_enabled ?? true}
                            onChange={() => handleToggle('glossary_enforce_enabled')}
                            className="mt-1 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                        />
                        <div>
                            <span className="text-sm font-semibold text-gray-900 dark:text-white">Glossary Strict Enforcement</span>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Forces Gemini to strictly adhere to terms and character titles in the project glossary.</p>
                        </div>
                    </label>

                    {/* Character Profiles */}
                    <label className="flex items-start gap-3 p-3.5 rounded-xl border border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                        <input
                            type="checkbox"
                            checked={settings.character_profiles_enabled || false}
                            onChange={() => handleToggle('character_profiles_enabled')}
                            className="mt-1 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                        />
                        <div>
                            <span className="text-sm font-semibold text-gray-900 dark:text-white">Character Speech Profiles</span>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Injects character tone, formality, and gender context into dialogue translation prompts.</p>
                        </div>
                    </label>

                    {/* Episode Summaries */}
                    <label className="flex items-start gap-3 p-3.5 rounded-xl border border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                        <input
                            type="checkbox"
                            checked={settings.episode_summaries_enabled || false}
                            onChange={() => handleToggle('episode_summaries_enabled')}
                            className="mt-1 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                        />
                        <div>
                            <span className="text-sm font-semibold text-gray-900 dark:text-white">Episode Summaries Context</span>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Maintains continuity across episodes by feeding prior episode plot summaries.</p>
                        </div>
                    </label>

                    {/* Repair Pass */}
                    <label className="flex items-start gap-3 p-3.5 rounded-xl border border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                        <input
                            type="checkbox"
                            checked={settings.repair_pass_enabled || false}
                            onChange={() => handleToggle('repair_pass_enabled')}
                            className="mt-1 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                        />
                        <div>
                            <span className="text-sm font-semibold text-gray-900 dark:text-white">Repair Pass (Targeted Re-translation)</span>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Re-translates low-confidence or format-flagged lines in an isolated repair round.</p>
                        </div>
                    </label>

                    {/* QC Reviewer */}
                    <label className="flex items-start gap-3 p-3.5 rounded-xl border border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                        <input
                            type="checkbox"
                            checked={settings.enable_reviewer || false}
                            onChange={() => handleToggle('enable_reviewer')}
                            className="mt-1 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                        />
                        <div>
                            <span className="text-sm font-semibold text-gray-900 dark:text-white">AI QC Reviewer Agent</span>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Spawns an independent Reviewer agent to grade translations and score quality.</p>
                        </div>
                    </label>

                    {/* Format Conformance */}
                    <label className="flex items-start gap-3 p-3.5 rounded-xl border border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                        <input
                            type="checkbox"
                            checked={settings.conformance_enabled || false}
                            onChange={() => handleToggle('conformance_enabled')}
                            className="mt-1 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                        />
                        <div>
                            <span className="text-sm font-semibold text-gray-900 dark:text-white">Subtitle Format Conformance</span>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Validates reading speed (CPS &lt; 21), line lengths, and subtitle tag syntax.</p>
                        </div>
                    </label>

                    {/* Term Harvesting */}
                    <label className="flex items-start gap-3 p-3.5 rounded-xl border border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                        <input
                            type="checkbox"
                            checked={settings.new_terms_suggest_enabled ?? true}
                            onChange={() => handleToggle('new_terms_suggest_enabled')}
                            className="mt-1 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                        />
                        <div>
                            <span className="text-sm font-semibold text-gray-900 dark:text-white">Dynamic Term Harvesting Suggestions</span>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Detects novel recurring character names or terms during translation for review.</p>
                        </div>
                    </label>
                </div>
            </div>
        </div>
    );
};

export default PresetsSettings;
