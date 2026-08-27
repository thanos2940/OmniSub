import React, { useState, useEffect, useCallback } from 'react';
import { Play, RefreshCw, Sparkles, Zap, SlidersHorizontal, ChevronDown, ChevronUp, Save } from 'lucide-react';
import { api } from '../api';
import { motion, AnimatePresence } from 'framer-motion';
import ModelCombobox from './ModelCombobox';

const DEFAULT_LINES = [
    "What does it matter? It's just a curse.",
    "You don't understand the weight of this identity.",
    "The cherry blossoms are falling early this year.",
    "Wait! Don't go into the forbidden forest alone!",
];

// Controlled slider with live value badge
const Slider = ({ label, value, min, max, step, onChange, format }) => (
    <div className="space-y-1.5">
        <div className="flex justify-between items-center">
            <span className="text-xs font-medium text-gray-600 dark:text-gray-400">{label}</span>
            <span className="text-xs font-mono font-bold px-2 py-0.5 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-md">
                {format ? format(value) : value}
            </span>
        </div>
        <input
            type="range" min={min} max={max} step={step} value={value}
            onChange={(e) => onChange(step < 1 ? parseFloat(e.target.value) : parseInt(e.target.value, 10))}
            className="w-full h-1.5 rounded-lg appearance-none cursor-pointer accent-indigo-600 bg-gray-200 dark:bg-gray-700"
        />
    </div>
);

