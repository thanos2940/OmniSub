import React, { useState, useEffect } from 'react';
import { Save, Info, Sparkles, Settings as SettingsIcon, BookOpen, Zap, Globe, Play, RefreshCw } from 'lucide-react';
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

    useEffect(() => {
        loadSettings();
    }, []);

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
                    </div>
                </div>

                <div className="p-6 bg-gray-50/50 dark:bg-gray-800/50 border-t border-gray-200 dark:border-gray-700 flex items-center justify-between">
                    <div className="text-sm">
                        {message && (
                            <span className={`font-medium ${message.type === 'success' ? 'text-green-600' : 'text-red-600'}`}>
                                {message.text}
                            </span>
                        )}
                    </div>
                    <button
                        onClick={handleSave}
                        disabled={saving}
                        className="px-6 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl font-medium shadow-lg shadow-indigo-200 dark:shadow-none transition-all flex items-center gap-2 disabled:opacity-70"
                    >
                        {saving ? 'Saving...' : <><Save className="w-4 h-4" /> Save Changes</>}
                    </button>
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
        </div>
    );
};

export default SettingsPage;
