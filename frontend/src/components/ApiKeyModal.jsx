import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Key, X, ExternalLink, AlertCircle, CheckCircle2, Eye, EyeOff, ShieldCheck, Trash2, Zap, RefreshCw } from 'lucide-react';
import { api } from '../api';

const ApiKeyModal = ({ isOpen, onClose, onSave, allowSkip = true }) => {
    const [apiKey, setApiKey] = useState('');
    const [showKey, setShowKey] = useState(false);
    const [hasConfiguredKey, setHasConfiguredKey] = useState(false);
    const [loading, setLoading] = useState(false);
    const [testing, setTesting] = useState(false);
    const [testResult, setTestResult] = useState(null); // { valid: bool, message: str, error: str }
    const [error, setError] = useState('');
    const [successMessage, setSuccessMessage] = useState('');

    useEffect(() => {
        if (isOpen) {
            checkStatus();
            setTestResult(null);
            setError('');
            setSuccessMessage('');
            setApiKey('');
        }
    }, [isOpen]);

    const checkStatus = async () => {
        try {
            const res = await api.getApiKeyStatus();
            setHasConfiguredKey(!!res.data?.has_key);
        } catch (err) {
            console.error('Failed to check API key status', err);
            setHasConfiguredKey(false);
        }
    };

    const handleTest = async () => {
        setTesting(true);
        setError('');
        setTestResult(null);
        try {
            const keyToTest = apiKey.trim() || null;
            const res = await api.testGeminiKey(keyToTest);
            if (res.data?.valid) {
                setTestResult({ valid: true, message: res.data.message || 'Connection successful!' });
            } else {
                setTestResult({ valid: false, error: res.data?.error || 'Connection failed' });
            }
        } catch (err) {
            setTestResult({
                valid: false,
                error: err.response?.data?.detail || err.message || 'Failed to connect to Google Gemini API'
            });
        } finally {
            setTesting(false);
        }
    };

    const handleSave = async () => {
        if (!apiKey.trim()) {
            setError('Please enter an API key');
            return;
        }

        setLoading(true);
        setError('');
        setSuccessMessage('');

        try {
            await api.setApiKey(apiKey.trim());
            setHasConfiguredKey(true);
            setSuccessMessage('API key updated and saved successfully!');
            setApiKey('');
            onSave?.();
            setTimeout(() => {
                onClose();
            }, 1200);
        } catch (err) {
            setError(err.response?.data?.detail || 'Failed to save API key');
        } finally {
            setLoading(false);
        }
    };

    const handleDelete = async () => {
        if (!window.confirm('Are you sure you want to remove the configured Gemini API key?')) return;
        setLoading(true);
        setError('');
        try {
            await api.deleteApiKey();
            setHasConfiguredKey(false);
            setApiKey('');
            setTestResult(null);
            setSuccessMessage('API key removed.');
            onSave?.();
        } catch (err) {
            setError(err.response?.data?.detail || 'Failed to remove API key');
        } finally {
            setLoading(false);
        }
    };

    const handleSkip = () => {
        if (allowSkip) {
            onClose();
        }
    };

    return (
        <AnimatePresence>
            {isOpen && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                    {/* Backdrop */}
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
                        onClick={handleSkip}
                    />

                    {/* Modal */}
                    <motion.div
                        initial={{ opacity: 0, scale: 0.95, y: 20 }}
                        animate={{ opacity: 1, scale: 1, y: 0 }}
                        exit={{ opacity: 0, scale: 0.95, y: 20 }}
                        className="relative bg-white dark:bg-gray-850 rounded-2xl shadow-2xl max-w-lg w-full overflow-hidden border border-gray-100 dark:border-gray-700/80"
                    >
                        {/* Header */}
                        <div className="bg-gradient-to-r from-indigo-600 via-indigo-700 to-purple-700 p-6 text-white">
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-3">
                                    <div className="p-2.5 bg-white/20 rounded-xl backdrop-blur-md shadow-inner">
                                        <Key size={22} />
                                    </div>
                                    <div>
                                        <h2 className="text-xl font-bold tracking-tight">Google Gemini API Key</h2>
                                        <p className="text-xs text-indigo-100/90 font-medium">Manage and test your LLM translation credentials</p>
                                    </div>
                                </div>
                                {allowSkip && (
                                    <button
                                        onClick={handleSkip}
                                        className="p-1.5 hover:bg-white/20 rounded-lg transition-colors text-white/80 hover:text-white"
                                        title="Close"
                                    >
                                        <X size={18} />
                                    </button>
                                )}
                            </div>
                        </div>

                        {/* Body */}
                        <div className="p-6 space-y-5">
                            {/* Current status pill */}
                            <div className="flex items-center justify-between p-3.5 rounded-xl bg-gray-50 dark:bg-gray-800/80 border border-gray-200/80 dark:border-gray-700/80 text-sm">
                                <div className="flex items-center gap-2.5">
                                    <div className={`w-2.5 h-2.5 rounded-full ${hasConfiguredKey ? 'bg-emerald-500 ring-4 ring-emerald-500/20' : 'bg-amber-500 ring-4 ring-amber-500/20'}`} />
                                    <span className="font-semibold text-gray-800 dark:text-gray-200">
                                        {hasConfiguredKey ? 'API Key Configured & Active' : 'No API Key Configured'}
                                    </span>
                                </div>
                                {hasConfiguredKey && (
                                    <button
                                        type="button"
                                        onClick={handleDelete}
                                        disabled={loading}
                                        className="text-xs text-rose-600 hover:text-rose-700 dark:text-rose-400 dark:hover:text-rose-300 font-medium flex items-center gap-1 hover:underline disabled:opacity-50"
                                    >
                                        <Trash2 size={13} />
                                        Remove Key
                                    </button>
                                )}
                            </div>

                            {/* Input Field */}
                            <div>
                                <label className="block text-xs font-bold text-gray-600 dark:text-gray-400 uppercase tracking-wider mb-1.5">
                                    {hasConfiguredKey ? 'Update API Key' : 'Enter API Key'}
                                </label>
                                <div className="relative">
                                    <input
                                        type={showKey ? 'text' : 'password'}
                                        value={apiKey}
                                        onChange={(e) => {
                                            setApiKey(e.target.value);
                                            setError('');
                                            setTestResult(null);
                                        }}
                                        onKeyDown={(e) => e.key === 'Enter' && handleSave()}
                                        placeholder={hasConfiguredKey ? "Enter new key to replace current (AIza...)" : "Paste your Google Gemini API key here (AIza...)"}
                                        className="w-full pl-4 pr-11 py-2.5 rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none font-mono text-sm transition-all shadow-sm"
                                    />
                                    <button
                                        type="button"
                                        onClick={() => setShowKey(!showKey)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 p-1"
                                        title={showKey ? "Hide key" : "Show key"}
                                    >
                                        {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
                                    </button>
                                </div>
                                <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-1">
                                    Saved securely in backend configuration and used for translation, scanning, and scene analysis.
                                </p>
                            </div>

                            {/* Test Result Message */}
                            {testResult && (
                                <div className={`p-3 rounded-xl border text-xs flex items-start gap-2.5 animate-in fade-in duration-200 ${
                                    testResult.valid
                                        ? 'bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-800/60 text-emerald-800 dark:text-emerald-300'
                                        : 'bg-rose-50 dark:bg-rose-950/30 border-rose-200 dark:border-rose-800/60 text-rose-800 dark:text-rose-300'
                                }`}>
                                    {testResult.valid ? (
                                        <CheckCircle2 size={16} className="text-emerald-600 dark:text-emerald-400 shrink-0 mt-0.5" />
                                    ) : (
                                        <AlertCircle size={16} className="text-rose-600 dark:text-rose-400 shrink-0 mt-0.5" />
                                    )}
                                    <div className="flex-1">
                                        <div className="font-semibold">{testResult.valid ? 'Verification Successful' : 'Connection Error'}</div>
                                        <div className="mt-0.5 font-mono text-[11px] break-words">{testResult.message || testResult.error}</div>
                                    </div>
                                </div>
                            )}

                            {error && (
                                <p className="text-xs text-rose-600 dark:text-rose-400 bg-rose-50 dark:bg-rose-900/20 p-2.5 rounded-lg border border-rose-200 dark:border-rose-800">
                                    {error}
                                </p>
                            )}

                            {successMessage && (
                                <p className="text-xs text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 p-2.5 rounded-lg border border-emerald-200 dark:border-emerald-800 font-medium">
                                    {successMessage}
                                </p>
                            )}

                            {/* External link */}
                            <div className="pt-2 border-t border-gray-100 dark:border-gray-800 flex items-center justify-between">
                                <a
                                    href="https://aistudio.google.com/app/apikey"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="inline-flex items-center gap-1.5 text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:underline"
                                >
                                    <ExternalLink size={13} />
                                    Get a free API key at Google AI Studio
                                </a>
                            </div>
                        </div>

                        {/* Footer Actions */}
                        <div className="bg-gray-50/80 dark:bg-gray-900/60 px-6 py-4 flex items-center justify-between border-t border-gray-100 dark:border-gray-800">
                            <button
                                type="button"
                                onClick={handleTest}
                                disabled={testing || (!apiKey.trim() && !hasConfiguredKey)}
                                className="px-3.5 py-2 text-xs font-bold rounded-xl border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-all flex items-center gap-1.5 disabled:opacity-40"
                            >
                                {testing ? (
                                    <>
                                        <RefreshCw size={13} className="animate-spin text-indigo-500" />
                                        Testing...
                                    </>
                                ) : (
                                    <>
                                        <Zap size={13} className="text-amber-500" />
                                        Test Connection
                                    </>
                                )}
                            </button>

                            <div className="flex items-center gap-2.5">
                                {allowSkip && (
                                    <button
                                        type="button"
                                        onClick={handleSkip}
                                        className="px-4 py-2 text-xs font-semibold text-gray-600 dark:text-gray-400 hover:bg-gray-200/60 dark:hover:bg-gray-800 rounded-xl transition-colors"
                                    >
                                        Cancel
                                    </button>
                                )}
                                <button
                                    type="button"
                                    onClick={handleSave}
                                    disabled={loading || !apiKey.trim()}
                                    className="px-5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold transition-all shadow-md shadow-indigo-500/20 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
                                >
                                    {loading ? (
                                        <>
                                            <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                            Saving...
                                        </>
                                    ) : (
                                        'Save Key'
                                    )}
                                </button>
                            </div>
                        </div>
                    </motion.div>
                </div>
            )}
        </AnimatePresence>
    );
};

export default ApiKeyModal;
