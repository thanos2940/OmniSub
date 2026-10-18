import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Wrench, CheckCircle2, Loader2 } from 'lucide-react';
import { api } from '../../../api';

const ApplyFixesModal = ({
    isOpen,
    onClose,
    projectName,
    episodeName = null,
    isBatch = false,
    selectedEpisodes = [],
    onSuccess,
    toast,
}) => {
    const [targetTrack, setTargetTrack] = useState('target'); // 'target' | 'source' | 'both'
    const [fixErrors, setFixErrors] = useState(true);
    const [splitLines, setSplitLines] = useState(true);
    const [running, setRunning] = useState(false);

    if (!isOpen) return null;

    const handleApply = async () => {
        setRunning(true);
        try {
            const episodesToFix = isBatch ? selectedEpisodes : (episodeName ? [episodeName] : []);
            let count = 0;

            for (const ep of episodesToFix) {
                await api.applyEpisodeFixes(projectName, ep, {
                    target_track: targetTrack,
                    fix_common_errors: fixErrors,
                    split_long_lines: splitLines,
                });
                count++;
            }

            if (toast?.success) toast.success(`Applied SubtitleEdit fixes to ${count} episode(s)!`);
            if (onSuccess) onSuccess();
            onClose();
        } catch (err) {
            console.error("Failed to apply fixes", err);
            if (toast?.error) toast.error("Failed to apply SubtitleEdit fixes.");
        } finally {
            setRunning(false);
        }
    };

    const count = isBatch ? selectedEpisodes.length : (episodeName ? 1 : 0);

    return (
        <AnimatePresence>
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
                <motion.div
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-md overflow-hidden border border-gray-200 dark:border-gray-700"
                >
                    <div className="p-6 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/50">
                        <div className="flex items-center gap-2">
                            <Wrench className="text-indigo-600 dark:text-indigo-400" size={20} />
                            <h3 className="text-lg font-bold text-gray-900 dark:text-white">
                                Apply SubtitleEdit Fixes ({count} {count === 1 ? 'Episode' : 'Episodes'})
                            </h3>
                        </div>
                        <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600 rounded-lg">
                            <X size={20} />
                        </button>
                    </div>

                    <div className="p-6 space-y-4">
                        <div>
                            <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">
                                Subtitle Track Target
                            </label>
                            <div className="grid grid-cols-3 gap-2">
                                {[
                                    { id: 'target', label: 'Translated' },
                                    { id: 'source', label: 'Source (Raw)' },
                                    { id: 'both', label: 'Both Tracks' },
                                ].map(({ id, label }) => (
                                    <button
                                        key={id}
                                        type="button"
                                        onClick={() => setTargetTrack(id)}
                                        className={`py-2 px-3 rounded-xl border text-xs font-semibold transition-all ${
                                            targetTrack === id
                                                ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-950/50 text-indigo-700 dark:text-indigo-300'
                                                : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400'
                                        }`}
                                    >
                                        {label}
                                    </button>
                                ))}
                            </div>
                        </div>

                        <div className="space-y-2 pt-2 border-t border-gray-100 dark:border-gray-700">
                            <label className="flex items-center gap-2.5 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={fixErrors}
                                    onChange={(e) => setFixErrors(e.target.checked)}
                                    className="w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                                />
                                <span className="text-xs font-medium text-gray-700 dark:text-gray-300">
                                    Fix Common Errors (OCR typos, punctuation, unclosed tags)
                                </span>
                            </label>

                            <label className="flex items-center gap-2.5 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={splitLines}
                                    onChange={(e) => setSplitLines(e.target.checked)}
                                    className="w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                                />
                                <span className="text-xs font-medium text-gray-700 dark:text-gray-300">
                                    Split Long Lines (&gt; 42 characters per line)
                                </span>
                            </label>
                        </div>
                    </div>

                    <div className="p-6 border-t border-gray-100 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 flex justify-end gap-2">
                        <button
                            type="button"
                            onClick={onClose}
                            className="px-4 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 rounded-xl"
                        >
                            Cancel
                        </button>
                        <button
                            type="button"
                            onClick={handleApply}
                            disabled={running || (!fixErrors && !splitLines)}
                            className="px-6 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-sm font-bold flex items-center gap-2 shadow-md transition-all disabled:opacity-50"
                        >
                            {running ? <Loader2 size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}
                            <span>{running ? 'Applying Fixes...' : 'Apply Fixes'}</span>
                        </button>
                    </div>
                </motion.div>
            </div>
        </AnimatePresence>
    );
};

export default ApplyFixesModal;
