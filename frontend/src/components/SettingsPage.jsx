import React, { useState, useEffect } from 'react';
import { Save, Info, Settings as SettingsIcon, BookOpen, Zap, Globe } from 'lucide-react';
import { api } from '../api';
import ModelCombobox from './ModelCombobox';

const SettingsPage = () => {
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

            {/* Instructions */}
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
