import React, { useState, useEffect } from 'react';
import { api } from '../api';
import {
    Clock, Play, CheckCircle2, AlertTriangle, Pause, XCircle,
    ArrowUp, RefreshCw, X, AlertCircle, Info, Activity, Terminal, Copy, Trash2, Zap
} from 'lucide-react';
import { useToast } from '../context/ToastContext';
import { useJobs } from '../context/JobContext';

const renderColorizedLog = (log) => {
    if (!log) return null;
    let colorClass = "text-gray-300"; // default
    const lowerLog = log.toLowerCase();
    
    if (lowerLog.includes("failed") || lowerLog.includes("failure") || lowerLog.includes("error") || lowerLog.includes("exception")) {
        colorClass = "text-red-400 font-semibold";
    } else if (lowerLog.includes("warning") || lowerLog.includes("incomplete") || lowerLog.includes("limit")) {
        colorClass = "text-amber-500 font-medium";
    } else if (lowerLog.includes("export") || lowerLog.includes("saved") || lowerLog.includes("save") || lowerLog.includes("stored") || lowerLog.includes("checkpoint file")) {
        colorClass = "text-emerald-400";
    } else if (lowerLog.includes("translating") || lowerLog.includes("translation") || lowerLog.includes("retranslate") || lowerLog.includes("reasoning:") || lowerLog.includes("reused")) {
        colorClass = "text-purple-400";
    } else if (lowerLog.includes("running") || lowerLog.includes("starting") || lowerLog.includes("initializing") || lowerLog.includes("syncing") || lowerLog.includes("extracting") || lowerLog.includes("reviewing")) {
        colorClass = "text-blue-400";
    }
    
    return <span className={colorClass}>{log}</span>;
};

