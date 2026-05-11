import React, { useState, useEffect } from 'react';
import { Save, Info, Sparkles, Settings as SettingsIcon, BookOpen, Zap, Globe, Play, RefreshCw, Database, ShieldAlert, List, Link as LinkIcon, Activity, Plus, Trash2 } from 'lucide-react';
import { api } from '../api';
import ModelCombobox from './ModelCombobox';

const SettingsPage = () => {
    const [testLines, setTestLines] = useState([
        "What does it matter? It's just a curse.",
        "You don't understand the weight of this identity.",
        "The cherry blossoms are falling early this year.",
        "Wait! Don't go into the forbidden forest alone!"
    ]);
    const [testModel, setTestModel] = useState('');
    const [isTesting, setIsTesting] = useState(false);
    const [testResult, setTestResult] = useState(null);

    const handleRunTest = async () => {
        setIsTesting(true);
        try {
            // We use the first project found as context for the test
            const projectsRes = await api.getProjects();
            const projectName = projectsRes.data[0]?.show_name;
            if (!projectName) {
                alert("Please create at least one project first to provide glossary/language context.");
                return;
            }

            const res = await api.testTranslation(projectName, {
                lines: testLines,
                temperature: settings.temperature,
                top_k: settings.top_k,
                top_p: settings.top_p,
                model_name: testModel || undefined
            });
            setTestResult(res.data);
        } catch (err) {
            console.error("Test failed", err);
        } finally {
            setIsTesting(false);
        }
    };
    const [settings, setSettings] = useState({
        default_target_language: 'English',
        default_scan_model: 'gemini-2.5-flash',
        default_translation_model: 'gemini-2.5-flash'
    });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [message, setMessage] = useState(null);
    const [bazarrStatus, setBazarrStatus] = useState(null);
    const [testingBazarr, setTestingBazarr] = useState(false);
    const [scanningBazarr, setScanningBazarr] = useState(false);

    useEffect(() => {
        loadSettings();
        loadBazarrStatus();
    }, []);

    const loadBazarrStatus = async () => {
        try {
            const res = await api.getBazarrStatus();
            setBazarrStatus(res.data);
        } catch (e) {
            console.error("Failed to load Bazarr status", e);
        }
    };

    const handleTestBazarr = async () => {
        setTestingBazarr(true);
        try {
            const res = await api.testBazarr({
                bazarr_url: settings.bazarr_url,
                bazarr_api_key: settings.bazarr_api_key
            });
            if (res.data.connected) {
                setMessage({ type: 'success', text: `Bazarr connection successful! Version: ${res.data.version}` });
            } else {
                setMessage({ type: 'error', text: `Connection failed: ${res.data.error}` });
            }
        } catch (err) {
            setMessage({ type: 'error', text: 'Failed to test Bazarr connection.' });
        } finally {
            setTestingBazarr(false);
            setTimeout(() => setMessage(null), 5000);
        }
    };

    const handleScanBazarr = async () => {
        setScanningBazarr(true);
        try {
            await api.scanNow(settings);
            setMessage({ type: 'success', text: 'Bazarr scan triggered successfully!' });
            loadBazarrStatus();
        } catch (err) {
            setMessage({ type: 'error', text: 'Failed to trigger Bazarr scan.' });
        } finally {
            setScanningBazarr(false);
            setTimeout(() => setMessage(null), 5000);
        }
    };

    const loadSettings = async () => {
        try {
            const response = await api.getSettings();
            setSettings(response.data);
        } catch (error) {
            console.error('Failed to load settings:', error);
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        setMessage(null);
        try {
            await api.updateSettings(settings);
            setMessage({ type: 'success', text: 'Settings saved successfully!' });
            setTimeout(() => setMessage(null), 3000);
        } catch (error) {
            console.error('Failed to save settings:', error);
            setMessage({ type: 'error', text: 'Failed to save settings.' });
        } finally {
            setSaving(false);
        }
    };

    const handleChange = (field, value) => {
        setSettings(prev => ({ ...prev, [field]: value }));
    };

    const handleAddMapping = () => {
        const mappings = [...(settings.bazarr_path_mappings || []), { remote: '', local: '' }];
        handleChange('bazarr_path_mappings', mappings);
    };

    const handleRemoveMapping = (index) => {
        const mappings = [...(settings.bazarr_path_mappings || [])];
        mappings.splice(index, 1);
        handleChange('bazarr_path_mappings', mappings);
    };

    const handleUpdateMapping = (index, field, value) => {
        const mappings = [...(settings.bazarr_path_mappings || [])];
        mappings[index] = { ...mappings[index], [field]: value };
        handleChange('bazarr_path_mappings', mappings);
    };

    if (loading) {
        return <div className="p-8 text-center text-gray-500">Loading settings...</div>;
    }

    return (
        <div className="max-w-4xl mx-auto p-8 space-y-8">
            <header className="flex items-center gap-3 mb-8">
                <div className="w-10 h-10 bg-indigo-600 rounded-xl flex items-center justify-center shadow-lg shadow-indigo-200 dark:shadow-none">
                    <SettingsIcon className="text-white" size={24} />
                </div>
                <div>
                    <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Global Settings</h1>
                    <p className="text-gray-500 dark:text-gray-400">Configure default behavior for all projects.</p>
                </div>
            </header>

            {/* Settings Form */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                        <Zap className="w-5 h-5 text-indigo-500" />
                        Defaults
                    </h2>
                </div>

                <div className="p-6 space-y-6">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                Default Target Language
                            </label>
                            <div className="relative">
                                <Globe className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 w-4 h-4" />
                                <input
                                    type="text"
                                    value={settings.default_target_language}
                                    onChange={(e) => handleChange('default_target_language', e.target.value)}
                                    className="w-full pl-10 pr-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                    placeholder="e.g. Greek, Spanish"
                                />
                            </div>
                            <p className="text-xs text-gray-500 mt-1">Used when creating new projects.</p>
                        </div>

                        <ModelCombobox
                            label="Default Translation Model"
                            value={settings.default_translation_model}
                            onChange={(v) => handleChange('default_translation_model', v)}
                        />
                        
                        <ModelCombobox
                            label="Default Context Model"
                            value={settings.default_context_model}
                            onChange={(v) => handleChange('default_context_model', v)}
                        />

                        <ModelCombobox
                            label="Default Glossary Model"
                            value={settings.default_glossary_model}
                            onChange={(v) => handleChange('default_glossary_model', v)}
                        />

                        <ModelCombobox
                            label="Default Scan Model"
                            value={settings.default_scan_model}
                            onChange={(v) => handleChange('default_scan_model', v)}
                        />

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                Concurrent Scenes (Parallel Limit)
                            </label>
                            <input
                                type="number"
                                min="1"
                                max="10"
                                value={settings.concurrent_scenes || 3}
                                onChange={(e) => handleChange('concurrent_scenes', parseInt(e.target.value, 10))}
                                className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                            />
                            <p className="text-xs text-gray-500 mt-1">Lower this if using local LLMs to prevent GPU/RAM crashes (Default: 3).</p>
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                Max Lines per Scene (Chunk Size)
                            </label>
                            <input
                                type="number"
                                min="10"
                                max="1000"
                                value={settings.max_lines_per_scene || 200}
                                onChange={(e) => handleChange('max_lines_per_scene', parseInt(e.target.value, 10))}
                                className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                            />
                            <p className="text-xs text-gray-500 mt-1">Split large scenes if they exceed this limit. 200 is recommended for most models.</p>
                        </div>

                        <div className="md:col-span-2 space-y-4 pt-4 border-t border-gray-100 dark:border-gray-700">
                            <h3 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">Post-Processing (SubtitleEdit)</h3>
                            
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                        SubtitleEdit Path
                                    </label>
                                    <input
                                        type="text"
                                        value={settings.subtitle_edit_path || ''}
                                        onChange={(e) => handleChange('subtitle_edit_path', e.target.value)}
                                        className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                        placeholder="P:\SubtitleEdit\SubtitleEdit.exe"
                                    />
                                    <p className="text-xs text-gray-500 mt-1">Path to the SubtitleEdit.exe for automated fixes.</p>
                                </div>

                                <div className="flex items-center gap-3 pt-6">
                                    <label className="relative inline-flex items-center cursor-pointer">
                                        <input 
                                            type="checkbox" 
                                            className="sr-only peer"
                                            checked={settings.apply_subtitle_edit_fixes || false}
                                            onChange={(e) => handleChange('apply_subtitle_edit_fixes', e.target.checked)}
                                        />
                                        <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-indigo-300 dark:peer-focus:ring-indigo-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-indigo-600"></div>
                                        <span className="ml-3 text-sm font-medium text-gray-900 dark:text-gray-300">Auto-Apply Fixes & Split Long Lines</span>
                                    </label>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Advanced Model Config */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                        <Sparkles className="w-5 h-5 text-amber-500" />
                        Advanced Model Config
                    </h2>
                </div>
                <div className="p-6 space-y-6">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                        {/* Temperature */}
                        <div className="space-y-4">
                            <div className="flex justify-between items-center">
                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Temperature</label>
                                <span className="inline-block px-2 py-0.5 rounded-lg bg-indigo-100 text-indigo-700 text-xs font-bold font-mono">
                                    {(settings.temperature ?? 0.3).toFixed(2)}
                                </span>
                            </div>
                            <input
                                type="range"
                                min="0"
                                max="2"
                                step="0.05"
                                value={settings.temperature ?? 0.3}
                                onChange={(e) => handleChange('temperature', parseFloat(e.target.value))}
                                className="w-full h-1.5 bg-gray-200 dark:bg-gray-700 rounded-lg appearance-none cursor-pointer accent-indigo-600"
                            />
                            <p className="text-xs text-gray-500">
                                Controls creativity. **0.0 - 0.3** is best for consistent translations. **0.5+** adds variety but might ignore glossary terms.
                            </p>
                        </div>

                        {/* Top-P */}
                        <div className="space-y-4">
                            <div className="flex justify-between items-center">
                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Top-P (Nucleus Sampling)</label>
                                <span className="inline-block px-2 py-0.5 rounded-lg bg-indigo-100 text-indigo-700 text-xs font-bold font-mono">
                                    {(settings.top_p ?? 1.0).toFixed(2)}
                                </span>
                            </div>
                            <input
                                type="range"
                                min="0.1"
                                max="1"
                                step="0.01"
                                value={settings.top_p ?? 1.0}
                                onChange={(e) => handleChange('top_p', parseFloat(e.target.value))}
                                className="w-full h-1.5 bg-gray-200 dark:bg-gray-700 rounded-lg appearance-none cursor-pointer accent-indigo-600"
                            />
                            <p className="text-xs text-gray-500">
                                Limits vocabulary to the most likely tokens. **0.9** is a safe choice for local models. **1.0** uses everything.
                            </p>
                        </div>

                        {/* Top-K */}
                        <div className="space-y-4">
                            <div className="flex justify-between items-center">
                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Top-K</label>
                                <span className="inline-block px-2 py-0.5 rounded-lg bg-indigo-100 text-indigo-700 text-xs font-bold font-mono">
                                    {settings.top_k ?? 40}
                                </span>
                            </div>
                            <input
                                type="range"
                                min="1"
                                max="100"
                                step="1"
                                value={settings.top_k ?? 40}
                                onChange={(e) => handleChange('top_k', parseInt(e.target.value, 10))}
                                className="w-full h-1.5 bg-gray-200 dark:bg-gray-700 rounded-lg appearance-none cursor-pointer accent-indigo-600"
                            />
                            <p className="text-xs text-gray-500">
                                Filters the top K tokens. **40** is standard. Lowering this makes the model more predictable.
                            </p>
                        </div>

                         <div className="bg-amber-50 dark:bg-amber-900/10 p-4 rounded-xl border border-amber-100 dark:border-amber-900/30">
                            <div className="flex gap-3">
                                <Info className="w-5 h-5 text-amber-500 flex-shrink-0" />
                                <div className="space-y-1">
                                    <p className="text-xs font-bold text-amber-800 dark:text-amber-400 uppercase tracking-wider">Expert Mode Tip</p>
                                    <p className="text-xs text-amber-700 dark:text-amber-300 leading-relaxed">
                                        For Local Models (Gemma), a temperature of **0.5**, Top-P of **0.9**, and Top-K of **64** are usually optimal for the "Thinking" stage.
                                    </p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Advanced AI Features */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                        <Database className="w-5 h-5 text-indigo-500" />
                        Advanced AI Features
                    </h2>
                </div>
                <div className="p-6 space-y-8">
                    {/* Translation Memory */}
                    <div className="space-y-4">
                        <div className="flex items-center justify-between">
                            <h3 className="text-md font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                <BookOpen className="w-4 h-4 text-indigo-400" /> Translation Memory (TM)
                            </h3>
                            <label className="relative inline-flex items-center cursor-pointer">
                                <input 
                                    type="checkbox" 
                                    className="sr-only peer"
                                    checked={settings.tm_enabled ?? true}
                                    onChange={(e) => handleChange('tm_enabled', e.target.checked)}
                                />
                                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-indigo-300 dark:peer-focus:ring-indigo-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-indigo-600"></div>
                            </label>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pl-6 border-l-2 border-indigo-100 dark:border-indigo-900/30">
                            <div className="space-y-2">
                                <div className="flex justify-between">
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Fuzzy Match Threshold</label>
                                    <span className="text-xs font-mono text-indigo-600">{(settings.tm_similarity_threshold ?? 0.80).toFixed(2)}</span>
                                </div>
                                <input type="range" min="0.5" max="0.99" step="0.01" value={settings.tm_similarity_threshold ?? 0.80} onChange={(e) => handleChange('tm_similarity_threshold', parseFloat(e.target.value))} className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-indigo-600" />
                                <p className="text-xs text-gray-500">Minimum similarity to use a TM entry as context.</p>
                            </div>
                            <div className="space-y-2">
                                <div className="flex justify-between">
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Exact Match Threshold</label>
                                    <span className="text-xs font-mono text-indigo-600">{(settings.tm_exact_match_threshold ?? 0.95).toFixed(2)}</span>
                                </div>
                                <input type="range" min="0.8" max="1.0" step="0.01" value={settings.tm_exact_match_threshold ?? 0.95} onChange={(e) => handleChange('tm_exact_match_threshold', parseFloat(e.target.value))} className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-indigo-600" />
                                <p className="text-xs text-gray-500">Threshold to auto-replace without translating.</p>
                            </div>
                        </div>
                    </div>

                    {/* AI Reviewer */}
                    <div className="space-y-4 pt-4 border-t border-gray-100 dark:border-gray-700">
                        <div className="flex items-center justify-between">
                            <h3 className="text-md font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                <ShieldAlert className="w-4 h-4 text-rose-400" /> AI Reviewer
                            </h3>
                            <label className="relative inline-flex items-center cursor-pointer">
                                <input
                                    type="checkbox"
                                    className="sr-only peer"
                                    checked={settings.enable_reviewer ?? false}
                                    onChange={(e) => handleChange('enable_reviewer', e.target.checked)}
                                />
                                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-rose-300 dark:peer-focus:ring-rose-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-rose-600"></div>
                            </label>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pl-6 border-l-2 border-rose-100 dark:border-rose-900/30">
                            <ModelCombobox label="Reviewer Model" value={settings.review_model} onChange={(v) => handleChange('review_model', v)} />
                            <div className="space-y-2">
                                <div className="flex justify-between">
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Max Review %</label>
                                    <span className="text-xs font-mono text-rose-600">{Math.round((settings.review_max_pct ?? 0.25) * 100)}%</span>
                                </div>
                                <input type="range" min="0.05" max="1.0" step="0.05" value={settings.review_max_pct ?? 0.25} onChange={(e) => handleChange('review_max_pct', parseFloat(e.target.value))} className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-rose-600" />
                                <p className="text-xs text-gray-500">Max % of lines to flag per episode.</p>
                            </div>
                        </div>
                    </div>

                    {/* Episode Summaries */}
                    <div className="space-y-4 pt-4 border-t border-gray-100 dark:border-gray-700">
                        <div className="flex items-center justify-between">
                            <h3 className="text-md font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                <List className="w-4 h-4 text-emerald-400" /> Episode Summaries
                            </h3>
                            <label className="relative inline-flex items-center cursor-pointer">
                                <input 
                                    type="checkbox" 
                                    className="sr-only peer"
                                    checked={settings.episode_summaries_enabled ?? true}
                                    onChange={(e) => handleChange('episode_summaries_enabled', e.target.checked)}
                                />
                                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-emerald-300 dark:peer-focus:ring-emerald-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-emerald-600"></div>
                            </label>
                        </div>
                        <div className="pl-6 border-l-2 border-emerald-100 dark:border-emerald-900/30">
                            <div className="w-1/2 space-y-2">
                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Summary Context Window</label>
                                <input type="number" min="1" max="10" value={settings.episode_summary_window ?? 3} onChange={(e) => handleChange('episode_summary_window', parseInt(e.target.value, 10))} className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-emerald-500 outline-none transition-all" />
                                <p className="text-xs text-gray-500">Number of previous episode summaries to include as context.</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Bazarr Integration */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 flex justify-between items-center">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                        <LinkIcon className="w-5 h-5 text-blue-500" />
                        Bazarr Integration
                    </h2>
                    <label className="relative inline-flex items-center cursor-pointer">
                        <input 
                            type="checkbox" 
                            className="sr-only peer"
                            checked={settings.bazarr_enabled ?? false}
                            onChange={(e) => handleChange('bazarr_enabled', e.target.checked)}
                        />
                        <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                    </label>
                </div>
                <div className="p-6 space-y-6">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Bazarr URL</label>
                            <input
                                type="text"
                                value={settings.bazarr_url ?? "http://localhost:6767"}
                                onChange={(e) => handleChange('bazarr_url', e.target.value)}
                                className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-blue-500 outline-none transition-all"
                                placeholder="http://localhost:6767"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">API Key</label>
                            <input
                                type="password"
                                value={settings.bazarr_api_key ?? ""}
                                onChange={(e) => handleChange('bazarr_api_key', e.target.value)}
                                className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-blue-500 outline-none transition-all"
                                placeholder="Enter Bazarr API Key"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Poll Interval (Minutes)</label>
                            <input
                                type="number"
                                min="5" max="120"
                                value={settings.bazarr_poll_interval ?? 30}
                                onChange={(e) => handleChange('bazarr_poll_interval', parseInt(e.target.value, 10))}
                                className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-blue-500 outline-none transition-all"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Media Types to Sync</label>
                            <select
                                value={settings.bazarr_media_types ?? "both"}
                                onChange={(e) => handleChange('bazarr_media_types', e.target.value)}
                                className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-blue-500 outline-none transition-all"
                            >
                                <option value="series">Series Only</option>
                                <option value="movies">Movies Only</option>
                                <option value="both">Both</option>
                            </select>
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Auto-Sync Interval (Minutes)</label>
                            <input
                                type="number"
                                min="0" max="1440"
                                value={settings.bazarr_sync_interval ?? 0}
                                onChange={(e) => handleChange('bazarr_sync_interval', parseInt(e.target.value, 10))}
                                className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-blue-500 outline-none transition-all"
                            />
                            <p className="text-xs text-gray-500 mt-1">0 = manual only. How often to auto-sync the Bazarr library.</p>
                        </div>
                    </div>
                    
                    {/* Path Mappings */}
                    <div className="pt-4 border-t border-gray-100 dark:border-gray-700">
                        <div className="flex items-center justify-between mb-4">
                            <div>
                                <h3 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">Path Mappings</h3>
                                <p className="text-xs text-gray-500">Map Bazarr's server paths to your local network paths.</p>
                            </div>
                            <button
                                onClick={handleAddMapping}
                                className="flex items-center gap-1 text-xs font-medium text-indigo-600 hover:text-indigo-700 transition-colors"
                            >
                                <Plus size={14} /> Add Mapping
                            </button>
                        </div>
                        
                        <div className="space-y-3">
                            {(settings.bazarr_path_mappings || []).map((mapping, index) => (
                                <div key={index} className="flex gap-3 items-start animate-in fade-in slide-in-from-top-2 duration-200">
                                    <div className="flex-1">
                                        <input
                                            type="text"
                                            value={mapping.remote}
                                            onChange={(e) => handleUpdateMapping(index, 'remote', e.target.value)}
                                            className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                            placeholder="Remote Path (e.g. E:\Shows)"
                                        />
                                    </div>
                                    <div className="flex-shrink-0 pt-2 text-gray-400">→</div>
                                    <div className="flex-1">
                                        <input
                                            type="text"
                                            value={mapping.local}
                                            onChange={(e) => handleUpdateMapping(index, 'local', e.target.value)}
                                            className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                            placeholder="Local Path (e.g. \\Server\e\Shows)"
                                        />
                                    </div>
                                    <button
                                        onClick={() => handleRemoveMapping(index)}
                                        className="p-2 text-gray-400 hover:text-rose-500 transition-colors"
                                    >
                                        <Trash2 size={16} />
                                    </button>
                                </div>
                            ))}
                            
                            {(settings.bazarr_path_mappings || []).length === 0 && (
                                <div className="text-center py-4 bg-gray-50 dark:bg-gray-800/50 rounded-xl border border-dashed border-gray-200 dark:border-gray-700">
                                    <p className="text-xs text-gray-400 font-medium italic">No path mappings configured.</p>
                                </div>
                            )}
                        </div>
                    </div>
                    
                    <div className="flex items-center gap-4 pt-4 border-t border-gray-100 dark:border-gray-700">
                        <button
                            onClick={handleTestBazarr}
                            disabled={testingBazarr}
                            className="px-4 py-2 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded-lg font-medium transition-all text-sm flex items-center gap-2"
                        >
                            {testingBazarr ? 'Testing...' : <><Activity className="w-4 h-4" /> Test Connection</>}
                        </button>
                        <button
                            onClick={handleScanBazarr}
                            disabled={scanningBazarr || !settings.bazarr_enabled}
                            className="px-4 py-2 bg-blue-50 hover:bg-blue-100 text-blue-700 rounded-lg font-medium transition-all text-sm flex items-center gap-2 disabled:opacity-50"
                        >
                            {scanningBazarr ? 'Scanning...' : <><RefreshCw className="w-4 h-4" /> Scan Now</>}
                        </button>
                        
                    </div>

                    {bazarrStatus && settings.bazarr_enabled && (
                        <div className="mt-6 bg-blue-50/50 dark:bg-blue-900/10 rounded-xl p-4 border border-blue-100 dark:border-blue-900/30 text-sm">
                            <h4 className="font-semibold text-blue-900 dark:text-blue-300 mb-2">Integration Status</h4>
                            <div className="grid grid-cols-3 gap-4 text-blue-800 dark:text-blue-400">
                                <div><span className="font-medium">Total Translated:</span> {bazarrStatus.translated_count || 0}</div>
                                <div><span className="font-medium">Last Scan:</span> {bazarrStatus.last_scan_time ? new Date(bazarrStatus.last_scan_time).toLocaleString() : 'Never'}</div>
                                <div><span className="font-medium">Current Item:</span> {bazarrStatus.current_item || 'Idle'}</div>
                            </div>
                        </div>
                    )}
                </div>
            </div>

            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                        <BookOpen className="w-5 h-5 text-emerald-500" />
                        Workflow Instructions
                    </h2>
                </div>
                <div className="p-6 space-y-4 text-gray-600 dark:text-gray-300">
                    <div className="flex gap-4">
                        <div className="w-8 h-8 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center font-bold flex-shrink-0">1</div>
                        <div>
                            <h3 className="font-semibold text-gray-900 dark:text-white">Create Project</h3>
                            <p className="text-sm">Start by creating a project for your show. Set the target language correctly.</p>
                        </div>
                    </div>

                    <div className="flex gap-4">
                        <div className="w-8 h-8 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center font-bold flex-shrink-0">2</div>
                        <div>
                            <h3 className="font-semibold text-gray-900 dark:text-white">Build Glossary</h3>
                            <p className="text-sm">Upload episodes/files or use "Research Mode" to extract character names and terms. Review and approve them.</p>
                        </div>
                    </div>

                    <div className="flex gap-4">
                        <div className="w-8 h-8 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center font-bold flex-shrink-0">3</div>
                        <div>
                            <h3 className="font-semibold text-gray-900 dark:text-white">Translate</h3>
                            <p className="text-sm">Run translation on episodes/files. The AI will use your glossary and context guide to ensure consistency.</p>
                        </div>
                    </div>

                    <div className="flex gap-4">
                        <div className="w-8 h-8 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center font-bold flex-shrink-0">4</div>
                        <div>
                            <h3 className="font-semibold text-gray-900 dark:text-white">Review & Export</h3>
                            <p className="text-sm">Use the editor to tweak translations if needed, then export the final SRT file.</p>
                        </div>
                    </div>
                </div>
            </div>

            {/* Sticky Save Bar */}
            <div className="sticky bottom-8 z-10">
                <div className="bg-white/80 dark:bg-gray-800/80 backdrop-blur-md rounded-2xl shadow-2xl border border-gray-200 dark:border-gray-700 p-4 flex items-center justify-between gap-4">
                    <div className="flex-1">
                        {message && (
                            <div className={`px-4 py-2 rounded-xl text-sm font-medium animate-in fade-in slide-in-from-bottom-2 duration-300 ${
                                message.type === 'success' 
                                    ? 'bg-green-50 text-green-700 border border-green-100 dark:bg-green-900/20 dark:text-green-400 dark:border-green-900/30' 
                                    : 'bg-red-50 text-red-700 border border-red-100 dark:bg-red-900/20 dark:text-red-400 dark:border-red-900/30'
                            }`}>
                                {message.text}
                            </div>
                        )}
                    </div>
                    <div className="flex items-center gap-3">
                        <button
                            onClick={loadSettings}
                            className="px-6 py-2.5 text-gray-700 dark:text-gray-300 font-medium hover:bg-gray-100 dark:hover:bg-gray-700 rounded-xl transition-all"
                        >
                            Reset
                        </button>
                        <button
                            onClick={handleSave}
                            disabled={saving}
                            className="px-8 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl font-semibold shadow-lg shadow-indigo-200 dark:shadow-none transition-all flex items-center gap-2 disabled:opacity-70 scale-100 active:scale-95"
                        >
                            {saving ? 'Saving...' : <><Save className="w-5 h-5" /> Save All Changes</>}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default SettingsPage;
