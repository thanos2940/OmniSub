import React from 'react';
import { Sliders, Globe, Layers, Cpu } from 'lucide-react';

const GeneralSettings = ({ settings, onChange }) => {
    const handleSlider = (key, value, isFloat = false) => {
        onChange(key, isFloat ? parseFloat(value) : parseInt(value, 10));
    };

    return (
        <div className="space-y-6">
            {/* Target Language & Basic Config */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-5">
                <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-4">
                    <Globe className="text-indigo-600 dark:text-indigo-400" size={20} />
                    <h3 className="text-base font-bold text-gray-900 dark:text-white">Localization & Defaults</h3>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                            Default Target Language
                        </label>
                        <input
                            type="text"
                            value={settings.default_target_language || ''}
                            onChange={(e) => onChange('default_target_language', e.target.value)}
                            placeholder="e.g. Greek, Spanish, German"
                            className="w-full px-4 py-2.5 rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                        />
                        <p className="text-xs text-gray-400 mt-1">
                            Primary language used when auto-creating projects from Sonarr / Radarr.
                        </p>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                            Episode Request Mode
                        </label>
                        <select
                            value={settings.episode_request_mode || 'auto'}
                            onChange={(e) => onChange('episode_request_mode', e.target.value)}
                            className="w-full px-4 py-2.5 rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                        >
                            <option value="auto">Auto (Full Episode for Gemini with large context)</option>
                            <option value="chunked">Chunked (Scene-by-scene chunks)</option>
                            <option value="per_episode">Whole Episode (Single atomic prompt)</option>
                        </select>
                        <p className="text-xs text-gray-400 mt-1">
                            Controls how long subtitle transcripts are grouped before prompting Gemini.
                        </p>
                    </div>
                </div>
            </div>

            {/* Sampling Parameters */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-5">
                <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-4">
                    <Sliders className="text-indigo-600 dark:text-indigo-400" size={20} />
                    <h3 className="text-base font-bold text-gray-900 dark:text-white">LLM Sampling Hyperparameters</h3>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    {/* Temperature */}
                    <div className="space-y-2">
                        <div className="flex justify-between items-center">
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Temperature</label>
                            <span className="font-mono text-xs font-bold px-2 py-0.5 rounded bg-indigo-100 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300">
                                {parseFloat(settings.temperature ?? 0.3).toFixed(2)}
                            </span>
                        </div>
                        <input
                            type="range"
                            min="0"
                            max="2"
                            step="0.05"
                            value={settings.temperature ?? 0.3}
                            onChange={(e) => handleSlider('temperature', e.target.value, true)}
                            className="w-full h-2 rounded-lg appearance-none cursor-pointer accent-indigo-600 bg-gray-200 dark:bg-gray-700"
                        />
                        <p className="text-[11px] text-gray-400">Lower = more literal &amp; consistent. Higher = more creative.</p>
                    </div>

                    {/* Top-P */}
                    <div className="space-y-2">
                        <div className="flex justify-between items-center">
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Top-P (Nucleus)</label>
                            <span className="font-mono text-xs font-bold px-2 py-0.5 rounded bg-indigo-100 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300">
                                {parseFloat(settings.top_p ?? 0.95).toFixed(2)}
                            </span>
                        </div>
                        <input
                            type="range"
                            min="0.1"
                            max="1"
                            step="0.01"
                            value={settings.top_p ?? 0.95}
                            onChange={(e) => handleSlider('top_p', e.target.value, true)}
                            className="w-full h-2 rounded-lg appearance-none cursor-pointer accent-indigo-600 bg-gray-200 dark:bg-gray-700"
                        />
                        <p className="text-[11px] text-gray-400">Probability threshold for token sampling.</p>
                    </div>

                    {/* Top-K */}
                    <div className="space-y-2">
                        <div className="flex justify-between items-center">
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Top-K</label>
                            <span className="font-mono text-xs font-bold px-2 py-0.5 rounded bg-indigo-100 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300">
                                {settings.top_k ?? 64}
                            </span>
                        </div>
                        <input
                            type="range"
                            min="1"
                            max="100"
                            step="1"
                            value={settings.top_k ?? 64}
                            onChange={(e) => handleSlider('top_k', e.target.value, false)}
                            className="w-full h-2 rounded-lg appearance-none cursor-pointer accent-indigo-600 bg-gray-200 dark:bg-gray-700"
                        />
                        <p className="text-[11px] text-gray-400">Sample top K candidates per step.</p>
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-4 border-t border-gray-100 dark:border-gray-700">
                    {/* Chunk Size */}
                    <div className="space-y-2">
                        <div className="flex justify-between items-center">
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Chunk Size (Lines)</label>
                            <span className="font-mono text-xs font-bold px-2 py-0.5 rounded bg-indigo-100 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300">
                                {settings.chunk_size ?? 150} lines
                            </span>
                        </div>
                        <input
                            type="range"
                            min="50"
                            max="400"
                            step="10"
                            value={settings.chunk_size ?? 150}
                            onChange={(e) => handleSlider('chunk_size', e.target.value, false)}
                            className="w-full h-2 rounded-lg appearance-none cursor-pointer accent-indigo-600 bg-gray-200 dark:bg-gray-700"
                        />
                        <p className="text-[11px] text-gray-400">Maximum subtitle dialogue cues sent in one chunk if chunked.</p>
                    </div>

                    {/* Max Output Tokens */}
                    <div className="space-y-2">
                        <div className="flex justify-between items-center">
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Max Output Tokens</label>
                            <span className="font-mono text-xs font-bold px-2 py-0.5 rounded bg-indigo-100 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300">
                                {settings.max_output_tokens ?? 8192}
                            </span>
                        </div>
                        <input
                            type="range"
                            min="2048"
                            max="32768"
                            step="1024"
                            value={settings.max_output_tokens ?? 8192}
                            onChange={(e) => handleSlider('max_output_tokens', e.target.value, false)}
                            className="w-full h-2 rounded-lg appearance-none cursor-pointer accent-indigo-600 bg-gray-200 dark:bg-gray-700"
                        />
                        <p className="text-[11px] text-gray-400">Output limit per Gemini completion request.</p>
                    </div>
                </div>
            </div>

            {/* Experimental & Conformance */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-4">
                <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-4">
                    <Cpu className="text-indigo-600 dark:text-indigo-400" size={20} />
                    <h3 className="text-base font-bold text-gray-900 dark:text-white">Pipeline Optimization &amp; Conformance</h3>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <label className="flex items-start gap-3 p-3 rounded-xl border border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                        <input
                            type="checkbox"
                            checked={settings.sequential_scenes || false}
                            onChange={(e) => onChange('sequential_scenes', e.target.checked)}
                            className="mt-1 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                        />
                        <div>
                            <span className="text-sm font-semibold text-gray-900 dark:text-white">Sequential Scene Feed</span>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Passes prior translated dialogue memory into subsequent scene prompts.</p>
                        </div>
                    </label>

                    <label className="flex items-start gap-3 p-3 rounded-xl border border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                        <input
                            type="checkbox"
                            checked={settings.prompt_caching_enabled || false}
                            onChange={(e) => onChange('prompt_caching_enabled', e.target.checked)}
                            className="mt-1 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                        />
                        <div>
                            <span className="text-sm font-semibold text-gray-900 dark:text-white">Google GenAI Prompt Caching</span>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Caches static glossary and universe guides on Gemini servers to reduce token latency.</p>
                        </div>
                    </label>
                </div>
            </div>
        </div>
    );
};

export default GeneralSettings;