const TranslationQueuePanel = () => {
    const toast = useToast();
    const { activeJobs } = useJobs();
    const [queueData, setQueueData] = useState({ items: [], summary: {}, rate_limits: {} });
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [activeTab, setActiveTab] = useState('active'); // active | history
    const [selectedJobLogs, setSelectedJobLogs] = useState(null); // { title: string, logs: string[] }
    const [checkingLimit, setCheckingLimit] = useState(false);

    const fetchQueue = async () => {
        try {
            const res = await api.getQueue();
            setQueueData(res.data);
            setError(null);
        } catch (e) {
            console.error("Failed to fetch queue:", e);
            setError("Failed to fetch queue data");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchQueue();
        const interval = setInterval(() => {
            if (!document.hidden) {
                fetchQueue();
            }
        }, 5000);
        return () => clearInterval(interval);
    }, []);

    const handleAction = async (actionFn, itemId, successMsg) => {
        try {
            await actionFn(itemId);
            toast.success(successMsg);
            fetchQueue();
        } catch (e) {
            console.error(`Action failed:`, e);
            toast.error(`Failed to perform action: ${e.response?.data?.detail || e.message}`);
        }
    };

    const handlePauseResume = async () => {
        try {
            if (queueData.paused) {
                await api.resumeQueue();
                toast.success('Translation worker resumed');
            } else {
                await api.pauseQueue();
                toast.success('Translation worker paused');
            }
            fetchQueue();
        } catch (e) {
            console.error("Failed to toggle queue pause:", e);
            toast.error('Failed to toggle queue worker state');
        }
    };

    const handleCheckDailyLimit = async () => {
        setCheckingLimit(true);
        try {
            const res = await api.checkDailyLimit();
            const cleared = res.data.cleared || [];
            if (cleared.length > 0) {
                toast.success(`Daily limit lifted — resuming: ${cleared.join(', ')}`);
            } else if ((res.data.checked || 0) === 0) {
                toast.info('No models are currently daily-limited.');
            } else {
                toast.info('Daily limit still in effect. Will keep auto-checking.');
            }
            fetchQueue();
        } catch (e) {
            console.error("Failed to check daily limit:", e);
            toast.error('Failed to check daily limit');
        } finally {
            setCheckingLimit(false);
        }
    };

    const handleClearHistory = async () => {
        try {
            const res = await api.clearQueueHistory();
            toast.success(`Cleared ${res.data.cleared_count} history items`);
            fetchQueue();
        } catch (e) {
            console.error("Failed to clear queue history:", e);
            toast.error('Failed to clear queue history');
        }
    };

    const handleViewLogs = async (itemId, title) => {
        try {
            let logs = [];
            const runningJob = Object.values(activeJobs || {}).find(j => j.id === `bg_${itemId}`);
            if (runningJob) {
                logs = runningJob.logs;
            } else {
                const res = await api.getQueueItemLogs(itemId);
                logs = res.data.logs || [];
            }
            setSelectedJobLogs({ title, itemId, logs });
        } catch (e) {
            console.error("Failed to fetch logs:", e);
            toast.error('Failed to load task logs');
        }
    };

    const getStatusIcon = (status) => {
        switch (status) {
            case 'pending': return <Clock className="text-blue-500" size={16} />;
            case 'running': return <Activity className="text-amber-500 animate-pulse" size={16} />;
            case 'completed': return <CheckCircle2 className="text-emerald-500" size={16} />;
            case 'failed': return <AlertTriangle className="text-rose-500" size={16} />;
            case 'paused_daily_limit': return <Pause className="text-amber-600" size={16} />;
            case 'cancelled': return <XCircle className="text-gray-400" size={16} />;
            default: return <Info className="text-gray-500" size={16} />;
        }
    };

    const getStatusBadge = (status) => {
        const base = "px-2 py-0.5 text-xs font-semibold rounded-full flex items-center gap-1.5 w-fit";
        switch (status) {
            case 'pending': return <span className={`${base} bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-400`}>{getStatusIcon(status)} Pending</span>;
            case 'running': return <span className={`${base} bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400 animate-pulse`}>{getStatusIcon(status)} Running</span>;
            case 'completed': return <span className={`${base} bg-emerald-50 text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-400`}>{getStatusIcon(status)} Completed</span>;
            case 'completed_flagged': return <span className={`${base} bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400`} title="Completed, but some lines need review"><AlertTriangle size={14} /> Needs review</span>;
            case 'failed': return <span className={`${base} bg-rose-50 text-rose-700 dark:bg-rose-900/20 dark:text-rose-450`}>{getStatusIcon(status)} Failed</span>;
            case 'paused_daily_limit': return <span className={`${base} bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-500`}>{getStatusIcon(status)} Rate Paused</span>;
            case 'cancelled': return <span className={`${base} bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-450`}>{getStatusIcon(status)} Cancelled</span>;
            default: return <span className={`${base} bg-gray-50 text-gray-700`}>{getStatusIcon(status)} {status}</span>;
        }
    };

    const getPriorityBadge = (priority) => {
        const base = "px-2 py-0.5 text-xs font-semibold rounded-full";
        if (priority === 0) return <span className={`${base} bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300`}>Webhook</span>;
        if (priority === 1) return <span className={`${base} bg-indigo-100 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-300`}>Manual</span>;
        if (priority === 2) return <span className={`${base} bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300`}>Sync</span>;
        return <span className={`${base} bg-slate-100 text-slate-800 dark:bg-gray-800 dark:text-gray-300`}>Backlog</span>;
    };

    const formatTimestamp = (isoString) => {
        if (!isoString) return '-';
        const date = new Date(isoString);
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' ' + date.toLocaleDateString();
    };

    const getCountdown = (isoString) => {
        if (!isoString) return null;
        const diff = new Date(isoString) - new Date();
        if (diff <= 0) return 'Ready';
        const mins = Math.ceil(diff / 60000);
        return `in ${mins}m`;
    };

    const items = queueData.items || [];
    const summary = queueData.summary || {};
    const rateLimits = queueData.rate_limits || {};
    const hasDailyLimit = Object.values(rateLimits).some(s => s.daily_exhausted)
        || (summary.paused_daily_limit || 0) > 0;

    const filteredItems = items.filter(item => {
        const isActive = ['pending', 'running', 'paused_daily_limit'].includes(item.status);
        return activeTab === 'active' ? isActive : !isActive;
    });

    const getModalLogs = () => {
        if (!selectedJobLogs) return [];
        const runningJob = Object.values(activeJobs || {}).find(j => j.id === `bg_${selectedJobLogs.itemId}`);
        return runningJob ? (runningJob.logs || []) : (selectedJobLogs.logs || []);
    };
    const modalLogs = getModalLogs();

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                    <h2 className="text-xl font-bold tracking-tight text-gray-900 dark:text-white">Background Translation Queue</h2>
                    <p className="text-sm text-gray-500 dark:text-gray-400">Monitor automated subtitle translations across Sonarr and Radarr.</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <button
                        onClick={handlePauseResume}
                        className={`flex items-center gap-1.5 text-sm font-semibold border px-3 py-1.5 rounded-lg shadow-sm transition-colors ${
                            queueData.paused
                                ? 'bg-amber-500/10 border-amber-500/30 text-amber-600 dark:text-amber-400 hover:bg-amber-500/20'
                                : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/20'
                        }`}
                    >
                        {queueData.paused ? <Play size={14} /> : <Pause size={14} />}
                        {queueData.paused ? 'Resume Worker' : 'Pause Worker'}
                    </button>

                    {hasDailyLimit && (
                        <button
                            onClick={handleCheckDailyLimit}
                            disabled={checkingLimit}
                            className="flex items-center gap-1.5 text-sm font-semibold bg-amber-500/10 border border-amber-500/30 text-amber-600 dark:text-amber-400 hover:bg-amber-500/20 px-3 py-1.5 rounded-lg shadow-sm transition-colors disabled:opacity-60"
                            title="Probe the API now to see if the daily quota has reset"
                        >
                            <Zap size={14} className={checkingLimit ? "animate-pulse" : ""} />
                            {checkingLimit ? 'Checking…' : 'Check Daily Limit'}
                        </button>
                    )}

                    <button
                        onClick={handleClearHistory}
                        className="flex items-center gap-1.5 text-sm font-semibold bg-rose-50 hover:bg-rose-100 dark:bg-rose-950/20 text-rose-600 dark:text-rose-400 border border-rose-200 dark:border-rose-900/30 px-3 py-1.5 rounded-lg shadow-sm transition-colors"
                    >
                        <Trash2 size={14} /> Clear History
                    </button>

                    <button 
                        onClick={fetchQueue}
                        className="flex items-center gap-1.5 text-sm font-semibold bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 px-3 py-1.5 rounded-lg shadow-sm hover:bg-gray-50 dark:hover:bg-gray-750 transition-colors"
                    >
                        <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
                    </button>
                </div>
            </div>

            {error && (
                <div className="p-4 bg-rose-50 dark:bg-rose-950/20 border border-rose-200 dark:border-rose-900/50 rounded-xl text-rose-700 dark:text-rose-400 flex gap-2">
                    <AlertCircle size={20} className="shrink-0" />
                    <div>{error}</div>
                </div>
            )}

            {/* Summary Cards */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm">
                    <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Running</div>
                    <div className="text-2xl font-bold text-amber-500 mt-1 flex items-center gap-2">
                        {summary.running || 0}
                        {summary.running > 0 && <Activity className="text-amber-500 animate-pulse" size={20} />}
                    </div>
                </div>
                <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm">
                    <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Pending</div>
                    <div className="text-2xl font-bold text-blue-500 mt-1">{summary.pending || 0}</div>
                </div>
                <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm">
                    <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Completed Today</div>
                    <div className="text-2xl font-bold text-emerald-500 mt-1">{summary.completed_today || 0}</div>
                </div>
                <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm">
                    <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Failed</div>
                    <div className="text-2xl font-bold text-rose-500 mt-1">{summary.failed || 0}</div>
                </div>
                <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm col-span-2 md:col-span-1">
                    <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Rate Paused</div>
                    <div className="text-2xl font-bold text-amber-600 mt-1">{summary.paused_daily_limit || 0}</div>
                </div>
            </div>

            {/* Rate Limits Gauges */}
            {Object.keys(rateLimits).length > 0 && (
                <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm space-y-4">
                    <h3 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">Per-Model Daily Quotas (RPD)</h3>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {Object.entries(rateLimits).map(([model, stats]) => {
                            const pct = Math.min(100, (stats.daily_count / stats.daily_limit) * 100);
                            // Exhaustion is now response-driven: trust the API-set latch.
                            const isExhausted = stats.daily_exhausted;
                            return (
                                <div key={model} className="space-y-1.5">
                                    <div className="flex justify-between text-xs font-semibold">
                                        <span className="text-gray-700 dark:text-gray-300 truncate max-w-[180px]">{model}</span>
                                        <span className={isExhausted ? "text-rose-500 font-bold" : "text-gray-500"}>
                                            {stats.daily_count} / {stats.daily_limit} RPD
                                        </span>
                                    </div>
                                    <div className="w-full bg-gray-100 dark:bg-gray-850 border border-gray-200 dark:border-gray-750 h-2.5 rounded-full overflow-hidden">
                                        <div 
                                            className={`h-full rounded-full transition-all duration-500 ${
                                                isExhausted 
                                                    ? 'bg-rose-500' 
                                                    : pct > 80 
                                                        ? 'bg-amber-500' 
                                                        : 'bg-indigo-600'
                                            }`}
                                            style={{ width: `${pct}%` }}
                                        ></div>
                                    </div>
                                    <div className="flex justify-between text-[10px] text-gray-400">
                                        <span>RPM Limit: {stats.tokens_available} tokens left</span>
                                        {stats.is_backing_off && (
                                            <span className="text-amber-500 animate-pulse">Backing off: {stats.backoff_remaining}s</span>
                                        )}
                                    </div>
                                    {isExhausted && (
                                        <div className="flex items-center gap-1.5 text-[10px] font-semibold text-rose-500">
                                            <Pause size={11} />
                                            Daily limit reached (detected from API).
                                            {stats.next_probe_in != null && (
                                                <span className="text-gray-400 font-normal">
                                                    Auto-recheck in {Math.ceil(stats.next_probe_in / 60)}m.
                                                </span>
                                            )}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* Main Tabs & Table */}
            <div className="bg-white dark:bg-gray-850 border border-gray-200 dark:border-gray-800 rounded-3xl overflow-hidden shadow-sm">
                <div className="border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-800/50 px-6 py-4 flex items-center justify-between">
                    <div className="flex gap-2">
                        <button 
                            onClick={() => setActiveTab('active')}
                            className={`px-3 py-1.5 text-sm font-semibold rounded-lg transition-colors ${
                                activeTab === 'active' 
                                    ? 'bg-indigo-600 text-white shadow-sm' 
                                    : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800'
                            }`}
                        >
                            Active Tasks ({items.filter(i => ['pending', 'running', 'paused_daily_limit'].includes(i.status)).length})
                        </button>
                        <button 
                            onClick={() => setActiveTab('history')}
                            className={`px-3 py-1.5 text-sm font-semibold rounded-lg transition-colors ${
                                activeTab === 'history' 
                                    ? 'bg-indigo-600 text-white shadow-sm' 
                                    : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800'
                            }`}
                        >
                            History ({items.filter(i => !['pending', 'running', 'paused_daily_limit'].includes(i.status)).length})
                        </button>
                    </div>
                </div>

                <div className="overflow-x-auto overflow-y-visible pb-20 -mb-20">
                    {filteredItems.length === 0 ? (
                        <div className="p-12 text-center text-gray-400 flex flex-col items-center justify-center gap-3">
                            <Clock size={40} className="text-gray-300 dark:text-gray-700" />
                            <div>No {activeTab} tasks found.</div>
                        </div>
                    ) : (
                        <table className="w-full text-left border-collapse">
                            <thead>
                                <tr className="border-b border-gray-250 dark:border-gray-800 text-xs font-bold text-gray-400 uppercase tracking-wider bg-gray-50/50 dark:bg-gray-850">
                                    <th className="px-6 py-3.5">Show / Episode</th>
                                    <th className="px-6 py-3.5">Status</th>
                                    <th className="px-6 py-3.5">Priority</th>
                                    <th className="px-6 py-3.5">Attempts</th>
                                    <th className="px-6 py-3.5">Next Retry</th>
                                    <th className="px-6 py-3.5">Error / Details</th>
                                    <th className="px-6 py-3.5 text-right">Actions</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-150 dark:divide-gray-800/80 text-sm">
                                {filteredItems.map(item => (
                                    <tr key={item.id} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/35 transition-colors">
                                        <td className="px-6 py-4 font-medium">
                                            <div className="text-gray-900 dark:text-white font-bold">{item.project_name}</div>
                                            <div className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{item.episode_name}</div>
                                        </td>
                                        <td className="px-6 py-4">
                                            {getStatusBadge(item.status === 'completed' && item.review_count > 0 ? 'completed_flagged' : item.status)}
                                            {item.status === 'completed' && item.review_count > 0 && (
                                                <div className="text-[11px] text-amber-600 dark:text-amber-400 mt-1">{item.review_count} line(s) flagged</div>
                                            )}
                                            {item.status === 'running' && item.progress != null && (
                                                <div className="mt-1.5 w-32">
                                                    <div className="w-full h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                                                        <div className="h-full bg-amber-500 rounded-full transition-all" style={{ width: `${Math.max(2, item.progress)}%` }} />
                                                    </div>
                                                    <div className="text-[10px] text-gray-400 mt-0.5">
                                                        {item.scenes_total > 0 ? `${item.scenes_done}/${item.scenes_total} scenes · ` : ''}{Math.round(item.progress)}%
                                                    </div>
                                                </div>
                                            )}
                                        </td>
                                        <td className="px-6 py-4">{getPriorityBadge(item.priority)}</td>
                                        <td className="px-6 py-4 font-semibold text-gray-500 dark:text-gray-400">{item.attempts} / 5</td>
                                        <td className="px-6 py-4 text-xs font-semibold">
                                            {item.status === 'pending' && item.next_retry_at ? (
                                                <span className="text-amber-600 dark:text-amber-500 flex items-center gap-1">
                                                    <Clock size={12} /> {getCountdown(item.next_retry_at)}
                                                </span>
                                            ) : (
                                                <span className="text-gray-400">-</span>
                                            )}
                                        </td>
                                        <td className="px-6 py-4 max-w-[220px]">
                                            {item.last_error ? (
                                                <div 
                                                    className="text-xs text-rose-500 truncate cursor-help group relative"
                                                    title={item.last_error}
                                                >
                                                    {item.last_error}
                                                    <div className="hidden group-hover:block absolute top-full left-0 mt-1 z-[99] bg-gray-900 text-white text-[11px] p-3 rounded-lg shadow-xl max-w-sm border border-gray-700 whitespace-pre-wrap leading-relaxed">
                                                        {item.last_error}
                                                    </div>
                                                </div>
                                            ) : (
                                                <span className="text-xs text-gray-400">-</span>
                                            )}
                                        </td>
                                        <td className="px-6 py-4 text-right">
                                            <div className="flex items-center justify-end gap-1.5">
                                                <button
                                                    onClick={() => handleViewLogs(item.id, `${item.project_name} - ${item.episode_name}`)}
                                                    className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-750 text-indigo-500 rounded-lg transition-colors"
                                                    title="View execution logs"
                                                >
                                                    <Terminal size={16} />
                                                </button>
                                                {['pending', 'failed', 'paused_daily_limit'].includes(item.status) && item.priority > 0 && (
                                                    <button
                                                        onClick={() => handleAction(api.prioritizeQueueItem, item.id, "Bumped task priority")}
                                                        className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-750 text-indigo-600 rounded-lg transition-colors"
                                                        title="Prioritize to top"
                                                    >
                                                        <ArrowUp size={16} />
                                                    </button>
                                                )}
                                                {['pending', 'failed', 'paused_daily_limit'].includes(item.status) && (
                                                    <button
                                                        onClick={() => handleAction(api.retryQueueItem, item.id, "Forced queue item retry")}
                                                        className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-750 text-emerald-600 rounded-lg transition-colors"
                                                        title="Retry now"
                                                    >
                                                        <Play size={16} />
                                                    </button>
                                                )}
                                                {['pending', 'running', 'failed', 'paused_daily_limit'].includes(item.status) && (
                                                    <button
                                                        onClick={() => handleAction(api.cancelQueueItem, item.id, "Cancelled queue task")}
                                                        className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-750 text-rose-500 rounded-lg transition-colors"
                                                        title="Cancel task"
                                                    >
                                                        <X size={16} />
                                                    </button>
                                                )}
                                                {!['pending', 'running', 'paused_daily_limit'].includes(item.status) && (
                                                    <span className="text-xs text-gray-400">
                                                        {item.status === 'completed' 
                                                            ? formatTimestamp(item.completed_at) 
                                                            : formatTimestamp(item.started_at)}
                                                    </span>
                                                )}
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
            </div>

            {/* View Logs Modal */}
            {selectedJobLogs && (
                <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[100] flex items-center justify-center p-4 md:p-10 font-mono">
                    <div className="bg-gray-950 text-gray-100 rounded-xl shadow-2xl border border-gray-800 w-full max-w-4xl h-[80vh] flex flex-col overflow-hidden">
                        <div className="p-4 bg-gray-900 border-b border-gray-800 flex justify-between items-center shrink-0">
                            <div className="flex items-center gap-2 font-sans">
                                <Terminal size={18} className="text-indigo-400" />
                                <span className="font-semibold text-sm">
                                    Logs: {selectedJobLogs.title}
                                </span>
                            </div>
                            <div className="flex items-center gap-3">
                                <button
                                    onClick={() => {
                                        const logsText = modalLogs.join('\n');
                                        navigator.clipboard.writeText(logsText);
                                        const btn = document.getElementById("copy-modal-btn");
                                        if (btn) {
                                            const origText = btn.innerText;
                                            btn.innerText = "Copied!";
                                            setTimeout(() => btn.innerText = origText, 2000);
                                        }
                                    }}
                                    id="copy-modal-btn"
                                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-lg transition-colors border border-gray-700 font-sans"
                                >
                                    <Copy size={12} />
                                    Copy Logs
                                </button>
                                <button
                                    onClick={() => setSelectedJobLogs(null)}
                                    className="p-1 hover:bg-gray-800 rounded-lg transition-colors text-gray-400 hover:text-gray-200"
                                >
                                    <X size={20} />
                                </button>
                            </div>
                        </div>
                        <div className="flex-1 p-4 overflow-y-auto space-y-1.5 text-xs select-text">
                            {modalLogs.length === 0 ? (
                                <div className="text-gray-500 italic p-4">No logs available for this task.</div>
                            ) : (
                                modalLogs.map((log, i) => (
                                    <div key={i} className="whitespace-pre-wrap leading-relaxed">
                                        {renderColorizedLog(log)}
                                    </div>
                                ))
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default TranslationQueuePanel;
