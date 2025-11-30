import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Sparkles, Languages, X } from 'lucide-react';

const TranslationModal = ({ isOpen, onClose, onConfirm, title = "Start Translation", count = 1 }) => {
    const [enhanceGlossary, setEnhanceGlossary] = useState(true);

    if (!isOpen) return null;

    return (
        <AnimatePresence>
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
                <motion.div
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full max-w-md overflow-hidden"
                >
                    <div className="p-6 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center">
                        <h3 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                            <Languages className="text-indigo-600" />
                            {title}
                        </h3>
                        <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
                            <X size={20} />
                        </button>
                    </div>

                    <div className="p-6 space-y-6">
                        <p className="text-gray-600 dark:text-gray-300">
                            You are about to translate <strong>{count}</strong> episode{count > 1 ? 's' : ''}.
                        </p>

                        <div
                            className={`p-4 rounded-xl border-2 cursor-pointer transition-all ${enhanceGlossary
                                ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-900/20'
                                : 'border-gray-200 dark:border-gray-700 hover:border-indigo-200'
                                }`}
                            onClick={() => setEnhanceGlossary(!enhanceGlossary)}
                        >
                            <div className="flex items-start gap-3">
                                <div className={`mt-1 p-1.5 rounded-full ${enhanceGlossary ? 'bg-indigo-600 text-white' : 'bg-gray-200 text-gray-500'}`}>
                                    <Sparkles size={16} />
                                </div>
                                <div>
                                    <h4 className="font-semibold text-gray-900 dark:text-white">Enhance Glossary First</h4>
                                    <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                                        Scan the episode{count > 1 ? 's' : ''} for new terms and add them to the glossary before translating. Recommended for better consistency.
                                    </p>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div className="p-6 bg-gray-50 dark:bg-gray-900/50 flex justify-end gap-3">
                        <button
                            onClick={onClose}
                            className="px-4 py-2 text-gray-600 hover:text-gray-800 font-medium"
                        >
                            Cancel
                        </button>
                        <button
                            onClick={() => onConfirm(enhanceGlossary)}
                            className="px-6 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-medium shadow-lg shadow-indigo-500/20 flex items-center gap-2"
                        >
                            <Languages size={18} />
                            Start Translation
                        </button>
                    </div>
                </motion.div>
            </div>
        </AnimatePresence>
    );
};

export default TranslationModal;
