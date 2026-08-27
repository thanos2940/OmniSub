import React, { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { Sparkles, X, Check, Globe, Folder, Plus, Trash2, Shield, Search, RefreshCw, Layers, ArrowUpRight, BookOpen } from 'lucide-react';
import { api } from '../api';
import { useToast } from '../context/ToastContext';

const TYPE_COLORS = {
    character: 'bg-amber-100 dark:bg-amber-950/50 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-900',
    location: 'bg-emerald-100 dark:bg-emerald-950/50 text-emerald-800 dark:text-emerald-300 border-emerald-200 dark:border-emerald-900',
    organization: 'bg-blue-100 dark:bg-blue-950/50 text-blue-800 dark:text-blue-300 border-blue-200 dark:border-blue-900',
    object: 'bg-purple-100 dark:bg-purple-950/50 text-purple-800 dark:text-purple-300 border-purple-200 dark:border-purple-900',
    technique: 'bg-rose-100 dark:bg-rose-950/50 text-rose-800 dark:text-rose-300 border-rose-200 dark:border-rose-900',
    other: 'bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-300 border-gray-200 dark:border-gray-700',
};

const TermHarvestModal = ({
    isOpen,
    onClose,
    projectName,
    parentProjectName = null,
    episodeName = null,
    onTermsAdded,
}) => {
    const toast = useToast();
    const [loading, setLoading] = useState(false);
    const [terms, setTerms] = useState([]);
    const [selectedIndices, setSelectedIndices] = useState(new Set());
    const [filterQuery, setFilterQuery] = useState('');

    useEffect(() => {
        if (isOpen) {
            handleScan();
        }
    }, [isOpen, projectName, episodeName]);

    const handleScan = async () => {
        setLoading(true);
        setTerms([]);
        setSelectedIndices(new Set());
        try {
            let res;
            if (episodeName) {
                res = await api.harvestEpisodeTerms(projectName, episodeName);
            } else {
                res = await api.harvestProjectTerms(projectName);
            }
            const discovered = res.data?.terms || [];
            setTerms(discovered);
            // Select all by default
            setSelectedIndices(new Set(discovered.map((_, i) => i)));
            if (discovered.length === 0) {
                toast.info("No new glossary terms detected in dialogue.");
            }
        } catch (e) {
            console.error("Failed to harvest terms:", e);
            toast.error("Failed to harvest terms from dialogue.");
        } finally {
            setLoading(false);
        }
    };

    if (!isOpen) return null;

    const toggleSelect = (index) => {
        setSelectedIndices(prev => {
            const next = new Set(prev);
            if (next.has(index)) next.delete(index);
            else next.add(index);
            return next;
        });
    };

    const toggleSelectAll = () => {
        if (selectedIndices.size === filteredTerms.length) {
            setSelectedIndices(new Set());
        } else {
            setSelectedIndices(new Set(filteredTerms.map((_, i) => i)));
        }
    };

    const handleAddSelectedToProject = async () => {
        const selected = Array.from(selectedIndices).map(i => terms[i]).filter(Boolean);
        if (selected.length === 0) return;

        try {
            await onTermsAdded(selected, false);
            toast.success(`Added ${selected.length} term(s) to project glossary!`);
            onClose();
        } catch (e) {
            toast.error("Failed to add terms.");
        }
    };

    const handleAddSelectedToUniverse = async () => {
        if (!parentProjectName) return;
        const selected = Array.from(selectedIndices).map(i => terms[i]).filter(Boolean);
        if (selected.length === 0) return;

        try {
            await api.promoteTermsBatch(projectName, selected);
            toast.success(`Elevated ${selected.length} term(s) directly to ${parentProjectName} Universe!`);
            onTermsAdded(selected, true);
            onClose();
        } catch (e) {
            toast.error("Failed to elevate terms to parent universe.");
        }
    };

    const handleAddSingle = async (term, toParent = false) => {
        try {
            if (toParent && parentProjectName) {
                await api.promoteTermsBatch(projectName, [term]);
                toast.success(`Added "${term.term}" to ${parentProjectName} Universe!`);
            } else {
                await onTermsAdded([term], false);
                toast.success(`Added "${term.term}" to project glossary!`);
            }
            setTerms(prev => prev.filter(t => t.term !== term.term));
        } catch (e) {
            toast.error("Failed to add term.");
        }
    };

    const handleDismissSingle = (termName) => {
        setTerms(prev => prev.filter(t => t.term !== termName));
    };

    const filteredTerms = terms.filter(t => {
        const q = filterQuery.toLowerCase();
        return (
            t.term?.toLowerCase().includes(q) ||
            t.translation?.toLowerCase().includes(q) ||
            t.description?.toLowerCase().includes(q)
        );
    });

    return createPortal(
        <AnimatePresence>
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
                <motion.div
                    initial={{ opacity: 0, scale: 0.96, y: 10 }}
                    animate={{ opacity: 1, scale: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.96, y: 10 }}
                    className="bg-white dark:bg-gray-850 rounded-2xl shadow-2xl border border-gray-200 dark:border-gray-700 w-full max-w-3xl overflow-hidden flex flex-col max-h-[85vh]"
                >
                    {/* Header */}
                    <div className="p-5 border-b border-gray-100 dark:border-gray-750 flex items-center justify-between bg-gradient-to-r from-indigo-50/50 via-purple-50/30 to-transparent dark:from-indigo-950/20 dark:via-purple-950/10">
                        <div className="flex items-center gap-3">
                            <div className="p-2.5 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 text-white shadow-md shadow-indigo-500/20">
                                <Sparkles className="w-5 h-5" />
                            </div>
                            <div>
                                <h3 className="text-base font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                    Dialogue Term Harvester
                                </h3>
                                <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                                    {episodeName
                                        ? `Scanning dialogue for "${episodeName}"`
                                        : `Scanning dialogue across "${projectName}"`}
                                </p>
                            </div>
                        </div>
                        <div className="flex items-center gap-2">
                            <button
                                onClick={handleScan}
                                disabled={loading}
                                className="p-2 text-gray-500 hover:text-indigo-600 dark:text-gray-400 dark:hover:text-indigo-400 hover:bg-gray-100 dark:hover:bg-gray-750 rounded-xl transition-colors"
                                title="Rescan Dialogue"
                            >
                                <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                            </button>
                            <button
                                onClick={onClose}
                                className="p-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-750 rounded-xl transition-colors"
                            >
                                <X className="w-5 h-5" />
                            </button>
                        </div>
                    </div>

                    {/* Toolbar */}
                    {terms.length > 0 && !loading && (
                        <div className="px-5 py-3 border-b border-gray-100 dark:border-gray-750 bg-gray-50/50 dark:bg-gray-800/40 flex items-center justify-between gap-4">
                            <div className="flex items-center gap-3 flex-1">
                                <label className="flex items-center gap-2 cursor-pointer select-none text-xs font-semibold text-gray-600 dark:text-gray-300">
                                    <input
                                        type="checkbox"
                                        checked={selectedIndices.size === filteredTerms.length && filteredTerms.length > 0}
                                        onChange={toggleSelectAll}
                                        className="rounded text-indigo-600 focus:ring-indigo-500 w-3.5 h-3.5 border-gray-300"
                                    />
                                    <span>Select All ({filteredTerms.length})</span>
                                </label>
                                <div className="relative flex-1 max-w-xs">
                                    <Search className="w-3.5 h-3.5 text-gray-400 absolute left-2.5 top-2.5" />
                                    <input
                                        type="text"
                                        placeholder="Filter terms..."
                                        value={filterQuery}
                                        onChange={(e) => setFilterQuery(e.target.value)}
                                        className="w-full pl-8 pr-3 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 focus:ring-1 focus:ring-indigo-500 text-gray-800 dark:text-gray-200"
                                    />
                                </div>
                            </div>

                            <div className="flex items-center gap-2">
                                <button
                                    onClick={handleAddSelectedToProject}
                                    disabled={selectedIndices.size === 0}
                                    className="px-3 py-1.5 text-xs font-bold text-indigo-700 dark:text-indigo-300 bg-indigo-50 dark:bg-indigo-950/60 hover:bg-indigo-100 rounded-lg border border-indigo-200 dark:border-indigo-800 transition-colors disabled:opacity-50 flex items-center gap-1.5 shadow-sm"
                                >
                                    <Plus className="w-3.5 h-3.5" /> Add Selected ({selectedIndices.size})
                                </button>
                                {parentProjectName && (
                                    <button
                                        onClick={handleAddSelectedToUniverse}
                                        disabled={selectedIndices.size === 0}
                                        className="px-3 py-1.5 text-xs font-bold text-purple-700 dark:text-purple-300 bg-purple-50 dark:bg-purple-950/60 hover:bg-purple-100 rounded-lg border border-purple-200 dark:border-purple-800 transition-colors disabled:opacity-50 flex items-center gap-1.5 shadow-sm"
                                    >
                                        <Globe className="w-3.5 h-3.5" /> Elevate to Universe ({selectedIndices.size})
                                    </button>
                                )}
                            </div>
                        </div>
                    )}

                    {/* Content List */}
                    <div className="p-5 overflow-y-auto space-y-3 flex-1">
                        {loading ? (
                            <div className="py-16 flex flex-col items-center justify-center text-center">
                                <div className="p-4 rounded-2xl bg-indigo-50 dark:bg-indigo-950/50 text-indigo-600 dark:text-indigo-400 mb-4 animate-pulse">
                                    <Sparkles className="w-8 h-8 animate-spin" />
                                </div>
                                <h4 className="font-bold text-gray-800 dark:text-gray-200 text-sm">Extracting New Terminology...</h4>
                                <p className="text-xs text-gray-400 max-w-sm mt-1">
                                    Scanning dialogue for recurring names, locations, organizations, and specialized lore terms.
                                </p>
                            </div>
                        ) : filteredTerms.length === 0 ? (
                            <div className="py-16 text-center text-gray-400">
                                <BookOpen className="w-10 h-10 mx-auto mb-2 opacity-40" />
                                <p className="text-sm font-semibold text-gray-500 dark:text-gray-400">No new candidate terms found</p>
                                <p className="text-xs mt-1">All detected entities are already registered in your glossaries or no unique terminology was found.</p>
                            </div>
                        ) : (
                            filteredTerms.map((item, idx) => {
                                const isSelected = selectedIndices.has(idx);
                                const typeClass = TYPE_COLORS[item.type] || TYPE_COLORS.other;

                                return (
                                    <div
                                        key={idx}
                                        className={`p-4 rounded-xl border transition-all ${
                                            isSelected
                                                ? 'border-indigo-300 dark:border-indigo-700 bg-indigo-50/30 dark:bg-indigo-950/20 shadow-sm'
                                                : 'border-gray-200 dark:border-gray-700/80 bg-white dark:bg-gray-800/60 hover:border-gray-300'
                                        }`}
                                    >
                                        <div className="flex items-start gap-3">
                                            <input
                                                type="checkbox"
                                                checked={isSelected}
                                                onChange={() => toggleSelect(idx)}
                                                className="mt-1 rounded text-indigo-600 focus:ring-indigo-500 w-3.5 h-3.5 border-gray-300"
                                            />
                                            <div className="flex-1">
                                                <div className="flex flex-wrap items-center justify-between gap-2">
                                                    <div className="flex items-center gap-2">
                                                        <span className="font-bold text-sm text-gray-900 dark:text-white">
                                                            {item.term}
                                                        </span>
                                                        <span className="text-gray-400 text-xs">→</span>
                                                        <span className="font-bold text-sm text-indigo-600 dark:text-indigo-400">
                                                            {item.translation}
                                                        </span>
                                                    </div>

                                                    <div className="flex items-center gap-1.5">
                                                        <span className={`text-[10px] font-bold px-2 py-0.5 rounded-md uppercase tracking-wider border ${typeClass}`}>
                                                            {item.type}
                                                        </span>
                                                        {item.gender && item.gender !== 'n/a' && (
                                                            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
                                                                {item.gender}
                                                            </span>
                                                        )}
                                                    </div>
                                                </div>

                                                {item.description && (
                                                    <p className="text-xs text-gray-600 dark:text-gray-300 mt-1">
                                                        {item.description}
                                                    </p>
                                                )}

                                                {item.context_snippet && (
                                                    <div className="mt-2 p-2 rounded-lg bg-gray-50 dark:bg-gray-800/80 border border-gray-150 dark:border-gray-750 text-[11px] text-gray-500 dark:text-gray-400 italic">
                                                        "{item.context_snippet}"
                                                    </div>
                                                )}
                                            </div>

                                            {/* Quick Actions */}
                                            <div className="flex items-center gap-1 flex-shrink-0 self-center">
                                                <button
                                                    onClick={() => handleAddSingle(item, false)}
                                                    className="p-1.5 text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-950/50 rounded-lg transition-colors border border-transparent hover:border-indigo-200"
                                                    title="Add to Project"
                                                >
                                                    <Plus className="w-4 h-4" />
                                                </button>
                                                {parentProjectName && (
                                                    <button
                                                        onClick={() => handleAddSingle(item, true)}
                                                        className="p-1.5 text-xs font-semibold text-purple-600 dark:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-950/50 rounded-lg transition-colors border border-transparent hover:border-purple-200"
                                                        title={`Elevate to ${parentProjectName} Universe`}
                                                    >
                                                        <Globe className="w-4 h-4" />
                                                    </button>
                                                )}
                                                <button
                                                    onClick={() => handleDismissSingle(item.term)}
                                                    className="p-1.5 text-xs font-semibold text-gray-400 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/40 rounded-lg transition-colors"
                                                    title="Dismiss"
                                                >
                                                    <X className="w-4 h-4" />
                                                </button>
                                            </div>
                                        </div>
                                    </div>
                                );
                            })
                        )}
                    </div>

                    {/* Footer */}
                    <div className="p-4 border-t border-gray-100 dark:border-gray-750 bg-gray-50/80 dark:bg-gray-800/80 flex items-center justify-between">
                        <div className="text-xs text-gray-500 dark:text-gray-400">
                            Found <strong>{terms.length}</strong> candidate terms
                        </div>
                        <button
                            onClick={onClose}
                            className="px-5 py-2 text-xs font-bold text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-xl transition-colors"
                        >
                            Done
                        </button>
                    </div>
                </motion.div>
            </div>
        </AnimatePresence>,
        document.body
    );
};

export default TermHarvestModal;
