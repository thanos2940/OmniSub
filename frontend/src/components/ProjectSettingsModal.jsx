import React, { useState, useEffect } from 'react';
import { X, Save, Settings } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const MODELS = [
    { id: 'gemini-flash-latest', name: 'Gemini Flash (Fast & Cheap)' },
    { id: 'gemini-flash-lite-latest', name: 'Gemini Flash Lite (Fastest)' },
    { id: 'gemini-pro-latest', name: 'Gemini Pro (High Quality)' },
    { id: 'manual', name: 'Manual / Other' }
];

const ProjectSettingsModal = ({ isOpen, onClose, project, onSave }) => {
    const [settings, setSettings] = useState({
        translation_model: 'gemini-flash-latest',
        context_model: 'gemini-flash-lite-latest',
        glossary_model: 'gemini-flash-lite-latest'
    });

    useEffect(() => {
        if (project && project.settings) {
            setSettings(project.settings);
        }
    }, [project]);

    const handleChange = (key, value) => {
        setSettings(prev => ({ ...prev, [key]: value }));
    };

    const handleSave = () => {
        onSave(settings);
        onClose();
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

                    <div className="p-6 space-y-6">
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                Translation Model
                            </label>
                            <select
                                value={settings.translation_model}
                                onChange={(e) => handleChange('translation_model', e.target.value)}
                                className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
                            >
                                {MODELS.map(m => (
                                    <option key={m.id} value={m.id}>{m.name}</option>
                                ))}
                            </select>
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                Context Analysis Model
                            </label>
                            <select
                                value={settings.context_model}
                                onChange={(e) => handleChange('context_model', e.target.value)}
                                className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
                            >
                                {MODELS.map(m => (
                                    <option key={m.id} value={m.id}>{m.name}</option>
                                ))}
                            </select>
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                Glossary Generation Model
                            </label>
                            <select
                                value={settings.glossary_model}
                                onChange={(e) => handleChange('glossary_model', e.target.value)}
                                className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
                            >
                                {MODELS.map(m => (
                                    <option key={m.id} value={m.id}>{m.name}</option>
                                ))}
                            </select>
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
