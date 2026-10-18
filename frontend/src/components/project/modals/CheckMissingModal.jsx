import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Search, CheckCircle2, AlertTriangle, Loader2, Play } from 'lucide-react';
import { api } from '../../../api';

const CheckMissingModal = ({
    isOpen,
    onClose,
    projectName,
    episodeName,
    onSuccess,
    toast,
}) => {
    const [scanning, setScanning] = useState(true);
    const [missingReport, setMissingReport] = useState(null);
    const [translating, setTranslating] = useState(false);

    useEffect(() => {
        if (isOpen && projectName && episodeName) {
            setScanning(true);
            setMissingReport(null);
            api.checkMissingLines(projectName, episodeName)
                .then(res => setMissingReport(res.data))
                .catch(err => {
                    console.error("Check missing failed", err);
                    if (toast?.error) toast.error("Failed to scan for missing lines.");
                })
                .finally(() => setScanning(false));
        }
    }, [isOpen, projectName, episodeName, toast]);

    if (!isOpen) return null;

    const handleTranslateMissing = async () => {
        setTranslating(true);
        try {
            await api.translateMissingLines(projectName, episodeName);
            if (toast?.success) toast.success(`Missing lines queued for translation!`);
            if (onSuccess) onSuccess();
            onClose();
        } catch (err) {
            console.error("Failed to translate missing lines", err);
            if (toast?.error) toast.error("Failed to start gap translation.");
        } finally {
            setTranslating(false);
        }
    };

    const missingCount = missingReport?.missing_count || 0;
    const totalLines = missingReport?.total_lines || 0;

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
                            <Search className="text-indigo-600 dark:text-indigo-400" size={20} />
                            <h3 className="text-lg font-bold text-gray-900 dark:text-white">
                                Missing Lines Check: {episodeName}
                            </h3>
                        </div>
                        <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600 rounded-lg">
                            <X size={20} />
                        </button>
                    </div>

                    <div className="p-6 space-y-4">
                        {scanning ? (
                            <div className="py-8 text-center text-gray-500 space-y-2">
                                <Loader2 className="w-8 h-8 mx-auto animate-spin text-indigo-600" />
                                <p className="text-sm">Scanning translated cues vs source cues...</p>
                            </div>
                        ) : missingReport ? (
                            <div className="space-y-4">
                                <div className={`p-4 rounded-xl flex items-center gap-3 ${
                                    missingCount === 0
                                        ? 'bg-emerald-50 dark:bg-emerald-950/30 text-emerald-800 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-900'
                                        : 'bg-amber-50 dark:bg-amber-950/30 text-amber-800 dark:text-amber-300 border border-amber-200 dark:border-amber-900'
                                }`}>
                                    {missingCount === 0 ? <CheckCircle2 size={24} className="shrink-0" /> : <AlertTriangle size={24} className="shrink-0" />}
                                    <div>
                                        <div className="font-bold text-sm">
                                            {missingCount === 0 ? "All lines translated!" : `Found ${missingCount} missing line(s)`}
                                        </div>
                                        <div className="text-xs mt-0.5 opacity-90">
                                            Total source cues: {totalLines} | Completed: {totalLines - missingCount}
                                        </div>
                                    </div>
                                </div>

                                {missingCount > 0 && missingReport.missing_indices && (
                                    <div className="max-h-48 overflow-y-auto p-3 bg-gray-50 dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-700 text-xs font-mono">
                                        <div className="font-semibold text-gray-500 uppercase tracking-wider mb-1">Missing Indices:</div>
                                        <div className="flex flex-wrap gap-1">
                                            {missingReport.missing_indices.slice(0, 50).map(idx => (
                                                <span key={idx} className="px-1.5 py-0.5 rounded bg-gray-200 dark:bg-gray-800 text-gray-700 dark:text-gray-300">
                                                    #{idx + 1}
                                                </span>
                                            ))}
                                            {missingReport.missing_indices.length > 50 && (
                                                <span className="text-gray-400">...+{missingReport.missing_indices.length - 50} more</span>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </div>
                        ) : null}
                    </div>

                    <div className="p-6 border-t border-gray-100 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 flex justify-end gap-2">
                        <button
                            type="button"
                            onClick={onClose}
                            className="px-4 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 rounded-xl"
                        >
                            Close
                        </button>
                        {missingCount > 0 && (
                            <button
                                type="button"
                                onClick={handleTranslateMissing}
                                disabled={translating}
                                className="px-6 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-sm font-bold flex items-center gap-2 shadow-md transition-all disabled:opacity-50"
                            >
                                {translating ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
                                <span>Translate Missing Gaps</span>
                            </button>
                        )}
                    </div>
                </motion.div>
            </div>
        </AnimatePresence>
    );
};

export default CheckMissingModal;
