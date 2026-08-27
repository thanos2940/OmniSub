import React, { useState } from 'react';
import { Sparkles, Globe } from 'lucide-react';
import { api } from '../../api';

const StepWelcome = ({ onNext }) => {
    const [language, setLanguage] = useState('English');
    const [saving, setSaving] = useState(false);

    const handleNext = async () => {
        setSaving(true);
        try {
            await api.updateSettings({ default_target_language: language.trim() || 'English' });
        } catch (e) {
            console.error('Failed to save target language', e);
        } finally {
            setSaving(false);
            onNext();
        }
    };

    return (
        <div className="space-y-6">
            <div className="text-center space-y-2">
                <div className="mx-auto w-14 h-14 rounded-2xl bg-indigo-600 flex items-center justify-center shadow-lg shadow-indigo-200 dark:shadow-none">
                    <Sparkles className="text-white" size={28} />
                </div>
                <h2 className="text-xl font-bold text-gray-900 dark:text-white">Welcome to Omnisub</h2>
                <p className="text-sm text-gray-500 dark:text-gray-400 max-w-md mx-auto">
                    A context-aware AI subtitle translator. Let's get your install configured —
                    this takes about two minutes and you can change any of it later in Settings.
                </p>
            </div>

            <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    What language do you translate into, most of the time?
                </label>
                <div className="relative">
                    <Globe className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 w-4 h-4" />
                    <input
                        type="text"
                        value={language}
                        onChange={(e) => setLanguage(e.target.value)}
                        placeholder="e.g. Greek, Spanish, German"
                        className="w-full pl-10 pr-4 py-2.5 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white focus:ring-2 focus:ring-indigo-500 outline-none text-sm"
                        autoFocus
                    />
                </div>
                <p className="text-xs text-gray-500 mt-1">Used as the default for new projects — each project can still override it.</p>
            </div>

            <button
                onClick={handleNext}
                disabled={saving}
                className="w-full px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-medium transition-colors disabled:opacity-50"
            >
                {saving ? 'Saving...' : "Let's go"}
            </button>
        </div>
    );
};

export default StepWelcome;
