import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Play, Zap, Database, RotateCcw, Loader2 } from 'lucide-react';
import ModelCombobox from '../../ModelCombobox';

const TranslationModal = ({
    isOpen,
    onClose,
    onConfirm,
    episodeName = null,
    isBatch = false,
    batchCount = 0,
    defaultModel = '',
}) => {
    const [translationType, setTranslationType] = useState('saved');
    const [force, setForce] = useState(false);
    const [enhanceGlossary, setEnhanceGlossary] = useState(false);
    const [model, setModel] = useState(defaultModel || '');
    const [isSubmitting, setIsSubmitting] = useState(false);
    const isSubmittingRef = useRef(false);

    useEffect(() => {
        if (!isOpen) {
            isSubmittingRef.current = false;
            setIsSubmitting(false);
        }
    }, [isOpen]);

    if (!isOpen) return null;

    const handleStart = async () => {
        if (isSubmittingRef.current || isSubmitting) return;
        isSubmittingRef.current = true;
        setIsSubmitting(true);
        try {
            await onConfirm({
                translationType,
                force,
                enhanceGlossary,
                model: model || undefined,
                episodeName,
                isBatch,
            });
            onClose();
        } catch (err) {
            console.error("Translation submission error:", err);
        } finally {
            isSubmittingRef.current = false;
            setIsSubmitting(false);
        }
    };

    return (
        <AnimatePresence>
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
                <motion.div
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden border border-gray-200 dark:border-gray-700"
                >
                    <div className="p-6 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/50">
                        <div className="flex items-center gap-2">
                            <Zap className="text-indigo-600 dark:text-indigo-400" size={20} />
                            <h3 className="text-lg font-bold text-gray-900 dark:text-white">
                                {isBatch ? `Translate ${batchCount} Episodes` : `Translate ${episodeName || 'Episode'}`}
                            </h3>
                        </div>
                        <button
                            onClick={onClose}
                            disabled={isSubmitting}
                            className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            <X size={20} />
                        </button>
                    </div>

                    <div className="p-6 space-y-5">
                        {/* Translation Mode Selection */}
                        <div className="space-y-2">
                            <label className="text-xs font-bold text-gray-500 uppercase tracking-wider">
                                Translation Cache Strategy
                            </label>
                            <div className="grid grid-cols-2 gap-3">
                                <button
                                    type="button"
                                    onClick={() => !isSubmitting && setTranslationType('saved')}
                                    disabled={isSubmitting}
                                    className={`p-4 rounded-xl border text-left transition-all disabled:opacity-60 ${
                                        translationType === 'saved'
                                            ? 'border-indigo-500 bg-indigo-50/60 dark:bg-indigo-950/40 ring-1 ring-indigo-500'
                                            : 'border-gray-200 dark:border-gray-700 hover:border-gray-300'
                                    }`}
                                >
                                    <div className="flex items-center gap-2 mb-1">
                                        <Database size={16} className={translationType === 'saved' ? 'text-indigo-600' : 'text-gray-400'} />
                                        <span className="font-bold text-sm text-gray-900 dark:text-white">Saved / Resume</span>
                                    </div>
                                    <p className="text-xs text-gray-500 dark:text-gray-400">
                                        Reuses previously translated scenes or TM matches. Fast &amp; token-efficient.
                                    </p>
                                </button>

                                <button
                                    type="button"
                                    onClick={() => !isSubmitting && setTranslationType('scratch')}
                                    disabled={isSubmitting}
                                    className={`p-4 rounded-xl border text-left transition-all disabled:opacity-60 ${
                                        translationType === 'scratch'
                                            ? 'border-indigo-500 bg-indigo-50/60 dark:bg-indigo-950/40 ring-1 ring-indigo-500'
                                            : 'border-gray-200 dark:border-gray-700 hover:border-gray-300'
                                    }`}
                                >
                                    <div className="flex items-center gap-2 mb-1">
                                        <RotateCcw size={16} className={translationType === 'scratch' ? 'text-indigo-600' : 'text-gray-400'} />
                                        <span className="font-bold text-sm text-gray-900 dark:text-white">From Scratch</span>
                                    </div>
                                    <p className="text-xs text-gray-500 dark:text-gray-400">
                                        Ignores previous translation checkpoints and re-translates from line 1.
                                    </p>
                                </button>
                            </div>
                        </div>

                        {/* Model Selection Override */}
                        <div>
                            <ModelCombobox
                                label="Model Override (Optional)"
                                value={model}
                                onChange={setModel}
                                placeholder={defaultModel ? `Project Default: ${defaultModel}` : "Select model..."}
                                disabled={isSubmitting}
                            />
                        </div>

                        {/* Additional Toggles */}
                        <div className="space-y-2 pt-2 border-t border-gray-100 dark:border-gray-700">
                            <label className={`flex items-center gap-2.5 ${isSubmitting ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}>
                                <input
                                    type="checkbox"
                                    checked={force}
                                    onChange={(e) => setForce(e.target.checked)}
                                    disabled={isSubmitting}
                                    className="w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                                />
                                <span className="text-xs font-medium text-gray-700 dark:text-gray-300">
                                    Force re-translation even if already completed
                                </span>
                            </label>

                            <label className={`flex items-center gap-2.5 ${isSubmitting ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}>
                                <input
                                    type="checkbox"
                                    checked={enhanceGlossary}
                                    onChange={(e) => setEnhanceGlossary(e.target.checked)}
                                    disabled={isSubmitting}
                                    className="w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                                />
                                <span className="text-xs font-medium text-gray-700 dark:text-gray-300">
                                    Harvest novel dialogue terms into glossary after completion
                                </span>
                            </label>
                        </div>
                    </div>

                    <div className="p-6 border-t border-gray-100 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 flex justify-end gap-2">
                        <button
                            type="button"
                            onClick={onClose}
                            disabled={isSubmitting}
                            className="px-4 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 rounded-xl disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            Cancel
                        </button>
                        <button
                            type="button"
                            onClick={handleStart}
                            disabled={isSubmitting}
                            className="px-6 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-sm font-bold flex items-center gap-2 shadow-md transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            {isSubmitting ? (
                                <Loader2 size={16} className="animate-spin" />
                            ) : (
                                <Play size={16} />
                            )}
                            <span>
                                {isSubmitting
                                    ? (isBatch ? 'Starting Batch...' : 'Starting...')
                                    : (isBatch ? `Start Batch (${batchCount})` : 'Start Translation')}
                            </span>
                        </button>
                    </div>
                </motion.div>
            </div>
        </AnimatePresence>
    );
};

export default TranslationModal;
