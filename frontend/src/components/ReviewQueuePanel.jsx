import React, { useState, useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ShieldAlert, Check, CheckCircle, RefreshCw, AlertTriangle, ChevronRight, BookOpen, Plus, Filter, X } from 'lucide-react';
import { api } from '../api';
import { useToast } from '../context/ToastContext';
import ReviewLineResolver from './ReviewLineResolver';

const ReviewQueuePanel = ({ projectName }) => {
    const [searchParams, setSearchParams] = useSearchParams();
    const queryProject = searchParams.get('project');
    const queryEpisode = searchParams.get('episode');
    const effectiveProject = projectName || queryProject;

    const [activeEpisodeFilter, setActiveEpisodeFilter] = useState(queryEpisode || null);
    const [queue, setQueue] = useState([]);
    const [loading, setLoading] = useState(true);
    const [resolvingId, setResolvingId] = useState(null);
    const [resolvingAll, setResolvingAll] = useState(false);
    const [activeIndex, setActiveIndex] = useState(0);
    const [suggestions, setSuggestions] = useState([]);
    const toast = useToast();

    useEffect(() => {
        setActiveEpisodeFilter(queryEpisode || null);
    }, [queryEpisode]);

    const handleClearEpisodeFilter = () => {
        setActiveEpisodeFilter(null);
        const nextParams = new URLSearchParams(searchParams);
        nextParams.delete('episode');
        setSearchParams(nextParams, { replace: true });
    };

    const loadQueue = async () => {
        setLoading(true);
        try {
            const res = effectiveProject 
                ? await api.getReviewQueue(effectiveProject, activeEpisodeFilter)
                : await api.getGlobalReviewQueue(activeEpisodeFilter ? { episode: activeEpisodeFilter } : {});
            setQueue(res.data?.items || []);
            setActiveIndex(0);
        } catch (err) {
            console.error("Failed to load review queue", err);
        } finally {
            setLoading(false);
        }
    };

    const loadSuggestions = async () => {
        if (!effectiveProject) return;
        try {
            const res = await api.getFeedbackSuggestions(effectiveProject);
            setSuggestions(res.data?.suggestions || []);
        } catch (err) {
            console.error("Failed to load suggestions", err);
        }
    };

    useEffect(() => {
        loadQueue();
        loadSuggestions();
    }, [effectiveProject, activeEpisodeFilter]);

    const displayedQueue = useMemo(() => {
        if (!activeEpisodeFilter) return queue;
        return queue.filter(item => item.episode === activeEpisodeFilter);
    }, [queue, activeEpisodeFilter]);

    // Keyboard navigation listener
    useEffect(() => {
        if (displayedQueue.length === 0) return;

        const handleKeyDown = (e) => {
            if (displayedQueue.length === 0) return;

            const isTypingField = document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA';
            if (isTypingField && (e.key === 'j' || e.key === 'k' || e.key === 'b')) {
                return;
            }

            if (e.key === 'j') {
                e.preventDefault();
                setActiveIndex(prev => Math.min(displayedQueue.length - 1, prev + 1));
            } else if (e.key === 'k') {
                e.preventDefault();
                setActiveIndex(prev => Math.max(0, prev - 1));
            } else if (e.key === 'Enter' && e.ctrlKey) {
                e.preventDefault();
                const activeItem = displayedQueue[activeIndex];
                if (activeItem) {
                    handleResolve(activeItem.project_name, activeItem.episode, activeItem.index);
                }
            } else if (e.key === 'b') {
                e.preventDefault();
                const activeItem = displayedQueue[activeIndex];
                if (activeItem) {
                    if (window.confirm(`Blacklist current translation for line ${activeItem.index + 1} and retry?`)) {
                        const targetProj = activeItem.project_name || effectiveProject;
                        api.blacklistAndRetry(targetProj, activeItem.episode, {
                            line_index: activeItem.index,
                            bad_target: activeItem.translated,
                            source_text: activeItem.original,
                            reason: "Keyboard shortcut blacklist"
                        }).then(() => {
                            toast.success("Blacklisted and retrying...");
                            setQueue(prev => prev.filter(it => !(
                                (it.project_name || effectiveProject) === targetProj && 
                                it.episode === activeItem.episode && 
                                it.index === activeItem.index
                            )));
                            setActiveIndex(prev => Math.max(0, Math.min(displayedQueue.length - 2, prev)));
                        }).catch(err => {
                            console.error("Failed to blacklist and retry", err);
                            toast.error("Failed to blacklist and retry.");
                        });
                    }
                }
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [displayedQueue, activeIndex, effectiveProject, toast]);

    useEffect(() => {
        const activeElement = document.getElementById(`queue-item-${activeIndex}`);
        if (activeElement) {
            activeElement.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    }, [activeIndex]);

    const handleResolve = async (itemProjectName, episode, index) => {
        const targetProj = itemProjectName || effectiveProject;
        setResolvingId(`${targetProj}-${episode}-${index}`);
        try {
            const activeItem = queue.find(item => 
                (item.project_name || effectiveProject) === targetProj && 
                item.episode === episode && 
                item.index === index
            );
            const text = activeItem ? activeItem.translated : "";
            await api.resolveReviewItem(targetProj, episode, index, text);
            toast.success("Item resolved");
            
            // Remove from queue
            setQueue(prev => prev.filter(item => !(
                (item.project_name || effectiveProject) === targetProj && 
                item.episode === episode && 
                item.index === index
            )));
            setActiveIndex(prev => Math.max(0, Math.min(displayedQueue.length - 2, prev)));
        } catch (err) {
            console.error("Failed to resolve item", err);
            toast.error("Failed to resolve item.");
        } finally {
            setResolvingId(null);
        }
    };

    const handleResolveAll = async () => {
        if (!effectiveProject) return;
        if (!window.confirm("Are you sure you want to dismiss all pending review items?")) return;
        setResolvingAll(true);
        try {
            await api.resolveAllReviewItems(effectiveProject);
            toast.success("All items resolved");
            setQueue([]);
        } catch (err) {
            console.error("Failed to resolve all", err);
            toast.error("Failed to resolve all items.");
        } finally {
            setResolvingAll(false);
        }
    };

    const handleLocalLineUpdate = (arg1, arg2, arg3, arg4) => {
        let episodeName, lineIndex, newText, projectName;
        if (typeof arg1 === 'string' && typeof arg2 === 'number') {
            episodeName = arg1;
            lineIndex = arg2;
            newText = arg3;
            projectName = arg4;
        } else {
            lineIndex = arg1;
            newText = arg2;
        }

        setQueue(prev => prev.map(item => {
            const matchesIndex = item.index === lineIndex;
            const matchesEp = !episodeName || item.episode === episodeName;
            const matchesProj = !projectName || !item.project_name || item.project_name === projectName;
            return (matchesIndex && matchesEp && matchesProj) ? { ...item, translated: newText } : item;
        }));
    };

    const handleAcceptSuggestion = async (sug) => {
        if (!effectiveProject) return;
        try {
            await api.acceptFeedbackSuggestion(effectiveProject, {
                term: sug.term,
                translation: sug.suggested_translation,
                type: "other",
                gender: "n/a"
            });
            toast.success(`Added "${sug.term}" to glossary!`);
            setSuggestions(prev => prev.filter(s => s.term !== sug.term));
        } catch (err) {
            toast.error("Failed to accept suggestion");
        }
    };

    if (loading) {
        return (
            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-8 text-center">
                <RefreshCw className="w-8 h-8 mx-auto text-rose-400 animate-spin mb-4" />
                <p className="text-gray-500">Loading Review Queue...</p>
            </div>
        );
    }

    return (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Main Review Queue */}
            <div className={`bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden shadow-sm ${effectiveProject ? 'lg:col-span-2' : 'lg:col-span-3'}`}>
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 flex justify-between items-center">
                    <div className="flex flex-col">
                        <h3 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                            <ShieldAlert size={20} className="text-rose-500" /> Review Queue
                            <span className="text-sm font-normal text-gray-500 ml-2">({displayedQueue.length} pending)</span>
                        </h3>
                        <p className="text-[10px] text-gray-400 mt-1 hidden sm:block">
                            Keyboard shortcuts: <kbd className="px-1.5 py-0.5 rounded bg-gray-100 border text-gray-600 font-mono text-[9px]">j</kbd> next, <kbd className="px-1.5 py-0.5 rounded bg-gray-100 border text-gray-600 font-mono text-[9px]">k</kbd> prev, <kbd className="px-1.5 py-0.5 rounded bg-gray-100 border text-gray-600 font-mono text-[9px]">ctrl+enter</kbd> resolve, <kbd className="px-1.5 py-0.5 rounded bg-gray-100 border text-gray-600 font-mono text-[9px]">b</kbd> blacklist
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        {effectiveProject && displayedQueue.length > 0 && (
                            <button
                                onClick={handleResolveAll}
                                disabled={resolvingAll}
                                className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 rounded-lg font-medium transition-colors disabled:opacity-50"
                            >
                                <CheckCircle size={14} /> Resolve All
                            </button>
                        )}
                        <button
                            onClick={loadQueue}
                            className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded-lg transition-colors"
                            title="Refresh Queue"
                        >
                            <RefreshCw size={16} />
                        </button>
                    </div>
                </div>

                {/* Active Filter Chip Banner */}
                {activeEpisodeFilter && (
                    <div className="mx-6 mt-4 p-3 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 rounded-xl flex items-center justify-between text-xs animate-in fade-in duration-200">
                        <div className="flex items-center gap-2 text-amber-900 dark:text-amber-200">
                            <span className="p-1 bg-amber-100 dark:bg-amber-900/60 rounded-md">
                                <Filter size={13} className="text-amber-600 dark:text-amber-400" />
                            </span>
                            <span>
                                Filtered to episode: <strong className="font-semibold text-amber-950 dark:text-amber-100">{activeEpisodeFilter}</strong>
                                {effectiveProject && <> &middot; Project: <strong className="font-semibold text-amber-950 dark:text-amber-100">{effectiveProject}</strong></>}
                                <span className="text-amber-700/70 dark:text-amber-400/70 ml-1.5">
                                    ({displayedQueue.length} line{displayedQueue.length !== 1 ? 's' : ''})
                                </span>
                            </span>
                        </div>
                        <button
                            type="button"
                            onClick={handleClearEpisodeFilter}
                            className="px-2.5 py-1 bg-white hover:bg-amber-100 dark:bg-gray-800 dark:hover:bg-gray-700 text-amber-900 dark:text-amber-200 border border-amber-200 dark:border-amber-800 rounded-lg flex items-center gap-1 font-semibold transition-colors shadow-xs"
                            title="Clear filter and view all episodes"
                        >
                            <X size={12} />
                            <span>Clear Filter</span>
                        </button>
                    </div>
                )}

                <div className="p-0">
                    {displayedQueue.length === 0 ? (
                        <div className="text-center py-12">
                            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-emerald-50 dark:bg-emerald-900/20 mb-4">
                                <CheckCircle size={32} className="text-emerald-500" />
                            </div>
                            <h4 className="text-lg font-medium text-gray-900 dark:text-white mb-1">You're all caught up!</h4>
                            <p className="text-gray-500 text-sm">
                                {activeEpisodeFilter ? `No items in episode "${activeEpisodeFilter}" need manual review.` : 'No items currently need manual review.'}
                            </p>
                        </div>
                    ) : (
                        <div className="divide-y divide-gray-100 dark:divide-gray-700 max-h-[600px] overflow-y-auto">
                            {displayedQueue.map((item, i) => {
                                const isActive = i === activeIndex;
                                return (
                                    <div 
                                        key={`${item.episode}-${item.index}-${i}`} 
                                        id={`queue-item-${i}`}
                                        onClick={() => setActiveIndex(i)}
                                        className={`p-4 transition-colors cursor-pointer ${
                                            isActive 
                                                ? 'bg-indigo-50/20 dark:bg-indigo-950/10 border-l-4 border-indigo-500' 
                                                : 'hover:bg-gray-50 dark:hover:bg-gray-800/50'
                                        }`}
                                    >
                                        <div className="flex justify-between items-start mb-3">
                                            <div className="flex items-center gap-2">
                                                {!projectName && item.project_name && (
                                                    <span className="text-xs font-bold px-2 py-0.5 rounded bg-indigo-50 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300 border border-indigo-100 dark:border-indigo-900">
                                                        {item.project_name}
                                                    </span>
                                                )}
                                                <span className="text-xs font-semibold px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
                                                    {item.episode}
                                                </span>
                                                <span className="text-xs font-mono text-gray-400">Idx: {item.index + 1}</span>
                                            </div>
                                            {!isActive && (
                                                <button
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        handleResolve(item.project_name, item.episode, item.index);
                                                    }}
                                                    disabled={resolvingId === `${item.project_name || effectiveProject}-${item.episode}-${item.index}`}
                                                    className="flex items-center gap-1 text-xs font-medium text-emerald-600 hover:text-emerald-700 bg-emerald-50 hover:bg-emerald-100 dark:bg-emerald-900/20 dark:hover:bg-emerald-900/40 px-2 py-1 rounded transition-colors disabled:opacity-50"
                                                >
                                                    <Check size={12} /> Resolve
                                                </button>
                                            )}
                                        </div>

                                        {isActive ? (
                                            <div onClick={(e) => e.stopPropagation()}>
                                                <ReviewLineResolver
                                                    projectName={item.project_name || effectiveProject}
                                                    episodeName={item.episode}
                                                    line={item}
                                                    onResolved={() => {
                                                        setQueue(prev => prev.filter(it => !(
                                                            (it.project_name || effectiveProject) === (item.project_name || effectiveProject) && 
                                                            it.episode === item.episode && 
                                                            it.index === item.index
                                                        )));
                                                        setActiveIndex(prev => Math.max(0, Math.min(displayedQueue.length - 2, prev)));
                                                    }}
                                                    onUpdateLineText={(lineIdx, newTxt) => handleLocalLineUpdate(item.episode, lineIdx, newTxt, item.project_name || effectiveProject)}
                                                    onSkip={() => setActiveIndex(prev => Math.min(displayedQueue.length - 1, prev + 1))}
                                                />
                                            </div>
                                        ) : (
                                            <div className="grid grid-cols-2 gap-4">
                                                <div className="text-xs text-gray-600 dark:text-gray-400 line-clamp-2">
                                                    <span className="font-bold text-[9px] text-gray-400 block uppercase mb-0.5">Source</span>
                                                    {item.original}
                                                </div>
                                                <div className="text-xs font-medium text-gray-900 dark:text-white line-clamp-2">
                                                    <span className="font-bold text-[9px] text-indigo-400 block uppercase mb-0.5">Translation</span>
                                                    {item.translated}
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </div>
            </div>

            {/* Glossary Suggestions sidebar */}
            {effectiveProject && (
                <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6 shadow-sm flex flex-col gap-4">
                    <h3 className="text-md font-bold text-gray-900 dark:text-white flex items-center gap-2 border-b pb-3 border-gray-100 dark:border-gray-700">
                        <BookOpen size={18} className="text-indigo-500" />
                        Emergent Suggestions
                    </h3>
                    <p className="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">
                        Emergent recurring terms identified across translated files. Add them to the glossary to ensure consistency.
                    </p>

                    {suggestions.length === 0 ? (
                        <div className="text-center py-6 text-xs text-gray-400">
                            No suggestions yet. Suggestions populate as reconciliation operates on files.
                        </div>
                    ) : (
                        <div className="divide-y divide-gray-100 dark:divide-gray-700 overflow-y-auto max-h-[400px] flex-1">
                            {suggestions.map((sug, i) => (
                                <div key={`${sug.term}-${i}`} className="py-3 flex items-center justify-between gap-3 group">
                                    <div className="flex flex-col gap-1 min-w-0">
                                        <span className="text-xs font-bold text-gray-900 dark:text-white truncate">{sug.term}</span>
                                        <div className="flex items-center gap-2 text-[10px] text-gray-400">
                                            <span className="truncate text-indigo-600 dark:text-indigo-400 font-medium">{sug.suggested_translation}</span>
                                            <span>•</span>
                                            <span>{sug.occurrences}x</span>
                                        </div>
                                    </div>
                                    <button
                                        onClick={() => handleAcceptSuggestion(sug)}
                                        className="flex items-center gap-1 p-1 bg-indigo-50 hover:bg-indigo-100 dark:bg-indigo-950/40 dark:hover:bg-indigo-900/60 text-indigo-600 dark:text-indigo-400 rounded-lg text-[10px] font-bold transition-all"
                                        title="Add to Glossary"
                                    >
                                        <Plus size={12} /> Add
                                    </button>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default ReviewQueuePanel;
