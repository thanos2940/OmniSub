import React, { useState, useEffect } from 'react';
import { Key, Sparkles, CheckCircle2, XCircle, Loader2, ExternalLink, ShieldCheck } from 'lucide-react';
import { api } from '../../api';
import ModelCombobox from '../ModelCombobox';

const GeminiSettings = ({ settings, onChange, toast }) => {
    const [hasApiKey, setHasApiKey] = useState(false);
    const [apiKeyInput, setApiKeyInput] = useState('');
    const [testingKey, setTestingKey] = useState(false);
    const [savingKey, setSavingKey] = useState(false);
    const [statusResult, setStatusResult] = useState(null);

    const checkKeyStatus = async () => {
        try {
            const res = await api.getApiKeyStatus();
            setHasApiKey(!!res.data?.has_key);
        } catch {
            setHasApiKey(false);
        }
    };

    useEffect(() => {
        checkKeyStatus();
    }, []);

    const handleTestKey = async () => {
        setTestingKey(true);
        setStatusResult(null);
        try {
            const keyToTest = apiKeyInput.trim() || null;
            const res = await api.testGeminiKey(keyToTest);
            if (res.data?.valid) {
                setStatusResult({ type: 'success', text: res.data.message || 'Gemini API connection verified!' });
                if (toast?.success) toast.success("Gemini API connection verified!");
            } else {
                setStatusResult({ type: 'error', text: res.data?.error || 'Invalid API Key' });
                if (toast?.error) toast.error("Gemini API verification failed");
            }
        } catch (err) {
            const msg = err.response?.data?.detail || err.message || 'Failed to test Gemini key';
            setStatusResult({ type: 'error', text: msg });
            if (toast?.error) toast.error(msg);
        } finally {
            setTestingKey(false);
        }
    };

    const handleSaveKey = async () => {
        if (!apiKeyInput.trim()) return;
        setSavingKey(true);
        try {
            await api.setApiKey(apiKeyInput.trim());
            setHasApiKey(true);
            setApiKeyInput('');
            setStatusResult({ type: 'success', text: 'Gemini API Key saved successfully!' });
            if (toast?.success) toast.success("Gemini API Key saved!");
            checkKeyStatus();
        } catch (err) {
            const msg = err.response?.data?.detail || err.message || 'Failed to save API key';
            setStatusResult({ type: 'error', text: msg });
            if (toast?.error) toast.error(msg);
        } finally {
            setSavingKey(false);
        }
    };

    const handleDeleteKey = async () => {
        if (!window.confirm("Remove stored Google Gemini API key?")) return;
        try {
            await api.deleteApiKey();
            setHasApiKey(false);
            setStatusResult(null);
            if (toast?.info) toast.info("Gemini API Key removed");
        } catch (err) {
            if (toast?.error) toast.error("Failed to remove API key");
        }
    };

    return (
        <div className="space-y-6">
            {/* Gemini API Key Configuration Card */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-5">
                <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-700 pb-4">
                    <div className="flex items-center gap-2">
                        <Key className="text-indigo-600 dark:text-indigo-400" size={20} />
                        <div>
                            <h3 className="text-base font-bold text-gray-900 dark:text-white">Google Gemini API Authentication</h3>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                                Enterprise-grade multi-lingual neural translation powered by Google GenAI &amp; Agent Development Kit (ADK).
                            </p>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        {hasApiKey ? (
                            <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-100 dark:bg-emerald-950 text-emerald-800 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-900 flex items-center gap-1">
                                <ShieldCheck size={14} /> Active &amp; Stored
                            </span>
                        ) : (
                            <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-100 dark:bg-amber-950 text-amber-800 dark:text-amber-300 border border-amber-200 dark:border-amber-900 flex items-center gap-1">
                                <XCircle size={14} /> Key Required
                            </span>
                        )}
                    </div>
                </div>

                <div className="space-y-3">
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                        {hasApiKey ? "Update Gemini API Key" : "Enter Gemini API Key"}
                    </label>

                    <div className="flex flex-col sm:flex-row gap-2">
                        <input
                            type="password"
                            value={apiKeyInput}
                            onChange={(e) => setApiKeyInput(e.target.value)}
                            placeholder={hasApiKey ? "•••••••••••••••••••••••• (Leave blank to keep existing key)" : "AIzaSy..."}
                            className="flex-1 px-4 py-2.5 rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white font-mono text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                        />
                        <div className="flex items-center gap-2">
                            <button
                                type="button"
                                onClick={handleTestKey}
                                disabled={testingKey}
                                className="px-4 py-2.5 rounded-xl bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 text-sm font-semibold flex items-center gap-2 transition-colors disabled:opacity-50"
                            >
                                {testingKey ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
                                <span>{testingKey ? "Testing..." : "Test Connection"}</span>
                            </button>

                            {apiKeyInput.trim() && (
                                <button
                                    type="button"
                                    onClick={handleSaveKey}
                                    disabled={savingKey}
                                    className="px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold flex items-center gap-2 transition-colors disabled:opacity-50 shadow-sm"
                                >
                                    {savingKey ? <Loader2 size={16} className="animate-spin" /> : <Key size={16} />}
                                    <span>Save Key</span>
                                </button>
                            )}

                            {hasApiKey && !apiKeyInput.trim() && (
                                <button
                                    type="button"
                                    onClick={handleDeleteKey}
                                    className="px-3.5 py-2.5 rounded-xl text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/30 text-sm font-semibold transition-colors"
                                    title="Delete stored key"
                                >
                                    Remove
                                </button>
                            )}
                        </div>
                    </div>

                    {statusResult && (
                        <div className={`p-3 rounded-xl text-xs flex items-center gap-2 ${
                            statusResult.type === 'success'
                                ? 'bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-900 text-emerald-700 dark:text-emerald-300'
                                : 'bg-rose-50 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-900 text-rose-700 dark:text-rose-300'
                        }`}>
                            {statusResult.type === 'success' ? <CheckCircle2 size={16} className="shrink-0" /> : <XCircle size={16} className="shrink-0" />}
                            <span>{statusResult.text}</span>
                        </div>
                    )}

                    <div className="pt-2">
                        <a
                            href="https://aistudio.google.com/app/apikey"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline flex items-center gap-1 font-semibold"
                        >
                            <span>Get a free Gemini API key from Google AI Studio</span>
                            <ExternalLink size={12} />
                        </a>
                    </div>
                </div>
            </div>

            {/* Default Models Configuration Card */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-5">
                <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-4">
                    <Sparkles className="text-indigo-600 dark:text-indigo-400" size={20} />
                    <div>
                        <h3 className="text-base font-bold text-gray-900 dark:text-white">Default Gemini Model Roster</h3>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                            Standard model assignments for translation pipelines, context extraction, and QA checks.
                        </p>
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                    <ModelCombobox
                        label="Default Translation Model"
                        value={settings.default_translation_model}
                        onChange={(v) => onChange('default_translation_model', v)}
                        placeholder="e.g. gemini-2.5-flash"
                    />

                    <ModelCombobox
                        label="Fallback Translation Model"
                        value={settings.fallback_translation_model}
                        onChange={(v) => onChange('fallback_translation_model', v)}
                        placeholder="e.g. gemini-2.5-pro or None"
                    />

                    <ModelCombobox
                        label="Context Analysis Model"
                        value={settings.default_context_model}
                        onChange={(v) => onChange('default_context_model', v)}
                        placeholder="e.g. gemini-2.5-flash"
                    />

                    <ModelCombobox
                        label="Glossary Generation Model"
                        value={settings.default_glossary_model}
                        onChange={(e) => onChange('default_glossary_model', e)}
                        placeholder="e.g. gemini-2.5-flash"
                    />

                    <ModelCombobox
                        label="Dialogue Scan & Assessment Model"
                        value={settings.default_scan_model}
                        onChange={(v) => onChange('default_scan_model', v)}
                        placeholder="e.g. gemini-2.5-flash"
                    />

                    <ModelCombobox
                        label="QC Reviewer Model"
                        value={settings.default_review_model}
                        onChange={(v) => onChange('default_review_model', v)}
                        placeholder="e.g. gemini-2.5-pro"
                    />
                </div>
            </div>

            {/* Content filter for every Gemini request */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-4">
                <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-4">
                    <ShieldCheck className="text-indigo-600 dark:text-indigo-400" size={20} />
                    <div>
                        <h3 className="text-base font-bold text-gray-900 dark:text-white">Gemini safety filter</h3>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                            Films and series contain violence, threats and crude language. Google&apos;s default filter refuses some of
                            those lines; this sets its thresholds for every Gemini request Omnisub makes.
                        </p>
                    </div>
                </div>
                <select
                    value={settings.gemini_safety_filter || 'off'}
                    onChange={(e) => onChange('gemini_safety_filter', e.target.value)}
                    className="w-full md:w-96 px-3 py-2 rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                >
                    <option value="off">Off: translate all dialogue (recommended)</option>
                    <option value="only_high">Block only content rated highly harmful</option>
                    <option value="default">Google&apos;s default thresholds</option>
                </select>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                    Some blocks can&apos;t be switched off by any setting (prohibited content, child safety, or reproducing copyrighted
                    text); lines stopped that way are reported in the log.
                </p>
            </div>
        </div>
    );
};

export default GeminiSettings;
