import React, { useState, useEffect } from 'react';
import { X, Check, ArrowRight, RefreshCw, Save, AlertCircle } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const TranslationComparisonModal = ({ isOpen, onClose, projectName, episodeName, currentLines, newTranslations, onApply }) => {
    const [selectedIndices, setSelectedIndices] = useState(new Set());
    const [isApplying, setIsApplying] = useState(false);

    useEffect(() => {
        if (isOpen) {
            // Initially select all new translations that are different from current
            const initialSelection = new Set();
            Object.keys(newTranslations).forEach(idx => {
                const current = currentLines[parseInt(idx)]?.translated || '';
                if (newTranslations[idx] !== current) {
                    initialSelection.add(idx);
                }
            });
            setSelectedIndices(initialSelection);
        }
    }, [isOpen, newTranslations, currentLines]);

    if (!isOpen) return null;

    const handleToggleLine = (idx) => {
        setSelectedIndices(prev => {
            const next = new Set(prev);
            if (next.has(idx)) next.delete(idx);
            else next.add(idx);
            return next;
        });
    };

    const handleSelectAll = () => {
        if (selectedIndices.size === Object.keys(newTranslations).length) {
            setSelectedIndices(new Set());
        } else {
            setSelectedIndices(new Set(Object.keys(newTranslations)));
        }
    };

    const handleApply = async () => {
        setIsApplying(true);
        try {
            const selectedMap = {};
            selectedIndices.forEach(idx => {
                selectedMap[idx] = newTranslations[idx];
            });
            await onApply(selectedMap);
            onClose();
        } catch (err) {
            console.error("Failed to merge translations", err);
        } finally {
            setIsApplying(false);
        }
    };

    // Filter to only show lines that have a new translation available
    const changedIndices = Object.keys(newTranslations).sort((a, b) => parseInt(a) - parseInt(b));

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
            <motion.div 
                initial={{ opacity: 0, scale: 0.95, y: 20 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.95, y: 20 }}
                className="bg-white dark:bg-gray-800 rounded-3xl shadow-2xl w-full max-w-6xl max-h-[90vh] flex flex-col overflow-hidden border border-gray-200 dark:border-gray-700"
            >
                {/* Header */}
                <div className="px-6 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between bg-gray-50/50 dark:bg-gray-800/50">
                    <div>
                        <h2 className="text-xl font-bold text-gray-900 dark:text-white flex items-center gap-2">
                            <RefreshCw size={20} className="text-indigo-500" />
                            Review New Translation
                        </h2>
                        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                            {episodeName} — Compare current version with new AI results
                        </p>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-full transition-colors text-gray-400">
                        <X size={20} />
                    </button>
                </div>

                {/* Toolbar */}
                <div className="px-6 py-3 bg-white dark:bg-gray-900 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
                    <div className="flex items-center gap-4 text-sm">
                        <label className="flex items-center gap-2 cursor-pointer group">
                            <div className="relative">
                                <input 
                                    type="checkbox" 
                                    className="sr-only" 
                                    checked={selectedIndices.size === changedIndices.length}
                                    onChange={handleSelectAll}
                                />
                                <div className={`w-5 h-5 rounded border transition-all flex items-center justify-center ${selectedIndices.size === changedIndices.length ? 'bg-indigo-600 border-indigo-600' : 'border-gray-300 dark:border-gray-600 group-hover:border-indigo-400'}`}>
                                    {selectedIndices.size === changedIndices.length && <Check size={14} className="text-white" />}
                                </div>
                            </div>
                            <span className="font-medium text-gray-700 dark:text-gray-300">Select All Changed ({changedIndices.length})</span>
                        </label>
                        <div className="h-4 w-px bg-gray-200 dark:bg-gray-700" />
                        <span className="text-gray-500">{selectedIndices.size} lines selected to overwrite</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <div className="flex items-center gap-1.5 text-xs text-amber-600 bg-amber-50 dark:bg-amber-900/20 px-2 py-1 rounded-lg">
                            <AlertCircle size={12} />
                            Unselected lines will keep their current translation
                        </div>
                    </div>
                </div>

                {/* Table Header */}
                <div className="grid grid-cols-[60px_1fr_1.5fr_1.5fr_60px] gap-4 px-6 py-3 bg-gray-50 dark:bg-gray-800/30 text-xs font-bold text-gray-400 uppercase tracking-wider border-b border-gray-100 dark:border-gray-700">
                    <div className="text-center">Line</div>
                    <div>Original</div>
                    <div>Current Translation</div>
                    <div>New AI Translation</div>
                    <div className="text-center">Use</div>
                </div>

                {/* Table Content */}
                <div className="flex-1 overflow-y-auto p-0 scrollbar-thin scrollbar-thumb-gray-200 dark:scrollbar-thumb-gray-700">
                    {changedIndices.map((idxStr) => {
                        const idx = parseInt(idxStr);
                        const line = currentLines[idx];
                        const isSelected = selectedIndices.has(idxStr);
                        const isDifferent = line?.translated !== newTranslations[idxStr];

                        return (
                            <div 
                                key={idxStr}
                                onClick={() => handleToggleLine(idxStr)}
                                className={`grid grid-cols-[60px_1fr_1.5fr_1.5fr_60px] gap-4 px-6 py-4 border-b border-gray-50 dark:border-gray-700/50 transition-colors cursor-pointer group ${isSelected ? 'bg-indigo-50/30 dark:bg-indigo-900/10' : 'hover:bg-gray-50 dark:hover:bg-gray-800/50'}`}
                            >
                                <div className="text-center text-sm font-mono text-gray-400">{line?.id || idx + 1}</div>
                                <div className="text-sm text-gray-600 dark:text-gray-300 line-clamp-3">{line?.original}</div>
                                <div className="text-sm text-gray-500 dark:text-gray-400 italic line-clamp-3">{line?.translated || '(Empty)'}</div>
                                <div className={`text-sm font-medium line-clamp-3 ${isSelected ? 'text-indigo-600 dark:text-indigo-400' : 'text-gray-900 dark:text-white'}`}>
                                    {newTranslations[idxStr]}
                                </div>
                                <div className="flex items-center justify-center">
                                    <div className={`w-6 h-6 rounded-full border-2 transition-all flex items-center justify-center ${isSelected ? 'bg-indigo-600 border-indigo-600 scale-110' : 'border-gray-200 dark:border-gray-600 group-hover:border-indigo-300'}`}>
                                        {isSelected && <Check size={14} className="text-white" strokeWidth={3} />}
                                    </div>
                                </div>
                            </div>
                        );
                    })}
                </div>

                {/* Footer */}
                <div className="px-6 py-6 border-t border-gray-100 dark:border-gray-700 flex items-center justify-between bg-white dark:bg-gray-800">
                    <button 
                        onClick={onClose}
                        className="px-6 py-2.5 rounded-xl text-sm font-semibold text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                    >
                        Cancel
                    </button>
                    <div className="flex items-center gap-3">
                        <button 
                            onClick={handleApply}
                            disabled={isApplying || selectedIndices.size === 0}
                            className="flex items-center gap-2 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-700 hover:to-purple-700 disabled:opacity-50 text-white px-8 py-2.5 rounded-xl text-sm font-bold transition-all shadow-lg shadow-indigo-500/20 hover:shadow-indigo-500/40"
                        >
                            {isApplying ? (
                                <>
                                    <RefreshCw size={18} className="animate-spin" />
                                    Applying...
                                </>
                            ) : (
                                <>
                                    <Save size={18} />
                                    Apply {selectedIndices.size} Changes
                                </>
                            )}
                        </button>
                    </div>
                </div>
            </motion.div>
        </div>
    );
};

export default TranslationComparisonModal;
