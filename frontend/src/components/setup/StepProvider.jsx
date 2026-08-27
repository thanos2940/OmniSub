import React, { useState } from 'react';
import { Cloud, Server, Shuffle, CheckCircle2, XCircle, Loader2 } from 'lucide-react';
import { api } from '../../api';

const PROVIDERS = [
    { key: 'cloud', label: 'Cloud', icon: Cloud, desc: 'Google Gemini. Best quality, needs an API key.' },
    { key: 'local', label: 'Local', icon: Server, desc: 'Your own OpenAI-compatible server (llama.cpp, LM Studio, Ollama).' },
    { key: 'hybrid', label: 'Hybrid', icon: Shuffle, desc: 'Cloud for translation, local for cheaper support tasks.' },
];

const StepProvider = ({ onNext, onBack, onSkip }) => {
    const [provider, setProvider] = useState('cloud');

    const [geminiKey, setGeminiKey] = useState('');
    const [geminiStatus, setGeminiStatus] = useState(null); // null | 'testing' | 'ok' | 'error'

    const [localUrl, setLocalUrl] = useState('http://localhost:11434');
    const [localStatus, setLocalStatus] = useState(null); // null | 'testing' | 'ok' | 'error'
    const [localModels, setLocalModels] = useState([]);
    const [selectedLocalModel, setSelectedLocalModel] = useState('');

    const [saving, setSaving] = useState(false);

    const testCloud = async () => {
        if (!geminiKey.trim()) return;
        setGeminiStatus('testing');
        try {
            await api.setApiKey(geminiKey.trim());
            const status = await api.getApiKeyStatus();
            setGeminiStatus(status.data?.has_key ? 'ok' : 'error');
        } catch {
            setGeminiStatus('error');
        }
    };

    const testLocal = async () => {
        setLocalStatus('testing');
        try {
            const res = await api.fetchAllModels(localUrl.trim());
            const models = res.data?.local || [];
            if (res.data?.local_online && models.length) {
                setLocalModels(models);
                setSelectedLocalModel(models[0].value);
                setLocalStatus('ok');
            } else {
                setLocalModels([]);
                setLocalStatus('error');
            }
        } catch {
            setLocalStatus('error');
        }
    };

    const handleNext = async () => {
        setSaving(true);
        try {
            const payload = { ai_provider: provider };
            if (provider !== 'local') {
                // nothing extra — Gemini key is already saved via testCloud
            }
            if (provider !== 'cloud' && localUrl.trim()) {
                payload.local_llm_base_url = localUrl.trim();
                if (selectedLocalModel) {
                    payload.local_translation_model = selectedLocalModel;
                }
            }
            await api.updateSettings(payload);
        } catch (e) {
            console.error('Failed to save provider settings', e);
        } finally {
            setSaving(false);
            onNext();
        }
    };

    const StatusIcon = ({ status }) => {
        if (status === 'testing') return <Loader2 size={16} className="animate-spin text-gray-400" />;
        if (status === 'ok') return <CheckCircle2 size={16} className="text-emerald-500" />;
        if (status === 'error') return <XCircle size={16} className="text-red-500" />;
        return null;
    };

    return (
        <div className="space-y-5">
            <div>
                <h2 className="text-lg font-bold text-gray-900 dark:text-white">AI provider</h2>
                <p className="text-sm text-gray-500 dark:text-gray-400">Where translation requests are sent. You can mix and match per project later.</p>
            </div>

            <div className="grid grid-cols-3 gap-2">
                {PROVIDERS.map(p => (
                    <button
                        key={p.key}
                        onClick={() => setProvider(p.key)}
                        className={`p-3 rounded-xl border text-left transition-all ${provider === p.key
                            ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-900/20 ring-1 ring-indigo-500'
                            : 'border-gray-200 dark:border-gray-600 hover:border-gray-300'}`}
                    >
                        <p.icon size={18} className={provider === p.key ? 'text-indigo-600' : 'text-gray-400'} />
                        <p className="text-sm font-semibold mt-1 text-gray-900 dark:text-white">{p.label}</p>
                        <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-0.5 leading-tight">{p.desc}</p>
                    </button>
                ))}
            </div>

            {(provider === 'cloud' || provider === 'hybrid') && (
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Google Gemini API key</label>
                    <div className="flex gap-2">
                        <input
                            type="password"
                            value={geminiKey}
                            onChange={(e) => { setGeminiKey(e.target.value); setGeminiStatus(null); }}
                            placeholder="AIza..."
                            className="flex-1 px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none text-sm font-mono"
                        />
                        <button onClick={testCloud} disabled={!geminiKey.trim() || geminiStatus === 'testing'} className="px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-700 text-sm font-medium flex items-center gap-1.5 disabled:opacity-50">
                            <StatusIcon status={geminiStatus} /> Save &amp; test
                        </button>
                    </div>
                    {geminiStatus === 'error' && <p className="text-xs text-red-500 mt-1">Couldn't verify that key.</p>}
                    {geminiStatus === 'ok' && <p className="text-xs text-emerald-600 mt-1">Key saved.</p>}
                    <a href="https://makersuite.google.com/app/apikey" target="_blank" rel="noopener noreferrer" className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline mt-1 inline-block">
                        Get a free API key from Google AI Studio
                    </a>
                </div>
            )}

            {(provider === 'local' || provider === 'hybrid') && (
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Local server base URL</label>
                    <div className="flex gap-2">
                        <input
                            type="text"
                            value={localUrl}
                            onChange={(e) => { setLocalUrl(e.target.value); setLocalStatus(null); }}
                            className="flex-1 px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none text-sm font-mono"
                        />
                        <button onClick={testLocal} disabled={localStatus === 'testing'} className="px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-700 text-sm font-medium flex items-center gap-1.5 disabled:opacity-50">
                            <StatusIcon status={localStatus} /> Test connection
                        </button>
                    </div>
                    {localStatus === 'error' && <p className="text-xs text-red-500 mt-1">No models found there — check the URL and that the server is running.</p>}
                    {localStatus === 'ok' && (
                        <div className="mt-2">
                            <p className="text-xs text-emerald-600 mb-1">Found {localModels.length} model(s). Default translation model:</p>
                            <select
                                value={selectedLocalModel}
                                onChange={(e) => setSelectedLocalModel(e.target.value)}
                                className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none"
                            >
                                {localModels.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                            </select>
                        </div>
                    )}
                </div>
            )}

            <div className="flex items-center justify-between pt-2">
                <button onClick={onBack} disabled={!onBack} className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 disabled:opacity-0">Back</button>
                <div className="flex gap-3">
                    <button onClick={onSkip} className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">Skip</button>
                    <button onClick={handleNext} disabled={saving} className="px-5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-medium disabled:opacity-50">
                        {saving ? 'Saving...' : 'Continue'}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default StepProvider;
