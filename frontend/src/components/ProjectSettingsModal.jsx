import React, { useState, useEffect } from 'react';
import { X, Save, Settings, AlertCircle } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import ModelCombobox from './ModelCombobox';

const ProjectSettingsModal = ({ isOpen, onClose, project, settings: initialSettings, onSave }) => {
    const [settings, setSettings] = useState({
        translation_model: 'gemini-2.5-flash',
        context_model: 'gemini-2.5-flash',
        glossary_model: 'gemini-2.5-flash',
        local_llm_base_url: 'http://localhost:1234/v1'
    });
    const [testStatus, setTestStatus] = useState(null); // 'testing', 'success', 'error'
    const [testError, setTestError] = useState(null);
    const [diagnostics, setDiagnostics] = useState(null);

    useEffect(() => {
        const src = initialSettings || (project && project.settings);
        if (src) {
            setSettings(prev => ({ ...prev, ...src }));
        }
    }, [project, initialSettings]);

    const handleChange = (key, value) => {
        setSettings(prev => ({ ...prev, [key]: value }));
    };

    const handleSave = () => {
        onSave(settings);
        onClose();
    };

    const handleTestConnection = async () => {
        setTestStatus('testing');
        try {
            const { api } = await import('../api');
            const res = await api.fetchLocalModels(settings.local_llm_base_url);
            if (res.data && res.data.models && res.data.models.length > 0) {
                setTestStatus('success');
                setTestError(null);
                setDiagnostics(null);
                // Auto-save verified local URL to global settings
                await api.updateSettings({ local_llm_base_url: settings.local_llm_base_url });
            } else {
                setTestStatus('error');
                setTestError(res.data?.error || "No models detected on this server");
                setDiagnostics(res.data?.diagnostics || null);
            }
        } catch (e) {
            setTestStatus('error');
            setTestError(e.message || "Connection failed");
            setDiagnostics(null);
        }
    };

    if (!isOpen) return null;

    return (
        <AnimatePresence>
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
                <motion.div
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="bg-white dark:bg-gray-800 rounded-xl shadow-xl w-full max-w-md overflow-hidden"
                >
                    <div className="flex justify-between items-center p-6 border-b border-gray-200 dark:border-gray-700">
                        <div className="flex items-center gap-2">
                            <Settings className="text-gray-500" size={20} />
                            <h2 className="text-xl font-bold text-gray-900 dark:text-white">Project Settings</h2>
                        </div>
                        <button onClick={onClose} className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">
                            <X size={24} />
                        </button>
                    </div>

                    <div className="p-6 space-y-5">
                        <ModelCombobox
                            label="Translation Model"
                            value={settings.translation_model}
                            onChange={(v) => handleChange('translation_model', v)}
                        />
                        <ModelCombobox
                            label="Context Analysis Model"
                            value={settings.context_model}
                            onChange={(v) => handleChange('context_model', v)}
                        />
                        <ModelCombobox
                            label="Glossary Generation Model"
                            value={settings.glossary_model}
                            onChange={(v) => handleChange('glossary_model', v)}
                        />

                        <div className="pt-4 border-t border-gray-100 dark:border-gray-700">
                            <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Local LLM (LM Studio)</h3>
                            <div className="space-y-3">
                                <div className="flex gap-2">
                                    <div className="flex-1">
                                        <input
                                            type="text"
                                            value={settings.local_llm_base_url}
                                            onChange={(e) => handleChange('local_llm_base_url', e.target.value)}
                                            placeholder="http://localhost:1234/v1"
                                            className="w-full px-4 py-2 rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                                        />
                                    </div>
                                    <button 
                                        onClick={handleTestConnection}
                                        disabled={testStatus === 'testing'}
                                        className={`px-4 py-2 rounded-xl text-sm font-medium transition-colors ${
                                            testStatus === 'success' ? 'bg-green-100 text-green-700' :
                                            testStatus === 'error' ? 'bg-red-100 text-red-700' :
                                            'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-200'
                                        }`}
                                    >
                                        {testStatus === 'testing' ? 'Testing...' : 
                                         testStatus === 'success' ? 'Connected!' : 
                                         testStatus === 'error' ? 'Failed' : 'Test'}
                                    </button>
                                </div>
                                <p className="text-xs text-gray-500 dark:text-gray-400">
                                    Input the API Base URL from LM Studio's Local Server tab.
                                </p>
                                {testError && (
                                    <div className="flex flex-col gap-2 p-3 rounded-xl bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 text-xs">
                                        <div className="flex items-start gap-2">
                                            <AlertCircle size={14} className="mt-0.5 flex-shrink-0" />
                                            <span className="font-semibold">{testError}</span>
                                        </div>
                                        {diagnostics && diagnostics.tried_urls && (
                                            <div className="mt-2 border-t border-red-100 dark:border-red-900/30 pt-2 space-y-1 overflow-hidden">
                                                <p className="font-bold opacity-70">Tried endpoints:</p>
                                                {diagnostics.tried_urls.map((u, i) => (
                                                    <div key={i} className="font-mono bg-white/50 dark:bg-black/20 p-1.5 rounded border border-red-100/50 dark:border-red-900/20">
                                                        <div className="truncate">{u}</div>
                                                        <div className="text-[10px] opacity-60 mt-1 italic italic">
                                                            {diagnostics.errors?.[i] || "Unknown error"}
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>

                    <div className="p-6 border-t border-gray-200 dark:border-gray-700 flex justify-end gap-3">
                        <button
                            onClick={onClose}
                            className="px-4 py-2 text-gray-600 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
                        >
                            Cancel
                        </button>
                        <button
                            onClick={handleSave}
                            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg transition-colors"
                        >
                            <Save size={18} />
                            Save Settings
                        </button>
                    </div>
                </motion.div>
            </div>
        </AnimatePresence>
    );
};

export default ProjectSettingsModal;
