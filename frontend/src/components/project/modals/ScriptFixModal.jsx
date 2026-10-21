import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, ShieldAlert, CheckCircle2, Loader2, Wrench } from 'lucide-react';
import { api } from '../../../api';

const ScriptFixModal = ({
    isOpen,
    onClose,
    projectName,
    episodeName,
    onSuccess,
    toast,
}) => {
    const [scanning, setScanning] = useState(true);
    const [scanData, setScanData] = useState(null);
    const [repairing, setRepairing] = useState(false);

    useEffect(() => {
        if (isOpen && projectName && episodeName) {
            setScanning(true);
            setScanData(null);
            api.scanScript(projectName, episodeName)
                .then(res => setScanData(res.data))
                .catch(err => {
                    console.error("ScriptGuard scan failed", err);
                    if (toast?.error) toast.error("ScriptGuard scan failed.");
                })
                .finally(() => setScanning(false));
        }
    }, [isOpen, projectName, episodeName, toast]);

    if (!isOpen) return null;

    const handleRepair = async () => {
        setRepairing(true);
        try {
            const res = await api.fixScript(projectName, episodeName);
            if (toast?.success) toast.success(`ScriptGuard repaired ${res.data?.repaired_count || 'all'} characters!`);
            if (onSuccess) onSuccess();
            onClose();
        } catch (err) {
            console.error("ScriptGuard fix failed", err);
            if (toast?.error) toast.error("Failed to repair script characters.");
        } finally {
            setRepairing(false);
        }
    };

    const violationsCount = scanData?.violations_count || 0;

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
                            <ShieldAlert className="text-purple-600 dark:text-purple-400" size={20} />
                            <h3 className="text-lg font-bold text-gray-900 dark:text-white">
                                ScriptGuard Homoglyph Inspector: {episodeName}
                            </h3>
                        </div>
                        <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600 rounded-lg">
                            <X size={20} />
                        </button>
                    </div>

                    <div className="p-6 space-y-4">
                        {scanning ? (
                            <div className="py-8 text-center text-gray-500 space-y-2">
                                <Loader2 className="w-8 h-8 mx-auto animate-spin text-purple-600" />
                                <p className="text-sm">Scanning for mixed-alphabet homoglyphs...</p>
                            </div>
                        ) : scanData ? (
                            <div className="space-y-4">
                                <div className={`p-4 rounded-xl flex items-center gap-3 ${
                                    violationsCount === 0
                                        ? 'bg-emerald-50 dark:bg-emerald-950/30 text-emerald-800 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-900'
                                        : 'bg-purple-50 dark:bg-purple-950/30 text-purple-800 dark:text-purple-300 border border-purple-200 dark:border-purple-900'
                                }`}>
                                    {violationsCount === 0 ? <CheckCircle2 size={24} className="shrink-0" /> : <ShieldAlert size={24} className="shrink-0" />}
                                    <div>
                                        <div className="font-bold text-sm">
                                            {violationsCount === 0 ? "No mixed-alphabet characters detected!" : `Found ${violationsCount} wrong-alphabet character(s)`}
                                        </div>
                                        <div className="text-xs mt-0.5 opacity-90">
                                            Detected corrupt Latin/Cyrillic lookalikes embedded inside Greek/target text words.
                                        </div>
                                    </div>
                                </div>

                                {violationsCount > 0 && scanData.samples && (
                                    <div className="max-h-48 overflow-y-auto p-3 bg-gray-50 dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-700 text-xs font-mono space-y-2">
                                        <div className="font-semibold text-gray-500 uppercase tracking-wider">Corrupted Samples:</div>
                                        {scanData.samples.map((s, i) => (
                                            <div key={i} className="p-2 rounded bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700">
                                                <span className="text-rose-500 font-bold">{s.word}</span> &rarr; <span className="text-emerald-600 dark:text-emerald-400">{s.fixed}</span>
                                            </div>
                                        ))}
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
                        {violationsCount > 0 && (
                            <button
                                type="button"
                                onClick={handleRepair}
                                disabled={repairing}
                                className="px-6 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-xl text-sm font-bold flex items-center gap-2 shadow-md transition-all disabled:opacity-50"
                            >
                                {repairing ? <Loader2 size={16} className="animate-spin" /> : <Wrench size={16} />}
                                <span>Repair Corrupted Characters</span>
                            </button>
                        )}
                    </div>
                </motion.div>
            </div>
        </AnimatePresence>
    );
};

export default ScriptFixModal;
