import React, { useState, useRef, useEffect } from 'react';
import { Play, Pause, Zap, Trash2, RefreshCw, Sparkles, XCircle, ChevronDown } from 'lucide-react';

const QueueControls = ({
    paused = false,
    hasDailyLimit = false,
    checkingLimit = false,
    loading = false,
    syncingTasks = false,
    syncingDisk = false,
    activeCount = 0,
    historyCount = 0,
    onPauseResume,
    onCheckDailyLimit,
    onClearHistory,
    onCancelAll,
    onClearQueue,
    onRefresh,
    onSyncTasks,
    onSyncDisk,
}) => {
    const [clearMenuOpen, setClearMenuOpen] = useState(false);
    const clearMenuRef = useRef(null);

    useEffect(() => {
        const handleClickOutside = (event) => {
            if (clearMenuRef.current && !clearMenuRef.current.contains(event.target)) {
                setClearMenuOpen(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const handleClearAction = (status) => {
        setClearMenuOpen(false);
        if (onClearQueue) {
            onClearQueue(status);
        } else if (onClearHistory && status === 'history') {
            onClearHistory();
        }
    };

    return (
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div>
                <h2 className="text-xl font-bold tracking-tight text-gray-900 dark:text-white">Background Translation Queue</h2>
                <p className="text-sm text-gray-500 dark:text-gray-400">Monitor automated subtitle translations across Sonarr and Radarr.</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
                <button
                    type="button"
                    onClick={onPauseResume}
                    className={`flex items-center gap-1.5 text-sm font-semibold border px-3 py-1.5 rounded-lg shadow-sm transition-colors ${
                        paused
                            ? 'bg-amber-500/10 border-amber-500/30 text-amber-600 dark:text-amber-400 hover:bg-amber-500/20'
                            : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/20'
                    }`}
                >
                    {paused ? <Play size={14} /> : <Pause size={14} />}
                    {paused ? 'Resume Worker' : 'Pause Worker'}
                </button>

                {hasDailyLimit && (
                    <button
                        type="button"
                        onClick={onCheckDailyLimit}
                        disabled={checkingLimit}
                        className="flex items-center gap-1.5 text-sm font-semibold bg-amber-500/10 border border-amber-500/30 text-amber-600 dark:text-amber-400 hover:bg-amber-500/20 px-3 py-1.5 rounded-lg shadow-sm transition-colors disabled:opacity-60"
                        title="Probe the API now to see if the daily quota has reset"
                    >
                        <Zap size={14} className={checkingLimit ? "animate-pulse" : ""} />
                        {checkingLimit ? 'Checking…' : 'Check Daily Limit'}
                    </button>
                )}

                {onSyncTasks && (
                    <button
                        type="button"
                        onClick={onSyncTasks}
                        disabled={syncingTasks}
                        className="flex items-center gap-1.5 text-sm font-semibold bg-indigo-50 hover:bg-indigo-100 dark:bg-indigo-950/20 text-indigo-600 dark:text-indigo-400 border border-indigo-200 dark:border-indigo-900/30 px-3 py-1.5 rounded-lg shadow-sm transition-colors disabled:opacity-60"
                        title="Release abandoned agent claims and complete items whose episode is already translated"
                    >
                        <Sparkles size={14} className={syncingTasks ? "animate-spin" : ""} />
                        {syncingTasks ? 'Syncing…' : 'Sync Agent Tasks'}
                    </button>
                )}

                {onSyncDisk && (
                    <button
                        type="button"
                        onClick={onSyncDisk}
                        disabled={syncingDisk}
                        className="flex items-center gap-1.5 text-sm font-semibold bg-emerald-50 hover:bg-emerald-100 dark:bg-emerald-950/20 text-emerald-600 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-900/30 px-3 py-1.5 rounded-lg shadow-sm transition-colors disabled:opacity-60"
                        title="Scan physical disk for subtitle files and reconcile project & queue status"
                    >
                        <RefreshCw size={14} className={syncingDisk ? "animate-spin" : ""} />
                        {syncingDisk ? 'Scanning Disk…' : 'Sync Disk Subtitles'}
                    </button>
                )}

                {onCancelAll && (
                    <button
                        type="button"
                        onClick={() => onCancelAll('pending')}
                        className="flex items-center gap-1.5 text-sm font-semibold bg-amber-50 hover:bg-amber-100 dark:bg-amber-950/20 text-amber-700 dark:text-amber-400 border border-amber-200 dark:border-amber-900/30 px-3 py-1.5 rounded-lg shadow-sm transition-colors"
                        title="Cancel all pending and running translations"
                    >
                        <XCircle size={14} /> Cancel All Pending
                    </button>
                )}

                {/* Clear Menu Dropdown */}
                <div className="relative" ref={clearMenuRef}>
                    <button
                        type="button"
                        onClick={() => setClearMenuOpen(prev => !prev)}
                        className="flex items-center gap-1.5 text-sm font-semibold bg-rose-50 hover:bg-rose-100 dark:bg-rose-950/20 text-rose-600 dark:text-rose-400 border border-rose-200 dark:border-rose-900/30 px-3 py-1.5 rounded-lg shadow-sm transition-colors"
                        title="Clear queue records"
                    >
                        <Trash2 size={14} /> Clear Queue <ChevronDown size={12} className={clearMenuOpen ? "rotate-180 transition-transform" : "transition-transform"} />
                    </button>

                    {clearMenuOpen && (
                        <div className="absolute right-0 mt-1.5 w-52 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-xl py-1 z-50 text-xs font-medium">
                            <button
                                type="button"
                                onClick={() => handleClearAction('history')}
                                className="w-full text-left px-3 py-2 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-750 flex items-center justify-between"
                            >
                                <span>Clear Finished History</span>
                                <span className="text-gray-400 text-[10px]">{historyCount} items</span>
                            </button>
                            <button
                                type="button"
                                onClick={() => handleClearAction('pending')}
                                className="w-full text-left px-3 py-2 text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-950/20 flex items-center justify-between"
                            >
                                <span>Clear Pending Items</span>
                                <span className="text-amber-500/80 text-[10px]">{activeCount} items</span>
                            </button>
                            <button
                                type="button"
                                onClick={() => handleClearAction('cancelled')}
                                className="w-full text-left px-3 py-2 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-750"
                            >
                                Clear Cancelled Items
                            </button>
                            <button
                                type="button"
                                onClick={() => handleClearAction('postponed')}
                                className="w-full text-left px-3 py-2 text-purple-600 dark:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-950/20"
                            >
                                Clear Postponed Items
                            </button>
                            <div className="my-1 border-t border-gray-150 dark:border-gray-700" />
                            <button
                                type="button"
                                onClick={() => {
                                    if (window.confirm("Are you sure you want to permanently clear ALL queue items and workspaces?")) {
                                        handleClearAction('all');
                                    }
                                }}
                                className="w-full text-left px-3 py-2 text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/30 font-semibold"
                            >
                                Clear All (Wipe Queue)
                            </button>
                        </div>
                    )}
                </div>

                <button 
                    type="button"
                    onClick={onRefresh}
                    className="flex items-center gap-1.5 text-sm font-semibold bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 px-3 py-1.5 rounded-lg shadow-sm hover:bg-gray-50 dark:hover:bg-gray-750 transition-colors"
                >
                    <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
                </button>
            </div>
        </div>
    );
};

export default QueueControls;

