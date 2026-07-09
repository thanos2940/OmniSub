import React, { useState, useEffect } from 'react';
import { Check, Save, Sparkles, AlertTriangle, ArrowRight, Ban, RefreshCw } from 'lucide-react';
import { api } from '../api';
import { useToast } from '../context/ToastContext';

const ReviewLineResolver = ({
    projectName,
    episodeName,
    line,
    onResolved,
    onUpdateLineText,
    onSkip
}) => {
    const [text, setText] = useState(line.translated || '');
    const [conformance, setConformance] = useState(null);
    const [measuring, setMeasuring] = useState(false);
    const [promoting, setPromoting] = useState(false);
    const [blacklisting, setBlacklisting] = useState(false);
    const toast = useToast();

    useEffect(() => {
        // Reset when switching lines OR when an external update changes the incoming
        // translation. This won't clobber active edits: the textarea's onChange echoes
        // every keystroke back to the parent via onUpdateLineText, so line.translated
        // already tracks local text and setText becomes a no-op for user-driven changes.
        setText(line.translated || '');
    }, [line.index, line.episode, line.translated]);

    useEffect(() => {
        const delayDebounce = setTimeout(() => {
            measureConformance();
        }, 300);

        return () => clearTimeout(delayDebounce);
    }, [text]);

    const measureConformance = async () => {
        if (!text.trim()) {
            setConformance(null);
            return;
        }
        setMeasuring(true);
        try {
            const res = await api.measureConformance(projectName, text, line.timecode);
            setConformance(res.data);
        } catch (err) {
            console.error("Conformance check failed", err);
        } finally {
            setMeasuring(false);
        }
    };

    const [saving, setSaving] = useState(false);
    const [resolving, setResolving] = useState(false);

    const handleSaveOnly = async () => {
        setSaving(true);
        try {
            const activeEp = episodeName || line.episode;
            await api.saveReviewLine(projectName, activeEp, line.index, text);
            if (onUpdateLineText) {
                onUpdateLineText(line.index, text);
            }
            toast.success("Translation saved");
        } catch (err) {
            console.error("Failed to save", err);
            toast.error("Failed to save translation.");
        } finally {
            setSaving(false);
        }
    };

    const handleResolve = async () => {
        setResolving(true);
        try {
            const activeEp = episodeName || line.episode;
            await api.resolveReviewItem(projectName, activeEp, line.index, text);
            toast.success("Saved and resolved");
            if (onResolved) {
                onResolved(line.index, text);
            }
        } catch (err) {
            console.error("Failed to resolve", err);
            toast.error("Failed to resolve item.");
        } finally {
            setResolving(false);
        }
    };

    const handlePromote = async () => {
        const term = window.prompt("Enter English term/phrase to add to glossary:", line.original);
        if (!term) return;
        setPromoting(true);
        try {
            await api.acceptFeedbackSuggestion(projectName, {
                term: term.trim(),
                translation: text.trim(),
                type: "other",
                gender: "n/a"
            });
            toast.success(`Added "${term}" to glossary!`);
        } catch (err) {
            console.error(err);
            toast.error("Failed to add to glossary");
        } finally {
            setPromoting(false);
        }
    };

    const handleBlacklist = async () => {
        if (!window.confirm("Blacklist current translation and trigger a re-translation retry for this line?")) return;
        setBlacklisting(true);
        try {
            const activeEp = episodeName || line.episode;
            await api.blacklistAndRetry(projectName, activeEp, {
                line_index: line.index,
                bad_target: line.translated || '',
                source_text: line.original,
                reason: "User flagged: blacklisted and retried"
            });
            toast.success("Blacklisted translation and enqueued retry job!");
            if (onResolved) {
                onResolved(line.index, text); // Treat as completed/resolved locally
            }
        } catch (err) {
            console.error(err);
            toast.error("Failed to trigger blacklist retry");
        } finally {
            setBlacklisting(false);
        }
    };

    return (
        <div className="bg-slate-50 dark:bg-gray-900/40 p-5 rounded-2xl border border-gray-200 dark:border-gray-700/60 shadow-inner">
            <div className="flex flex-col gap-4">
                {/* Meta details */}
                <div className="flex justify-between items-center text-xs text-gray-500 dark:text-gray-400">
                    <span className="font-mono bg-white dark:bg-gray-800 px-2.5 py-1 rounded-lg border border-gray-200 dark:border-gray-700">
                        Timecode: {line.timecode}
                    </span>
                    <span className="font-semibold text-rose-500 flex items-center gap-1">
                        <AlertTriangle size={14} />
                        {line.review_issues || "Manual review flagged"}
                    </span>
                </div>

                {/* Subtitle text inputs side-by-side */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {/* Source */}
                    <div className="bg-white dark:bg-gray-800 p-4 rounded-xl border border-gray-200 dark:border-gray-700 flex flex-col gap-2">
                        <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Source (English)</span>
                        <p className="text-gray-800 dark:text-gray-200 text-sm leading-relaxed whitespace-pre-wrap">{line.original}</p>
                    </div>

                    {/* Target Translation */}
                    <div className="bg-indigo-50/20 dark:bg-indigo-950/5 p-4 rounded-xl border border-indigo-100 dark:border-indigo-900/30 flex flex-col gap-2 relative">
                        <span className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider">Target Translation</span>
                        <textarea
                            value={text}
                            onChange={(e) => {
                                setText(e.target.value);
                                if (onUpdateLineText) {
                                    onUpdateLineText(line.index, e.target.value);
                                }
                            }}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter' && e.ctrlKey) {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    handleResolve();
                                }
                            }}
                            className="w-full min-h-[70px] bg-white dark:bg-gray-800 p-3 rounded-lg border border-gray-200 dark:border-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 text-gray-900 dark:text-white text-sm font-medium resize-y"
                            placeholder="Type translation here..."
                        />
                    </div>
                </div>

                {/* Conformance Live Meter */}
                {conformance && conformance.metrics && (
                    <div className="bg-white dark:bg-gray-800/80 p-4 rounded-xl border border-gray-100 dark:border-gray-800 flex flex-wrap items-center gap-6 justify-between transition-all">
                        <div className="flex items-center gap-2">
                            <span className="text-xs font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider">Live Conformance:</span>
                            {conformance.is_conformant ? (
                                <span className="bg-emerald-50 dark:bg-emerald-950/20 text-emerald-700 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-900 text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wide">
                                    Conformant
                                </span>
                            ) : (
                                <span className="bg-rose-50 dark:bg-rose-950/20 text-rose-700 dark:text-rose-400 border border-rose-200 dark:border-rose-900 text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wide">
                                    Out of limits
                                </span>
                            )}
                        </div>

                        <div className="flex items-center gap-6 text-xs">
                            {/* CPS */}
                            <div className="flex flex-col">
                                <span className="text-gray-400 text-[10px] uppercase font-bold tracking-wider">CPS</span>
                                <span className={`font-mono text-sm font-bold ${conformance.metrics.cps > conformance.limits.max_cps ? 'text-rose-500' : 'text-emerald-500'}`}>
                                    {conformance.metrics.cps} / {conformance.limits.max_cps}
                                </span>
                            </div>

                            {/* Line Length */}
                            <div className="flex flex-col">
                                <span className="text-gray-400 text-[10px] uppercase font-bold tracking-wider">Max Line Length</span>
                                <span className={`font-mono text-sm font-bold ${conformance.metrics.max_line_len > conformance.limits.max_chars_per_line ? 'text-rose-500' : 'text-emerald-500'}`}>
                                    {conformance.metrics.max_line_len} / {conformance.limits.max_chars_per_line}
                                </span>
                            </div>

                            {/* Line Count */}
                            <div className="flex flex-col">
                                <span className="text-gray-400 text-[10px] uppercase font-bold tracking-wider">Line Count</span>
                                <span className={`font-mono text-sm font-bold ${conformance.metrics.line_count > conformance.limits.max_lines ? 'text-rose-500' : 'text-emerald-500'}`}>
                                    {conformance.metrics.line_count} / {conformance.limits.max_lines}
                                </span>
                            </div>
                        </div>

                        {conformance.issues.length > 0 && (
                            <div className="w-full mt-2 text-xs text-rose-500 bg-rose-50/50 dark:bg-rose-950/10 p-2.5 rounded-lg border border-rose-100/30 flex items-center gap-2">
                                <AlertTriangle size={14} className="flex-shrink-0" />
                                <span>Issues: {conformance.issues.join(", ")}</span>
                            </div>
                        )}
                    </div>
                )}

                {/* Actions */}
                <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
                    <div className="flex items-center gap-2">
                        <button
                            onClick={handleSaveOnly}
                            disabled={saving}
                            className="flex items-center gap-1.5 px-3 py-2 bg-white dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-xl text-xs font-semibold border border-gray-200 dark:border-gray-700 transition-all active:scale-[0.98] disabled:opacity-50"
                        >
                            <Save size={14} /> {saving ? 'Saving...' : 'Save'}
                        </button>
                        <button
                            onClick={handlePromote}
                            disabled={promoting}
                            className="flex items-center gap-1.5 px-3 py-2 bg-indigo-50 dark:bg-indigo-950/20 hover:bg-indigo-100 dark:hover:bg-indigo-900/40 text-indigo-700 dark:text-indigo-400 rounded-xl text-xs font-semibold border border-indigo-100/50 dark:border-indigo-900/30 transition-all active:scale-[0.98]"
                        >
                            <Sparkles size={14} /> Promote to Glossary
                        </button>
                        <button
                            onClick={handleBlacklist}
                            disabled={blacklisting}
                            className="flex items-center gap-1.5 px-3 py-2 bg-rose-50 dark:bg-rose-950/20 hover:bg-rose-100 dark:hover:bg-rose-900/40 text-rose-700 dark:text-rose-400 rounded-xl text-xs font-semibold border border-rose-100/50 dark:border-rose-900/30 transition-all active:scale-[0.98]"
                        >
                            <Ban size={14} /> {blacklisting ? 'Retrying...' : 'Blacklist + Retry'}
                        </button>
                    </div>

                    <div className="flex items-center gap-2">
                        {onSkip && (
                            <button
                                onClick={onSkip}
                                className="px-4 py-2 hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 rounded-xl text-xs font-semibold transition-all active:scale-[0.98]"
                            >
                                Skip
                            </button>
                        )}
                        <button
                            onClick={handleResolve}
                            disabled={resolving}
                            className="flex items-center gap-1.5 px-5 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl text-xs font-bold transition-all shadow-md shadow-emerald-600/10 active:scale-[0.98] disabled:opacity-50"
                        >
                            <Check size={14} /> {resolving ? 'Resolving...' : 'Accept & Resolve'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default ReviewLineResolver;
