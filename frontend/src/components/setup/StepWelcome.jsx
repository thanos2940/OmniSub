import { useState, useEffect } from 'react';
import { Sparkles } from 'lucide-react';
import { api } from '../../api';
import LanguageSelect from '../common/LanguageSelect';
import { browserTargetLanguage } from '../../utils/languages';

const StepWelcome = ({ onNext }) => {
    // The subtitles are translated from English, so English is never the default target: the language
    // already saved, else the browser's own language, else nothing until the person picks one.
    const [language, setLanguage] = useState('');
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        let alive = true;
        api.getSettings()
            .then(res => {
                const saved = (res.data?.default_target_language || '').trim();
                if (alive) setLanguage(prev => prev || (saved && saved !== 'English' ? saved : (browserTargetLanguage() || '')));
            })
            .catch(() => { if (alive) setLanguage(prev => prev || browserTargetLanguage() || ''); });
        return () => { alive = false; };
    }, []);

    const handleNext = async () => {
        setSaving(true);
        try {
            await api.updateSettings({ default_target_language: language });
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
                    A context-aware AI subtitle translator for English subtitles. Let’s get your install configured —
                    this takes about two minutes and you can change any of it later in Settings.
                </p>
            </div>

            <div>
                <label htmlFor="wizard-target-language" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Which language should subtitles be translated into?
                </label>
                <LanguageSelect id="wizard-target-language" value={language} onChange={setLanguage} autoFocus />
                <p className="text-xs text-gray-500 mt-1">The default for new projects — each project can still override it.</p>
            </div>

            <button
                onClick={handleNext}
                disabled={saving || !language}
                className="w-full px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-medium transition-colors disabled:opacity-50"
            >
                {saving ? 'Saving...' : "Let's go"}
            </button>
        </div>
    );
};

export default StepWelcome;
