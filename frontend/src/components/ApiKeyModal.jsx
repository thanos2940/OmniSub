import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Key, X, ExternalLink, AlertCircle } from 'lucide-react';
import { api } from '../api';

const ApiKeyModal = ({ isOpen, onClose, onSave, allowSkip = true }) => {
    const [apiKey, setApiKey] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const handleSave = async () => {
        if (!apiKey.trim()) {
            setError('Please enter an API key');
            return;
        }

        setLoading(true);
        setError('');

        try {
            await api.setApiKey(apiKey);
            console.log('API key saved successfully');
            onSave?.();
            onClose();
        } catch (err) {
            setError(err.response?.data?.detail || 'Failed to save API key');
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
                        className="relative bg-white dark:bg-gray-800 rounded-2xl shadow-2xl max-w-md w-full overflow-hidden"
                    >
                        {/* Header */}
                        <div className="bg-gradient-to-r from-indigo-500 to-purple-600 p-6 text-white">
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-3">
                                    <div className="p-2 bg-white/20 rounded-lg">
                                        <Key size={24} />
                                    </div>
                                    <div>
                                        <h2 className="text-xl font-bold">API Key Required</h2>
                                        <p className="text-sm text-white/80">Set up your Google Gemini API key</p>
                                    </div>
                                </div>
                                {allowSkip && (
                                    <button
                                        onClick={handleSkip}
                                        className="p-1 hover:bg-white/20 rounded-lg transition-colors"
                                    >
                                        <X size={20} />
                                    </button>
                                )}
                            </div>
                        </div>

                        {/* Body */}
                        <div className="p-6 space-y-4">
                            <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
                                <div className="flex gap-2">
                                    <AlertCircle className="text-blue-600 dark:text-blue-400 flex-shrink-0 mt-0.5" size={20} />
                                    <div className="text-sm">
                                        <p className="font-medium text-blue-900 dark:text-blue-100 mb-1">
                                            Omnisub uses Google Gemini AI for translation
                                        </p>
                                        <p className="text-blue-700 dark:text-blue-300">
                                            You'll need a free API key from Google AI Studio to use AI features.
                                        </p>
                                    </div>
                                </div>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                    API Key
                                </label>
                                <input
                                    type="password"
                                    value={apiKey}
                                    onChange={(e) => {
                                        setApiKey(e.target.value);
                                        setError('');
                                    }}
                                    onKeyPress={(e) => e.key === 'Enter' && handleSave()}
                                    placeholder="AIza..."
                                    className="w-full px-4 py-3 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none font-mono text-sm"
                                />
                                {error && (
                                    <p className="mt-2 text-sm text-red-600 dark:text-red-400">{error}</p>
                                )}
                            </div>

                            <a
                                href="https://makersuite.google.com/app/apikey"
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex items-center gap-2 text-indigo-600 dark:text-indigo-400 hover:underline text-sm"
                            >
                                <ExternalLink size={16} />
                                Get your free API key from Google AI Studio
                            </a>
                        </div>

                        {/* Footer */}
                        <div className="bg-gray-50 dark:bg-gray-900/50 px-6 py-4 flex gap-3 justify-end">
                            {allowSkip && (
                                <button
                                    onClick={handleSkip}
                                    className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg transition-colors"
                                >
                                    Skip for now
                                </button>
                            )}
                            <button
                                onClick={handleSave}
                                disabled={loading || !apiKey.trim()}
                                className="px-6 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                            >
                                {loading ? (
                                    <>
                                        <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                        Saving...
                                    </>
                                ) : (
                                    'Save API Key'
                                )}
                            </button>
                        </div>
                    </motion.div>
                </div>
            )}
        </AnimatePresence>
    );
};

export default ApiKeyModal;
