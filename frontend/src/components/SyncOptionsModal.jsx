import React, { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { RefreshCw, X, Zap, Layers, FileText, CheckCircle2, Film, Tv, HelpCircle, HardDrive } from 'lucide-react';

const STORAGE_KEY = 'omnisub_sync_options';

const getSavedOptions = () => {
    try {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved) return JSON.parse(saved);
    } catch (e) {
        // ignore
    }
    return {
        scan_ass: true,
        extract_embedded_ass: false,
        source: 'both',
    };
};

const SyncOptionsModal = ({
    isOpen,
    onClose,
    onConfirm,
    title = "Initiate Media Scan",
    description = "Configure scan depth and subtitle format detection before scanning.",
    isProjectSpecific = false,
    projectName = null,
}) => {
    const [options, setOptions] = useState(getSavedOptions);
    const [saveDefault, setSaveDefault] = useState(true);

    useEffect(() => {
        if (isOpen) {
            setOptions(getSavedOptions());
        }
    }, [isOpen]);

    if (!isOpen) return null;

    const handlePreset = (preset) => {
        if (preset === 'fast') {
            setOptions(prev => ({ ...prev, scan_ass: false, extract_embedded_ass: false }));
        } else if (preset === 'standard') {
            setOptions(prev => ({ ...prev, scan_ass: true, extract_embedded_ass: false }));
        } else if (preset === 'deep') {
            setOptions(prev => ({ ...prev, scan_ass: true, extract_embedded_ass: true }));
        }
    };

    const isFast = !options.scan_ass && !options.extract_embedded_ass;
    const isStandard = options.scan_ass && !options.extract_embedded_ass;
    const isDeep = options.scan_ass && options.extract_embedded_ass;

    const handleStart = () => {
        if (saveDefault) {
            try {
                localStorage.setItem(STORAGE_KEY, JSON.stringify(options));
            } catch (e) {
                // ignore
            }
        }
        onConfirm(options);
        onClose();
    };

    return createPortal(
        <AnimatePresence>
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
                <motion.div
                    initial={{ opacity: 0, scale: 0.96, y: 10 }}
                    animate={{ opacity: 1, scale: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.96, y: 10 }}
                    transition={{ duration: 0.15 }}
                    className="bg-white dark:bg-gray-850 rounded-2xl shadow-2xl border border-gray-200 dark:border-gray-700 w-full max-w-lg overflow-hidden flex flex-col"
                >
                    {/* Header */}
                    <div className="p-5 border-b border-gray-100 dark:border-gray-750 flex items-center justify-between bg-gray-50/50 dark:bg-gray-800/40">
                        <div className="flex items-center gap-3">
                            <div className="p-2.5 rounded-xl bg-indigo-50 dark:bg-indigo-950/60 text-indigo-600 dark:text-indigo-400 border border-indigo-100 dark:border-indigo-900/50">
                                <RefreshCw className="w-5 h-5" />
                            </div>
                            <div>
                                <h3 className="text-base font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                    {title}
                                </h3>
                                <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                                    {projectName ? `Scanning media files for "${projectName}"` : description}
                                </p>
                            </div>
                        </div>
                        <button
                            onClick={onClose}
                            className="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-750 transition-colors"
                        >
                            <X className="w-5 h-5" />
                        </button>
                    </div>

                    {/* Body */}
                    <div className="p-5 space-y-5 overflow-y-auto max-h-[75vh]">
                        {/* Scope Selector (Only for global sync) */}
                        {!isProjectSpecific && (
                            <div>
                                <label className="text-xs font-bold text-gray-600 dark:text-gray-300 uppercase tracking-wider block mb-2">
                                    Target Services
                                </label>
                                <div className="grid grid-cols-3 gap-2">
                                    {[
                                        { id: 'both', label: 'All Services', icon: Layers },
                                        { id: 'sonarr', label: 'Sonarr (TV)', icon: Tv },
                                        { id: 'radarr', label: 'Radarr (Movies)', icon: Film },
                                    ].map(({ id, label, icon: Icon }) => (
                                        <button
                                            key={id}
                                            type="button"
                                            onClick={() => setOptions(prev => ({ ...prev, source: id }))}
                                            className={`p-2.5 rounded-xl border text-xs font-semibold flex flex-col items-center gap-1.5 transition-all ${
                                                options.source === id
                                                    ? 'border-indigo-600 bg-indigo-50/50 dark:bg-indigo-950/30 text-indigo-700 dark:text-indigo-300 shadow-sm'
                                                    : 'border-gray-200 dark:border-gray-700 text-gray-650 dark:text-gray-400 hover:border-gray-300'
                                            }`}
                                        >
                                            <Icon className="w-4 h-4" />
                                            <span>{label}</span>
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}

                        {/* Presets */}
                        <div>
                            <label className="text-xs font-bold text-gray-600 dark:text-gray-300 uppercase tracking-wider block mb-2">
                                Scan Speed Presets
                            </label>
                            <div className="grid grid-cols-3 gap-2">
                                <button
                                    type="button"
                                    onClick={() => handlePreset('fast')}
                                    className={`p-2.5 rounded-xl border text-xs font-semibold flex flex-col items-center gap-1 transition-all ${
                                        isFast
                                            ? 'border-emerald-500 bg-emerald-50/50 dark:bg-emerald-950/30 text-emerald-700 dark:text-emerald-300 shadow-sm'
                                            : 'border-gray-200 dark:border-gray-700 text-gray-650 dark:text-gray-400 hover:border-gray-300'
                                    }`}
                                >
                                    <div className="flex items-center gap-1 font-bold">
                                        <Zap className="w-3.5 h-3.5 text-emerald-500" /> Fast
                                    </div>
                                    <span className="text-[10px] opacity-75">.srt only</span>
                                </button>

                                <button
                                    type="button"
                                    onClick={() => handlePreset('standard')}
                                    className={`p-2.5 rounded-xl border text-xs font-semibold flex flex-col items-center gap-1 transition-all ${
                                        isStandard
                                            ? 'border-indigo-600 bg-indigo-50/50 dark:bg-indigo-950/30 text-indigo-700 dark:text-indigo-300 shadow-sm'
                                            : 'border-gray-200 dark:border-gray-700 text-gray-650 dark:text-gray-400 hover:border-gray-300'
                                    }`}
                                >
                                    <div className="flex items-center gap-1 font-bold">
                                        <FileText className="w-3.5 h-3.5 text-indigo-500" /> Standard
                                    </div>
                                    <span className="text-[10px] opacity-75">.srt + .ass files</span>
                                </button>

                                <button
                                    type="button"
                                    onClick={() => handlePreset('deep')}
                                    className={`p-2.5 rounded-xl border text-xs font-semibold flex flex-col items-center gap-1 transition-all ${
                                        isDeep
                                            ? 'border-purple-600 bg-purple-50/50 dark:bg-purple-950/30 text-purple-700 dark:text-purple-300 shadow-sm'
                                            : 'border-gray-200 dark:border-gray-700 text-gray-650 dark:text-gray-400 hover:border-gray-300'
                                    }`}
                                >
                                    <div className="flex items-center gap-1 font-bold">
                                        <HardDrive className="w-3.5 h-3.5 text-purple-500" /> Deep Scan
                                    </div>
                                    <span className="text-[10px] opacity-75">Probe MKV containers</span>
                                </button>
                            </div>
                        </div>

                        {/* Granular Toggles */}
                        <div className="space-y-3 pt-1">
                            <label className="text-xs font-bold text-gray-600 dark:text-gray-300 uppercase tracking-wider block">
                                Subtitle Format & Extraction Settings
                            </label>

                            {/* Toggle 1: Look for .ass on disk */}
                            <label className="flex items-start gap-3 p-3 rounded-xl border border-gray-200 dark:border-gray-700 hover:bg-gray-50/50 dark:hover:bg-gray-800/50 cursor-pointer transition-colors">
                                <input
                                    type="checkbox"
                                    checked={options.scan_ass}
                                    onChange={(e) => {
                                        const checked = e.target.checked;
                                        setOptions(prev => ({
                                            ...prev,
                                            scan_ass: checked,
                                            extract_embedded_ass: checked ? prev.extract_embedded_ass : false,
                                        }));
                                    }}
                                    className="mt-0.5 w-4 h-4 rounded text-indigo-600 focus:ring-indigo-500 border-gray-300"
                                />
                                <div className="flex-1">
                                    <div className="text-xs font-bold text-gray-800 dark:text-gray-200 flex items-center gap-1.5">
                                        Look for .ass / .ssa subtitles on disk
                                        {options.scan_ass && <span className="text-[10px] px-1.5 py-0.2 rounded bg-indigo-100 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300 font-semibold">Enabled</span>}
                                    </div>
                                    <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-0.5 leading-relaxed">
                                        Searches for external styled <code>.ass</code> and <code>.ssa</code> files next to video files. Uncheck if your library only uses standard <code>.srt</code> for maximum speed.
                                    </p>
                                </div>
                            </label>

                            {/* Toggle 2: Probe video containers for embedded ASS & SRT */}
                            <label className={`flex items-start gap-3 p-3 rounded-xl border transition-colors ${
                                options.scan_ass
                                    ? 'border-gray-200 dark:border-gray-700 hover:bg-gray-50/50 dark:hover:bg-gray-800/50 cursor-pointer'
                                    : 'border-gray-150 dark:border-gray-800 bg-gray-50/40 dark:bg-gray-900/40 opacity-60 cursor-not-allowed'
                            }`}>
                                <input
                                    type="checkbox"
                                    disabled={!options.scan_ass}
                                    checked={options.extract_embedded_ass}
                                    onChange={(e) => setOptions(prev => ({ ...prev, extract_embedded_ass: e.target.checked }))}
                                    className="mt-0.5 w-4 h-4 rounded text-indigo-600 focus:ring-indigo-500 border-gray-300 disabled:opacity-50"
                                />
                                <div className="flex-1">
                                    <div className="text-xs font-bold text-gray-800 dark:text-gray-200 flex items-center gap-1.5">
                                        Probe video files for embedded subtitles (.ass and .srt)
                                        {options.extract_embedded_ass && <span className="text-[10px] px-1.5 py-0.2 rounded bg-purple-100 dark:bg-purple-950 text-purple-700 dark:text-purple-300 font-semibold">Container Probe</span>}
                                    </div>
                                    <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-0.5 leading-relaxed">
                                        Uses <code>ffprobe</code> to probe video containers when no external subtitle is found on disk and extract muxed text tracks (<code>.ass</code> or <code>.srt</code>).
                                    </p>
                                </div>
                            </label>
                        </div>

                        {/* Save preference */}
                        <div className="flex items-center gap-2 pt-1 border-t border-gray-100 dark:border-gray-750">
                            <input
                                type="checkbox"
                                id="saveSyncDefault"
                                checked={saveDefault}
                                onChange={(e) => setSaveDefault(e.target.checked)}
                                className="w-3.5 h-3.5 rounded text-indigo-600 focus:ring-indigo-500 border-gray-300"
                            />
                            <label htmlFor="saveSyncDefault" className="text-xs text-gray-500 dark:text-gray-400 cursor-pointer select-none">
                                Remember these options as my default for future scans
                            </label>
                        </div>
                    </div>

                    {/* Footer */}
                    <div className="p-4 bg-gray-50/80 dark:bg-gray-800/80 border-t border-gray-100 dark:border-gray-750 flex items-center justify-between">
                        <div className="text-xs text-gray-500 dark:text-gray-400">
                            Mode:{' '}
                            <strong className={isFast ? 'text-emerald-600 dark:text-emerald-400' : isDeep ? 'text-purple-600 dark:text-purple-400' : 'text-indigo-600 dark:text-indigo-400'}>
                                {isFast ? 'Fast (.srt only)' : isDeep ? 'Deep (Embedded .ass)' : 'Standard (.ass files)'}
                            </strong>
                        </div>
                        <div className="flex items-center gap-2">
                            <button
                                type="button"
                                onClick={onClose}
                                className="px-4 py-2 text-xs font-semibold text-gray-650 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-xl transition-colors"
                            >
                                Cancel
                            </button>
                            <button
                                type="button"
                                onClick={handleStart}
                                className="px-5 py-2 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-700 rounded-xl shadow-md flex items-center gap-2 transition-all"
                            >
                                <RefreshCw className="w-3.5 h-3.5" /> Start Scan
                            </button>
                        </div>
                    </div>
                </motion.div>
            </div>
        </AnimatePresence>,
        document.body
    );
};

export default SyncOptionsModal;
