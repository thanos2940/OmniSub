import React, { useState } from 'react';
import { Sparkles, CheckCircle2, XCircle, Loader2, Key } from 'lucide-react';
import { api } from '../../api';

const StepProvider = ({ onNext, onBack, onSkip }) => {
    const [geminiKey, setGeminiKey] = useState('');
    const [geminiStatus, setGeminiStatus] = useState(null); // null | 'testing' | 'ok' | 'error'
    const [statusMessage, setStatusMessage] = useState('');
    const [saving, setSaving] = useState(false);

    const testGemini = async () => {
        if (!geminiKey.trim()) return;
        setGeminiStatus('testing');
        setStatusMessage('');
        try {
            await api.setApiKey(geminiKey.trim());
            const res = await api.testGeminiKey(geminiKey.trim());
            if (res.data?.valid) {
                setGeminiStatus('ok');
                setStatusMessage('Google Gemini API Key verified!');
            } else {
                setGeminiStatus('error');
                setStatusMessage(res.data?.error || 'Invalid API Key');
            }
        } catch (err) {
            setGeminiStatus('error');
            setStatusMessage(err.response?.data?.detail || err.message || 'Verification failed');
        }
    };

    const handleNext = async () => {
        setSaving(true);
        try {
            if (geminiKey.trim()) {
                await api.setApiKey(geminiKey.trim());
            }
            await api.updateSettings({ ai_provider: 'cloud' });
        } catch (e) {
            console.error('Failed to save provider settings', e);
        } finally {
            setSaving(false);
            onNext();
        }
    };

    const StatusIcon = ({ status }) => {
        if (status === 'testing') return <Loader2 size={16} className="animate-spin text-indigo-500" />;
        if (status === 'ok') return <CheckCircle2 size={16} className="text-emerald-500" />;
        if (status === 'error') return <XCircle size={16} className="text-red-500" />;
        return null;
    };

    return (
        <div className="space-y-6">
            <div>
                <div className="flex items-center gap-2 mb-1">
                    <Sparkles className="text-indigo-600 dark:text-indigo-400" size={22} />
                    <h2 className="text-xl font-bold text-gray-900 dark:text-white">AI Engine Configuration</h2>
                </div>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                    OmbiSub is powered directly by Google Gemini 2.5 for state-of-the-art multi-lingual translation, character tracking, and quality control.
                </p>
            </div>

            <div className="bg-indigo-50/60 dark:bg-indigo-950/30 border border-indigo-100 dark:border-indigo-900/50 rounded-2xl p-5 space-y-4">
                <div className="flex items-center gap-2 text-indigo-900 dark:text-indigo-300 font-semibold text-sm">
                    <Key size={16} className="text-indigo-600" />
                    <span>Google Gemini API Key</span>
                </div>

                <div className="flex gap-2">
                    <input
                        type="password"
                        value={geminiKey}
                        onChange={(e) => { setGeminiKey(e.target.value); setGeminiStatus(null); }}
                        placeholder="AIzaSy..."
                        className="flex-1 px-3.5 py-2.5 rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-800 dark:text-white outline-none text-sm font-mono focus:ring-2 focus:ring-indigo-500"
                    />
                    <button
                        onClick={testGemini}
                        disabled={!geminiKey.trim() || geminiStatus === 'testing'}
                        className="px-4 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold flex items-center gap-2 disabled:opacity-50 transition-colors shadow-sm"
                    >
                        <StatusIcon status={geminiStatus} />
                        {geminiStatus === 'testing' ? 'Testing...' : 'Test & Save'}
                    </button>
                </div>

                {geminiStatus === 'error' && (
                    <p className="text-xs text-red-500 flex items-center gap-1 font-medium">
                        <XCircle size={14} /> {statusMessage || "Couldn't verify that API key."}
                    </p>
                )}
                {geminiStatus === 'ok' && (
                    <p className="text-xs text-emerald-600 dark:text-emerald-400 flex items-center gap-1 font-medium">
                        <CheckCircle2 size={14} /> {statusMessage || "Key saved and ready."}
                    </p>
                )}

                <div className="pt-2 text-xs text-gray-500 dark:text-gray-400">
                    Don't have a key?{' '}
                    <a
                        href="https://aistudio.google.com/app/apikey"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-indigo-600 dark:text-indigo-400 hover:underline font-semibold"
                    >
                        Get a free Google Gemini API key from Google AI Studio &rarr;
                    </a>
                </div>
            </div>

            <div className="flex items-center justify-between pt-2">
                <button
                    onClick={onBack}
                    disabled={!onBack}
                    className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 disabled:opacity-0"
                >
                    Back
                </button>
                <div className="flex gap-3">
                    <button
                        onClick={onSkip}
                        className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                    >
                        Configure Later
                    </button>
                    <button
                        onClick={handleNext}
                        disabled={saving}
                        className="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-sm font-semibold disabled:opacity-50 shadow-md transition-all"
                    >
                        {saving ? 'Saving...' : 'Continue'}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default StepProvider;
