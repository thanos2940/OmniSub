import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, GitCompare, Check, RotateCcw } from 'lucide-react';

const ComparisonModal = ({
    isOpen,
    onClose,
    episodeName,
    diffData = [],
    onAcceptNew,
    onKeepOriginal,
}) => {
    if (!isOpen) return null;

    return (
        <AnimatePresence>
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
                <motion.div
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col overflow-hidden border border-gray-200 dark:border-gray-700"
                >
                    <div className="p-6 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/50">
                        <div className="flex items-center gap-2">
                            <GitCompare className="text-indigo-600 dark:text-indigo-400" size={20} />
                            <div>
                                <h3 className="text-lg font-bold text-gray-900 dark:text-white">
                                    Translation Diff Comparison: {episodeName}
                                </h3>
                                <p className="text-xs text-gray-500 dark:text-gray-400">
                                    Compare previously saved subtitle output against newly re-translated draft.
                                </p>
                            </div>
                        </div>
                        <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600 rounded-lg">
                            <X size={20} />
                        </button>
                    </div>

                    <div className="p-6 flex-1 overflow-y-auto space-y-4 font-mono text-xs">
                        {diffData.length === 0 ? (
                            <div className="text-center py-12 text-gray-400 font-sans">
                                No differences detected between the versions.
                            </div>
                        ) : (
                            <div className="space-y-2">
                                {diffData.map((item, idx) => (
                                    <div key={idx} className="p-3 bg-gray-50 dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-700 space-y-1.5">
                                        <div className="text-[10px] text-gray-400 font-bold uppercase tracking-wider font-sans">
                                            Cue #{item.index || idx + 1}
                                        </div>
                                        <div className="text-gray-500 line-through bg-rose-50 dark:bg-rose-950/30 p-2 rounded text-rose-800 dark:text-rose-300">
                                            - {item.original || item.old}
                                        </div>
                                        <div className="text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-950/30 p-2 rounded font-semibold">
                                            + {item.updated || item.new}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    <div className="p-6 border-t border-gray-100 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 flex justify-end gap-2 font-sans">
                        <button
                            type="button"
                            onClick={onKeepOriginal || onClose}
                            className="px-4 py-2 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-xl text-xs font-bold flex items-center gap-1.5 transition-colors"
                        >
                            <RotateCcw size={14} /> Keep Previous Version
                        </button>
                        <button
                            type="button"
                            onClick={onAcceptNew || onClose}
                            className="px-6 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 shadow-md transition-all"
                        >
                            <Check size={14} /> Accept New Translation
                        </button>
                    </div>
                </motion.div>
            </div>
        </AnimatePresence>
    );
};

export default ComparisonModal;
