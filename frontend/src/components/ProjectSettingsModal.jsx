import React, { useState, useEffect } from 'react';
import { X, Save, Settings, AlertCircle, Check } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import ModelCombobox from './ModelCombobox';

const ProjectSettingsModal = ({ isOpen, onClose, project, settings: initialSettings, onSave, tokenSummary }) => {
    const [settings, setSettings] = useState({
        translation_model: '',
        context_model: '',
        glossary_model: '',
        local_llm_base_url: 'http://localhost:11434',
        auto_export_enabled: false,
        auto_export_dir: '',
        apply_subtitle_edit_fixes: true,
        translate_all_source_formats: false,
        temperature: 0.3,
        top_k: 40,
        top_p: 1.0
    });
    const [testStatus, setTestStatus] = useState(null); // 'testing', 'success', 'error'
    const [testError, setTestError] = useState(null);
    const [diagnostics, setDiagnostics] = useState(null);
    const [parentProject, setParentProject] = useState('');
    const [allProjects, setAllProjects] = useState([]);
    const [globalSettings, setGlobalSettings] = useState(null);
    const [extraLangs, setExtraLangs] = useState('');  // Plan 15: additional target languages

    useEffect(() => {
        if (isOpen) {
            import('../api').then(({ api }) => {
                api.getProjects().then(res => setAllProjects(res.data.filter(p => p.name !== project?.name && p.type !== 'episode')));
                api.getSettings().then(res => setGlobalSettings(res.data));
            });
        }
    }, [isOpen, project]);

    useEffect(() => {
        const src = initialSettings || (project && project.settings);
        if (src) {
            setSettings(prev => ({ ...prev, ...src }));
        }
        if (project && project.parent_project) {
            setParentProject(project.parent_project);
        } else {
            setParentProject('');
        }
        // Additional target languages beyond the primary (Plan 15).
        const primary = project?.target_language;
        const extras = (project?.target_languages || []).filter(l => l && l !== primary);
        setExtraLangs(extras.join(', '));
    }, [project, initialSettings, isOpen]);

    const handleChange = (key, value) => {
        setSettings(prev => ({ ...prev, [key]: value }));
    };

    const handleSave = () => {
        // Persist additional target languages (Plan 15) directly to project metadata.
        if (project?.name) {
            const primary = project?.target_language;
            const extras = extraLangs.split(',').map(s => s.trim()).filter(Boolean);
            const target_languages = [primary, ...extras].filter(Boolean);
            const uniqueLangs = [...new Set(target_languages)];
            import('../api').then(({ api }) => {
                api.updateProject(project.name, { target_languages: uniqueLangs }).catch(() => {});
            });
        }
        onSave(settings, parentProject || null);
        onClose();
    };

    const handleTestConnection = async () => {
        setTestStatus('testing');
        try {
            const { api } = await import('../api');
            const res = await api.fetchLocalModels(settings.local_llm_base_url);
            if (res.data && res.data.models && res.data.models.length > 0) {
                setTestStatus('success');
                setTestError(null);
                setDiagnostics(null);
                // Auto-save verified local URL to global settings
                await api.updateSettings({ local_llm_base_url: settings.local_llm_base_url });
            } else {
                setTestStatus('error');
                setTestError(res.data?.error || "No models detected on this server");
                setDiagnostics(res.data?.diagnostics || null);
            }
        } catch (e) {
            setTestStatus('error');
            setTestError(e.message || "Connection failed");
            setDiagnostics(null);
        }
    };

    if (!isOpen) return null;

    return (
        <AnimatePresence>
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
                <motion.div
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="bg-white dark:bg-gray-800 rounded-xl shadow-xl w-full max-w-md max-h-[90vh] flex flex-col overflow-hidden"
                >
                    <div className="flex justify-between items-center p-6 border-b border-gray-200 dark:border-gray-700">
                        <div className="flex items-center gap-2">
                            <Settings className="text-gray-500" size={20} />
                            <h2 className="text-xl font-bold text-gray-900 dark:text-white">Project Settings</h2>
                        </div>
                        <button onClick={onClose} className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">
                            <X size={24} />
                        </button>
                    </div>

                    <div className="p-6 space-y-5 flex-1 overflow-y-auto custom-scrollbar">
                        <ModelCombobox
                            label="Translation Model"
                            value={settings.translation_model}
                            onChange={(v) => handleChange('translation_model', v)}
                            placeholder={globalSettings ? `Global: ${globalSettings.default_translation_model}` : "Select model..."}
                        />
                        <ModelCombobox
                            label="Context Analysis Model"
                            value={settings.context_model}
                            onChange={(v) => handleChange('context_model', v)}
                            placeholder={globalSettings ? `Global: ${globalSettings.default_context_model}` : "Select model..."}
                        />
                        <ModelCombobox
                            label="Glossary Generation Model"
                            value={settings.glossary_model}
                            onChange={(v) => handleChange('glossary_model', v)}
                            placeholder={globalSettings ? `Global: ${globalSettings.default_glossary_model}` : "Select model..."}
                        />

                        <div className="pt-4 border-t border-gray-100 dark:border-gray-700">
                            <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Project Organization</h3>
                            <div className="space-y-1.5">
                                <label className="text-sm text-gray-600 dark:text-gray-400">Parent Universe</label>
                                <select
                                    value={parentProject}
                                    onChange={(e) => setParentProject(e.target.value)}
                                    className="w-full px-4 py-2 rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                                >
                                    <option value="">None (Standalone)</option>
                                    {allProjects.map(p => (
                                        <option key={p.name} value={p.name}>{p.name}</option>
                                    ))}
                                </select>
                                <p className="text-[10px] text-gray-400 italic mb-2">
                                    Link this project to a parent universe to share its glossary and context guide.
                                </p>
                                {parentProject && (
                                    <div className="mt-3 space-y-2 pl-3 border-l-2 border-indigo-500/35">
                                        <label className="flex items-center gap-2 cursor-pointer select-none">
                                            <input
                                                type="checkbox"
                                                checked={settings.inherit_glossary !== false}
                                                onChange={(e) => handleChange('inherit_glossary', e.target.checked)}
                                                className="w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300 dark:border-gray-600 dark:bg-gray-700"
                                            />
                                            <span className="text-xs text-gray-750 dark:text-gray-250 font-medium">Inherit Parent Glossary</span>
                                        </label>
                                        <label className="flex items-center gap-2 cursor-pointer select-none">
                                            <input
                                                type="checkbox"
                                                checked={settings.inherit_context !== false}
                                                onChange={(e) => handleChange('inherit_context', e.target.checked)}
                                                className="w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300 dark:border-gray-600 dark:bg-gray-700"
                                            />
                                            <span className="text-xs text-gray-750 dark:text-gray-250 font-medium">Inherit Parent Context Guide</span>
                                        </label>
                                        <label className="flex items-center gap-2 cursor-pointer select-none">
                                            <input
                                                type="checkbox"
                                                checked={settings.inherit_characters !== false}
                                                onChange={(e) => handleChange('inherit_characters', e.target.checked)}
                                                className="w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300 dark:border-gray-600 dark:bg-gray-700"
                                            />
                                            <span className="text-xs text-gray-750 dark:text-gray-250 font-medium">Inherit Parent Character Profiles</span>
                                        </label>
                                    </div>
                                )}
                            </div>
                        </div>

                        <div className="pt-4 border-t border-gray-100 dark:border-gray-700">
                            <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Sampling Parameters</h3>
                            <div className="space-y-4">
                                {[
                                    { key: 'temperature', label: 'Temperature', min: 0, max: 2, step: 0.05, default: 0.3, format: v => parseFloat(v).toFixed(2) },
                                    { key: 'top_k', label: 'Top-K', min: 1, max: 100, step: 1, default: 40, format: v => v },
                                    { key: 'top_p', label: 'Top-P', min: 0.1, max: 1, step: 0.01, default: 1.0, format: v => parseFloat(v).toFixed(2) },
                                ].map(({ key, label, min, max, step, default: def, format }) => (
                                    <div key={key} className="space-y-1.5">
                                        <div className="flex justify-between">
                                            <label className="text-sm text-gray-600 dark:text-gray-400">{label}</label>
                                            <span className="text-xs font-mono font-bold px-2 py-0.5 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-md">
                                                {format(settings[key] ?? def)}
                                            </span>
                                        </div>
                                        <input
                                            type="range" min={min} max={max} step={step}
                                            value={settings[key] ?? def}
                                            onChange={(e) => handleChange(key, step < 1 ? parseFloat(e.target.value) : parseInt(e.target.value, 10))}
                                            className="w-full h-1.5 rounded-lg appearance-none cursor-pointer accent-indigo-600 bg-gray-200 dark:bg-gray-700"
                                        />
                                    </div>
                                ))}
                                <p className="text-[10px] text-gray-400 italic">These settings also apply in the Translation Sandbox tab.</p>
                            </div>
                        </div>

                        <div className="pt-4 border-t border-gray-100 dark:border-gray-700">
                            <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Context Budget Analysis</h3>
                            {tokenSummary ? (
                                <div className="p-4 rounded-2xl bg-indigo-50/50 dark:bg-indigo-900/10 border border-indigo-100/50 dark:border-indigo-900/20 space-y-2">
                                    <div className="flex justify-between text-xs">
                                        <span className="text-gray-500">Glossary Cost:</span>
                                        <span className="font-mono font-bold text-indigo-600">{tokenSummary.glossary_tokens} tokens</span>
                                    </div>
                                    <div className="flex justify-between text-xs">
                                        <span className="text-gray-500">Context Guide:</span>
                                        <span className="font-mono font-bold text-indigo-600">{tokenSummary.context_tokens} tokens</span>
                                    </div>
                                    <div className="flex justify-between text-xs">
                                        <span className="text-gray-500">Base Instructions:</span>
                                        <span className="font-mono font-bold text-indigo-600">{tokenSummary.base_instruction_tokens} tokens</span>
                                    </div>
                                    <div className="pt-2 border-t border-indigo-100/30 flex justify-between text-xs font-bold">
                                        <span className="text-gray-700 dark:text-gray-300">Total Base Load:</span>
                                        <span className="text-indigo-700 dark:text-indigo-400">{tokenSummary.total_static_tokens} tokens</span>
                                    </div>
                                    <div className="pt-1 flex justify-between text-[10px] text-gray-400 italic">
                                        <span>+ Avg. Chunk (Ref: 200 lines):</span>
                                        <span>~6,500 tokens</span>
                                    </div>
                                    <div className="pt-2 border-t border-indigo-200/30 flex justify-between text-sm font-black">
                                        <span className="text-gray-900 dark:text-white uppercase tracking-tighter">Est. Total Chunk:</span>
                                        <span className="text-indigo-600 dark:text-indigo-400">{tokenSummary.total_static_tokens + 6500} tokens</span>
                                    </div>
                                </div>
                            ) : (
                                <p className="text-xs text-center text-gray-400 italic">Waiting for token summary...</p>
                            )}
                            <p className="text-[10px] text-gray-400 mt-2 italic px-1">
                                Base Load is sent with every scene. Reducing glossary entries or context length saves context window space.
                            </p>
                        </div>

                        <div className="pt-4 border-t border-gray-100 dark:border-gray-700">
                            <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Auto Export</h3>
                            <div className="space-y-4">
                                <label className="flex items-center gap-3 cursor-pointer group">
                                    <div className="relative flex items-center justify-center">
                                        <input
                                            type="checkbox"
                                            checked={settings.auto_export_enabled || false}
                                            onChange={(e) => handleChange('auto_export_enabled', e.target.checked)}
                                            className="sr-only"
                                        />
                                        <div className={`w-5 h-5 rounded border transition-all flex items-center justify-center ${settings.auto_export_enabled ? 'bg-indigo-600 border-indigo-600' : 'border-gray-300 dark:border-gray-600 group-hover:border-indigo-400'}`}>
                                            {settings.auto_export_enabled && <Check size={14} className="text-white" />}
                                        </div>
                                    </div>
                                    <span className="text-sm text-gray-700 dark:text-gray-300 font-medium">Enable automatic export on translation completion</span>
                                </label>

                                {settings.auto_export_enabled && (
                                    <div className="space-y-1.5 pl-8 animate-in fade-in slide-in-from-top-2">
                                        <label className="text-sm text-gray-600 dark:text-gray-400">Export Directory</label>
                                        <input
                                            type="text"
                                            value={settings.auto_export_dir || ''}
                                            onChange={(e) => handleChange('auto_export_dir', e.target.value)}
                                            placeholder="e.g. C:\Downloads\Subtitles"
                                            className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                                        />
                                        <p className="text-xs text-gray-500 dark:text-gray-400">
                                            Files will be saved as <code>OriginalName.langCode.srt/ass/ssa</code>
                                        </p>
                                    </div>
                                )}
                            </div>
                        </div>

                        <div className="pt-4 border-t border-gray-100 dark:border-gray-700">
                            <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Post-Processing</h3>
                            <label className="flex items-center gap-3 cursor-pointer group">
                                <div className="relative flex items-center justify-center">
                                    <input
                                        type="checkbox"
                                        checked={settings.apply_subtitle_edit_fixes || false}
                                        onChange={(e) => handleChange('apply_subtitle_edit_fixes', e.target.checked)}
                                        className="sr-only"
                                    />
                                    <div className={`w-5 h-5 rounded border transition-all flex items-center justify-center ${settings.apply_subtitle_edit_fixes ? 'bg-indigo-600 border-indigo-600' : 'border-gray-300 dark:border-gray-600 group-hover:border-indigo-400'}`}>
                                        {settings.apply_subtitle_edit_fixes && <Check size={14} className="text-white" />}
                                    </div>
                                </div>
                                <span className="text-sm text-gray-700 dark:text-gray-300 font-medium">Auto-Apply SubtitleEdit Fixes & Splits</span>
                            </label>
                            <p className="text-[10px] text-gray-400 mt-1 pl-8 italic">
                                Runs "Fix Common Errors" and "Split Long Lines" via SubtitleEdit CLI after translation.
                            </p>
                        </div>

                        <div className="pt-4 border-t border-gray-100 dark:border-gray-700">
                            <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Subtitle Formats</h3>
                            <label className="flex items-center gap-3 cursor-pointer group">
                                <div className="relative flex items-center justify-center">
                                    <input
                                        type="checkbox"
                                        checked={settings.translate_all_source_formats || false}
                                        onChange={(e) => handleChange('translate_all_source_formats', e.target.checked)}
                                        className="sr-only"
                                    />
                                    <div className={`w-5 h-5 rounded border transition-all flex items-center justify-center ${settings.translate_all_source_formats ? 'bg-indigo-600 border-indigo-600' : 'border-gray-300 dark:border-gray-600 group-hover:border-indigo-400'}`}>
                                        {settings.translate_all_source_formats && <Check size={14} className="text-white" />}
                                    </div>
                                </div>
                                <span className="text-sm text-gray-700 dark:text-gray-300 font-medium">Translate all source subtitle formats</span>
                            </label>
                            <p className="text-[10px] text-gray-400 mt-1 pl-8 italic">
                                Translate alternate formats (e.g. SRT, ASS) if present in the source files.
                            </p>
                        </div>

                        <div className="pt-4 border-t border-gray-100 dark:border-gray-700">
                            <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Local LLM (LM Studio)</h3>
                            <div className="space-y-3">
                                <div className="flex gap-2">
                                    <div className="flex-1">
                                        <input
                                            type="text"
                                            value={settings.local_llm_base_url}
                                            onChange={(e) => handleChange('local_llm_base_url', e.target.value)}
                                            placeholder="http://localhost:1234/v1"
                                            className="w-full px-4 py-2 rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                                        />
                                    </div>
                                    <button
                                        onClick={handleTestConnection}
                                        disabled={testStatus === 'testing'}
                                        className={`px-4 py-2 rounded-xl text-sm font-medium transition-colors ${testStatus === 'success' ? 'bg-green-100 text-green-700' :
                                                testStatus === 'error' ? 'bg-red-100 text-red-700' :
                                                    'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-200'
                                            }`}
                                    >
                                        {testStatus === 'testing' ? 'Testing...' :
                                            testStatus === 'success' ? 'Connected!' :
                                                testStatus === 'error' ? 'Failed' : 'Test'}
                                    </button>
                                </div>
                                <p className="text-xs text-gray-500 dark:text-gray-400">
                                    Input the API Base URL from LM Studio's Local Server tab.
                                </p>
                                {testError && (
                                    <div className="flex flex-col gap-2 p-3 rounded-xl bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 text-xs">
                                        <div className="flex items-start gap-2">
                                            <AlertCircle size={14} className="mt-0.5 flex-shrink-0" />
                                            <span className="font-semibold">{testError}</span>
                                        </div>
                                        {diagnostics && diagnostics.tried_urls && (
                                            <div className="mt-2 border-t border-red-100 dark:border-red-900/30 pt-2 space-y-1 overflow-hidden">
                                                <p className="font-bold opacity-70">Tried endpoints:</p>
                                                {diagnostics.tried_urls.map((u, i) => (
                                                    <div key={i} className="font-mono bg-white/50 dark:bg-black/20 p-1.5 rounded border border-red-100/50 dark:border-red-900/20">
                                                        <div className="truncate">{u}</div>
                                                        <div className="text-[10px] opacity-60 mt-1 italic italic">
                                                            {diagnostics.errors?.[i] || "Unknown error"}
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                ) || null}
                            </div>
                        </div>
                    </div>

                    <div className="px-6 pb-4">
                        <label className="block text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1">
                            Additional target languages
                        </label>
                        <input
                            type="text"
                            value={extraLangs}
                            onChange={(e) => setExtraLangs(e.target.value)}
                            placeholder="e.g. Spanish, French — translated in addition to the primary"
                            className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none"
                        />
                        <p className="text-xs text-gray-400 mt-1">
                            Comma-separated. Each produces its own <span className="font-mono">.&lt;code&gt;.srt/ass/ssa</span> next to the media.
                        </p>
                    </div>

                    <div className="p-6 border-t border-gray-200 dark:border-gray-700 flex justify-end gap-3">
                        <button
                            onClick={onClose}
                            className="px-4 py-2 text-gray-600 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
                        >
                            Cancel
                        </button>
                        <button
                            onClick={handleSave}
                            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg transition-colors"
                        >
                            <Save size={18} />
                            Save Settings
                        </button>
                    </div>
                </motion.div>
            </div>
        </AnimatePresence>
    );
};

export default ProjectSettingsModal;