const TranslationSandbox = ({ project, projectName, onSettingsChange }) => {
    const [globalSettings, setGlobalSettings] = useState(null);

    useEffect(() => {
        api.getSettings().then(res => setGlobalSettings(res.data)).catch(() => {});
    }, []);

    // ── Settings state — fully synced from project.settings ──
    const getSettings = useCallback(() => {
        const s = project?.settings || {};
        return {
            temperature:       s.temperature       ?? globalSettings?.temperature       ?? 0.3,
            top_k:             s.top_k             ?? globalSettings?.top_k             ?? 64,
            top_p:             s.top_p             ?? globalSettings?.top_p             ?? 0.95,
            translation_model: s.translation_model ?? globalSettings?.default_translation_model ?? 'gemini-2.5-flash',
        };
    }, [project, globalSettings]);

    const [localSettings, setLocalSettings] = useState(getSettings);
    const [settingsOpen, setSettingsOpen]   = useState(false);
    const [isSaving, setIsSaving]           = useState(false);
    const [savedOk, setSavedOk]             = useState(false);

    // Re-sync if the parent project prop or globalSettings changes
    useEffect(() => {
        setLocalSettings(getSettings());
    }, [getSettings]);

    const updateSetting = (key, value) => {
        setLocalSettings(prev => ({ ...prev, [key]: value }));
        setSavedOk(false);
    };

    const handleSaveSettings = async () => {
        setIsSaving(true);
        try {
            const merged = { ...(project?.settings || {}), ...localSettings };
            await api.updateProject(projectName, { settings: merged });
            onSettingsChange?.(merged);
            setSavedOk(true);
            setTimeout(() => setSavedOk(false), 2500);
        } catch (e) {
            console.error('Failed to save settings', e);
        } finally {
            setIsSaving(false);
        }
    };

    // ── Sandbox state ──
    const [testLines, setTestLines] = useState(DEFAULT_LINES);
    const [isTesting, setIsTesting] = useState(false);
    const [testResult, setTestResult] = useState(null);
    const [error, setError] = useState(null);

    const handleRunTest = async () => {
        setIsTesting(true);
        setTestResult(null);
        setError(null);
        try {
            const res = await api.testTranslation(projectName, {
                lines: testLines,
                temperature:  localSettings.temperature,
                top_k:        localSettings.top_k,
                top_p:        localSettings.top_p,
                model_name:   localSettings.translation_model || undefined,
            });
            setTestResult(res.data);
        } catch (e) {
            setError(e.response?.data?.detail || e.message || 'Translation test failed.');
        } finally {
            setIsTesting(false);
        }
    };

    return (
        <div className="space-y-6">
            {/* Info banner */}
            <div className="flex items-start gap-3 p-4 bg-gradient-to-r from-indigo-50 to-purple-50 dark:from-indigo-900/20 dark:to-purple-900/20 rounded-2xl border border-indigo-100 dark:border-indigo-900/30">
                <Zap className="w-5 h-5 text-indigo-500 flex-shrink-0 mt-0.5" />
                <div>
                    <p className="text-sm font-semibold text-indigo-800 dark:text-indigo-300">Translation Workbench</p>
                    <p className="text-xs text-indigo-600/80 dark:text-indigo-400/80 mt-0.5">
                        Benchmark how different sampling settings and models affect translation quality using this project's real glossary and context guide. Changes here are saved to the project and reflected in the ⚙ Settings modal too.
                    </p>
                </div>
            </div>

            {/* ── Sampling Parameters (collapsible) ── */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 overflow-hidden shadow-sm">
                <button
                    onClick={() => setSettingsOpen(v => !v)}
                    className="w-full flex items-center justify-between p-5 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors text-left"
                >
                    <div className="flex items-center gap-3">
                        <SlidersHorizontal className="w-4 h-4 text-gray-500" />
                        <span className="text-sm font-semibold text-gray-800 dark:text-white">Sampling Parameters & Model</span>
                        <div className="hidden sm:flex items-center gap-1.5">
                            <span className="text-[10px] font-mono bg-gray-100 dark:bg-gray-700 text-gray-500 px-2 py-0.5 rounded-md">
                                T={localSettings.temperature.toFixed(2)}
                            </span>
                            <span className="text-[10px] font-mono bg-gray-100 dark:bg-gray-700 text-gray-500 px-2 py-0.5 rounded-md">
                                K={localSettings.top_k}
                            </span>
                            <span className="text-[10px] font-mono bg-gray-100 dark:bg-gray-700 text-gray-500 px-2 py-0.5 rounded-md">
                                P={localSettings.top_p.toFixed(2)}
                            </span>
                        </div>
                    </div>
                    {settingsOpen
                        ? <ChevronUp className="w-4 h-4 text-gray-400" />
                        : <ChevronDown className="w-4 h-4 text-gray-400" />
                    }
                </button>

                <AnimatePresence>
                    {settingsOpen && (
                        <motion.div
                            initial={{ height: 0, opacity: 0 }}
                            animate={{ height: 'auto', opacity: 1 }}
                            exit={{ height: 0, opacity: 0 }}
                            transition={{ duration: 0.2 }}
                            className="overflow-hidden"
                        >
                            <div className="px-5 pb-5 pt-4 border-t border-gray-100 dark:border-gray-700 space-y-5">
                                {/* Model selector (full row) */}
                                <ModelCombobox
                                    label="Translation Model"
                                    value={localSettings.translation_model}
                                    onChange={(v) => updateSetting('translation_model', v)}
                                />

                                {/* Sliders row */}
                                <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
                                    <Slider
                                        label="Temperature"
                                        value={localSettings.temperature}
                                        min={0} max={2} step={0.05}
                                        onChange={(v) => updateSetting('temperature', v)}
                                        format={(v) => v.toFixed(2)}
                                    />
                                    <Slider
                                        label="Top-K"
                                        value={localSettings.top_k}
                                        min={1} max={100} step={1}
                                        onChange={(v) => updateSetting('top_k', v)}
                                    />
                                    <Slider
                                        label="Top-P"
                                        value={localSettings.top_p}
                                        min={0.1} max={1} step={0.01}
                                        onChange={(v) => updateSetting('top_p', v)}
                                        format={(v) => v.toFixed(2)}
                                    />
                                </div>

                                {/* Save row */}
                                <div className="flex items-center justify-between pt-1">
                                    <p className="text-xs text-gray-400 italic">
                                        These settings sync with the Project ⚙ Settings modal.
                                    </p>
                                    <button
                                        onClick={handleSaveSettings}
                                        disabled={isSaving}
                                        className={`flex items-center gap-1.5 text-xs font-bold px-3 py-1.5 rounded-lg transition-all ${
                                            savedOk
                                                ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                                                : 'bg-indigo-600 hover:bg-indigo-700 text-white disabled:opacity-60'
                                        }`}
                                    >
                                        {isSaving
                                            ? <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                                            : <Save className="w-3.5 h-3.5" />
                                        }
                                        {savedOk ? 'Saved!' : 'Save to Project'}
                                    </button>
                                </div>
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>

            {/* ── Input + Output grid ── */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Left: Editable Lines */}
                <div className="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden">
                    <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 dark:border-gray-700">
                        <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest">Benchmark Sentences</h3>
                        <button
                            onClick={() => setTestLines(DEFAULT_LINES)}
                            className="text-[10px] text-indigo-500 hover:text-indigo-700 font-medium transition-colors"
                        >
                            Reset to defaults
                        </button>
                    </div>
                    <div className="p-5 space-y-4">
                        {testLines.map((line, idx) => (
                            <div key={idx} className="flex gap-3">
                                <div className="flex-shrink-0 w-6 h-6 rounded-lg bg-indigo-50 dark:bg-indigo-900/30 flex items-center justify-center text-[10px] font-bold text-indigo-500">
                                    {idx + 1}
                                </div>
                                <textarea
                                    value={line}
                                    onChange={(e) => {
                                        const updated = [...testLines];
                                        updated[idx] = e.target.value;
                                        setTestLines(updated);
                                    }}
                                    rows={2}
                                    className="flex-1 bg-gray-50 dark:bg-gray-700/50 border border-gray-100 dark:border-gray-600 rounded-xl px-3 py-2 text-sm text-gray-800 dark:text-white outline-none focus:ring-2 focus:ring-indigo-500 resize-none transition-all"
                                />
                            </div>
                        ))}
                        <button
                            onClick={handleRunTest}
                            disabled={isTesting}
                            className="w-full mt-2 flex items-center justify-center gap-2 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-700 hover:to-purple-700 disabled:opacity-60 text-white px-4 py-3 rounded-xl text-sm font-bold transition-all shadow-md shadow-indigo-500/20 hover:shadow-indigo-500/40 hover:-translate-y-0.5 active:translate-y-0"
                        >
                            {isTesting
                                ? <><RefreshCw className="w-4 h-4 animate-spin" /> Running…</>
                                : <><Play className="w-4 h-4" /> Run Benchmark</>
                            }
                        </button>
                    </div>
                </div>

                {/* Right: AI Response */}
                <div className="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden flex flex-col">
                    <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
                        <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest">AI Response</h3>
                        {testResult && (
                            <span className="text-[10px] font-mono text-gray-400">
                                {localSettings.translation_model} · T={localSettings.temperature.toFixed(2)}
                            </span>
                        )}
                    </div>
                    <div className="p-5 flex-1">
                        {error && (
                            <div className="p-3 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 text-xs rounded-xl border border-red-100 dark:border-red-900/30">
                                {error}
                            </div>
                        )}

                        {isTesting && !testResult && (
                            <div className="h-full flex flex-col items-center justify-center gap-3 text-gray-400 py-10">
                                <RefreshCw className="w-8 h-8 animate-spin text-indigo-400" />
                                <p className="text-xs italic">Model is thinking…</p>
                            </div>
                        )}

                        {testResult && (
                            <div className="space-y-4">
                                {testResult.reasoning && (
                                    <div className="bg-amber-50/60 dark:bg-amber-900/10 p-4 rounded-xl border border-amber-100 dark:border-amber-900/30">
                                        <div className="flex items-center gap-2 mb-2">
                                            <Sparkles className="w-3.5 h-3.5 text-amber-500" />
                                            <span className="text-[10px] font-black uppercase tracking-widest text-amber-600 dark:text-amber-400">Internal Reasoning</span>
                                        </div>
                                        <p className="text-xs text-amber-800/80 dark:text-amber-300/80 italic leading-relaxed whitespace-pre-wrap">
                                            {testResult.reasoning}
                                        </p>
                                    </div>
                                )}
                                <div className="space-y-2">
                                    {testResult.translations?.length > 0
                                        ? testResult.translations.map((t, i) => (
                                            <div key={i} className="flex gap-3 p-3 rounded-xl bg-gray-50 dark:bg-gray-700/50 border border-gray-100 dark:border-gray-600">
                                                <span className="text-[10px] font-bold font-mono text-indigo-400 pt-0.5 flex-shrink-0">{t.index}</span>
                                                <p className="text-sm text-gray-800 dark:text-white font-medium leading-snug">{t.text}</p>
                                            </div>
                                        ))
                                        : testResult.raw && (
                                            <div className="p-4 bg-gray-50 dark:bg-gray-700 rounded-xl border border-gray-100 dark:border-gray-600">
                                                <p className="text-xs font-mono text-gray-600 dark:text-gray-300 whitespace-pre-wrap">{testResult.raw}</p>
                                            </div>
                                        )
                                    }
                                </div>
                            </div>
                        )}

                        {!testResult && !isTesting && !error && (
                            <div className="h-full flex flex-col items-center justify-center gap-3 text-gray-300 dark:text-gray-600 py-10">
                                <Play className="w-10 h-10" />
                                <p className="text-xs italic text-center">
                                    Hit "Run Benchmark" to test how<br />
                                    this project's glossary affects translation.
                                </p>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default TranslationSandbox;
