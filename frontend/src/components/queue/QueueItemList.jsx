import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
    Clock, Activity, CheckCircle2, AlertTriangle, Pause, XCircle,
    Info, ArrowUp, Play, X, Terminal, Sparkles, Bot, Copy, Check, Folder, FileText, Trash2
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { api } from '../../api';
import { formatEta } from '../../utils/formatters';
import { useToast } from '../../context/ToastContext';
import AgentTaskModal from '../modals/AgentTaskModal';

export const renderColorizedLog = (log) => {
    if (!log) return null;
    let colorClass = "text-gray-300";
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

const QueueItemList = ({
    items = [],
    activeTab,
    setActiveTab,
    onViewLogs,
    onAction,
    onDelete,
    onBulkDelete,
    onRefresh,
}) => {
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

    const [selectedIds, setSelectedIds] = useState(new Set());
    const [bulkProcessing, setBulkProcessing] = useState(false);
    const [preparingAgentId, setPreparingAgentId] = useState(null);
    const [agentModalData, setAgentModalData] = useState(null);
    const [copiedField, setCopiedField] = useState(null);
    const toast = useToast();

    const handleCopy = (text, fieldName) => {
        navigator.clipboard.writeText(text);
        setCopiedField(fieldName);
        setTimeout(() => setCopiedField(null), 2000);
        toast.success("Copied to clipboard!");
    };

    const handlePrepareAgentTask = async (item) => {
        setPreparingAgentId(item.id);
        try {
            const res = await api.prepareAgentTask(item.id);
            if (res.data && res.data.success) {
                setAgentModalData(res.data);
                toast.success(`Agent workspace ready for ${item.project_name}`);
                if (onRefresh) onRefresh();
            } else {
                toast.error(res.data?.message || "Failed to prepare agent workspace");
            }
        } catch (err) {
            toast.error(err.response?.data?.message || err.response?.data?.detail || err.message || "Failed to prepare agent workspace");
        } finally {
            setPreparingAgentId(null);
        }
    };

    const filteredItems = items.filter(item => {
        const isActive = ['pending', 'running', 'paused_daily_limit'].includes(item.status);
        return activeTab === 'active' ? isActive : !isActive;
    });

    // Clear selection when tab changes
    useEffect(() => {
        setSelectedIds(new Set());
    }, [activeTab]);

    const allSelected = filteredItems.length > 0 && filteredItems.every(i => selectedIds.has(i.id));
    const someSelected = filteredItems.length > 0 && filteredItems.some(i => selectedIds.has(i.id));

    const toggleSelectAll = () => {
        if (allSelected) {
            setSelectedIds(new Set());
        } else {
            setSelectedIds(new Set(filteredItems.map(i => i.id)));
        }
    };

    const toggleSelectItem = (id) => {
        setSelectedIds(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };

    const handleBulkPrioritize = async () => {
        const targets = filteredItems.filter(i => selectedIds.has(i.id) && ['pending', 'failed', 'paused_daily_limit'].includes(i.status) && i.priority > 0);
        if (targets.length === 0) {
            toast.info("No prioritizable tasks selected");
            return;
        }
        setBulkProcessing(true);
        try {
            const results = await Promise.allSettled(targets.map(t => api.prioritizeQueueItem(t.id)));
            const succeeded = results.filter(r => r.status === 'fulfilled').length;
            const failed = results.filter(r => r.status === 'rejected').length;
            if (succeeded > 0 && failed === 0) {
                toast.success(`Prioritized ${succeeded} task(s)`);
            } else if (succeeded > 0 && failed > 0) {
                toast.warning(`Prioritized ${succeeded} task(s), ${failed} failed`);
            } else {
                toast.error(`Failed to prioritize ${failed} task(s)`);
            }
            setSelectedIds(new Set());
            if (onRefresh) onRefresh();
        } catch (err) {
            toast.error("Failed to prioritize tasks");
        } finally {
            setBulkProcessing(false);
        }
    };

    const handleBulkRetry = async () => {
        const targets = filteredItems.filter(i => selectedIds.has(i.id) && ['pending', 'failed', 'paused_daily_limit', 'completed', 'cancelled'].includes(i.status));
        if (targets.length === 0) {
            toast.info("No retriable tasks selected");
            return;
        }
        setBulkProcessing(true);
        try {
            const results = await Promise.allSettled(targets.map(t => api.retryQueueItem(t.id)));
            const succeeded = results.filter(r => r.status === 'fulfilled').length;
            const failed = results.filter(r => r.status === 'rejected').length;
            if (succeeded > 0 && failed === 0) {
                toast.success(`Retrying ${succeeded} task(s)`);
            } else if (succeeded > 0 && failed > 0) {
                toast.warning(`Retrying ${succeeded} task(s), ${failed} failed`);
            } else {
                toast.error(`Failed to retry ${failed} task(s)`);
            }
            setSelectedIds(new Set());
            if (onRefresh) onRefresh();
        } catch (err) {
            toast.error("Failed to retry tasks");
        } finally {
            setBulkProcessing(false);
        }
    };

    const handleBulkCancel = async () => {
        const targets = filteredItems.filter(i => selectedIds.has(i.id) && ['pending', 'running', 'failed', 'paused_daily_limit'].includes(i.status));
        if (targets.length === 0) {
            toast.info("No cancellable tasks selected");
            return;
        }
        setBulkProcessing(true);
        try {
            const results = await Promise.allSettled(targets.map(t => api.cancelQueueItem(t.id)));
            const succeeded = results.filter(r => r.status === 'fulfilled').length;
            const failed = results.filter(r => r.status === 'rejected').length;
            if (succeeded > 0 && failed === 0) {
                toast.success(`Cancelled ${succeeded} task(s)`);
            } else if (succeeded > 0 && failed > 0) {
                toast.warning(`Cancelled ${succeeded} task(s), ${failed} failed`);
            } else {
                toast.error(`Failed to cancel ${failed} task(s)`);
            }
            setSelectedIds(new Set());
            if (onRefresh) onRefresh();
        } catch (err) {
            toast.error("Failed to cancel tasks");
        } finally {
            setBulkProcessing(false);
        }
    };

    const handleBulkDeleteAction = async () => {
        const ids = Array.from(selectedIds);
        if (ids.length === 0) return;
        if (!window.confirm(`Are you sure you want to permanently delete ${ids.length} selected queue task(s)?`)) {
            return;
        }
        setBulkProcessing(true);
        try {
            if (onBulkDelete) {
                await onBulkDelete(ids);
            } else if (onDelete) {
                await Promise.allSettled(ids.map(id => onDelete(id)));
            }
            setSelectedIds(new Set());
            if (onRefresh) onRefresh();
        } catch (err) {
            toast.error("Failed to delete selected tasks");
        } finally {
            setBulkProcessing(false);
        }
    };

    const activeCount = items.filter(i => ['pending', 'running', 'paused_daily_limit'].includes(i.status)).length;
    const historyCount = items.filter(i => !['pending', 'running', 'paused_daily_limit'].includes(i.status)).length;

    return (
        <div className="bg-white dark:bg-gray-850 border border-gray-200 dark:border-gray-800 rounded-3xl overflow-hidden shadow-sm">
            <div className="border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-800/50 px-6 py-4 flex items-center justify-between">
                <div className="flex gap-2">
                    <button 
                        type="button"
                        onClick={() => setActiveTab('active')}
                        className={`px-3 py-1.5 text-sm font-semibold rounded-lg transition-colors ${
                            activeTab === 'active' 
                                ? 'bg-indigo-600 text-white shadow-sm' 
                                : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800'
                        }`}
                    >
                        Active Tasks ({activeCount})
                    </button>
                    <button 
                        type="button"
                        onClick={() => setActiveTab('history')}
                        className={`px-3 py-1.5 text-sm font-semibold rounded-lg transition-colors ${
                            activeTab === 'history' 
                                ? 'bg-indigo-600 text-white shadow-sm' 
                                : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800'
                        }`}
                    >
                        History ({historyCount})
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
                                <th className="pl-6 pr-2 py-3.5 w-10">
                                    <input
                                        type="checkbox"
                                        checked={allSelected}
                                        ref={el => { if (el) el.indeterminate = someSelected && !allSelected; }}
                                        onChange={toggleSelectAll}
                                        className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500 cursor-pointer h-4 w-4"
                                        title={allSelected ? "Deselect all" : "Select all visible"}
                                    />
                                </th>
                                <th className="px-4 py-3.5">Show / Episode</th>
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
                                <tr key={item.id} className={`hover:bg-gray-50/50 dark:hover:bg-gray-800/35 transition-colors ${selectedIds.has(item.id) ? 'bg-indigo-50/40 dark:bg-indigo-950/20' : ''}`}>
                                    <td className="pl-6 pr-2 py-4 w-10">
                                        <input
                                            type="checkbox"
                                            checked={selectedIds.has(item.id)}
                                            onChange={() => toggleSelectItem(item.id)}
                                            className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500 cursor-pointer h-4 w-4"
                                        />
                                    </td>
                                    <td className="px-4 py-4 font-medium">
                                        <div className="text-gray-900 dark:text-white font-bold">{item.project_name}</div>
                                        <div className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{item.episode_name}</div>
                                    </td>
                                    <td className="px-6 py-4">
                                        {item.status === 'completed' ? (
                                            <div className="flex flex-col gap-1">
                                                {getStatusBadge(item.review_count > 0 ? 'completed_flagged' : 'completed')}
                                                {item.review_count > 0 ? (
                                                    <Link
                                                        to={`/review?project=${encodeURIComponent(item.project_name)}&episode=${encodeURIComponent(item.episode_name)}`}
                                                        className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-600 dark:text-amber-400 hover:text-amber-700 dark:hover:text-amber-300 hover:underline bg-amber-50 dark:bg-amber-950/40 px-2 py-0.5 rounded-full border border-amber-200 dark:border-amber-800/40 w-fit mt-0.5 transition-colors"
                                                        title={`Click to review ${item.review_count} flagged line(s) in Review Queue`}
                                                    >
                                                        <AlertTriangle size={11} /> Needs Review ({item.review_count} line{item.review_count > 1 ? 's' : ''})
                                                    </Link>
                                                ) : item.auto_repaired_count > 0 ? (
                                                    <span
                                                        className="inline-flex items-center gap-1 text-[11px] font-semibold text-purple-700 dark:text-purple-300 bg-purple-50 dark:bg-purple-950/40 px-2 py-0.5 rounded-full border border-purple-200 dark:border-purple-800/40 w-fit mt-0.5"
                                                        title={`${item.auto_repaired_count} cue(s) recovered by automated repair`}
                                                    >
                                                        <Sparkles size={11} /> Auto-Repaired ({item.auto_repaired_count})
                                                    </span>
                                                ) : (
                                                    <span
                                                        className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-950/40 px-2 py-0.5 rounded-full border border-emerald-200 dark:border-emerald-800/40 w-fit mt-0.5"
                                                        title="All cues translated with 0 warnings"
                                                    >
                                                        <CheckCircle2 size={11} /> Clean
                                                    </span>
                                                )}
                                            </div>
                                        ) : (
                                            <div className="flex items-center gap-1.5 flex-wrap">
                                                {getStatusBadge(item.status)}
                                                {item.engine === 'agent' && !['completed', 'cancelled'].includes(item.status) && (
                                                    <span
                                                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-purple-50 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300"
                                                        title={item.agent?.task_id ? `Claimed by an AI agent (${item.agent.task_id})` : 'Waiting for an AI agent (the model worker skips it)'}
                                                    >
                                                        <Bot size={11} /> Agent
                                                    </span>
                                                )}
                                            </div>
                                        )}

                                        {item.status === 'running' && item.agent && (
                                            <div className="mt-2 min-w-[200px] max-w-[260px] space-y-1">
                                                <div className="flex items-center justify-between gap-2">
                                                    <div className="flex-1 h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                                                        <div
                                                            className="h-full bg-gradient-to-r from-purple-500 to-indigo-600 rounded-full transition-all duration-500"
                                                            style={{ width: `${Math.max(3, Math.min(100, item.progress || 0))}%` }}
                                                        />
                                                    </div>
                                                    <span className="text-xs font-bold text-gray-700 dark:text-gray-200 tabular-nums">
                                                        {Math.round(item.progress || 0)}%
                                                    </span>
                                                </div>
                                                <div className="text-[11px] text-gray-500 dark:text-gray-400 truncate" title={item.agent.workspace_dir || ''}>
                                                    {item.agent.units_done ?? 0}/{item.agent.units ?? '?'} lines · agent working
                                                </div>
                                            </div>
                                        )}

                                        {item.status === 'running' && !item.agent && (
                                            <div className="mt-2 min-w-[200px] max-w-[260px] space-y-1.5">
                                                {/* Line 1: Progress Track with Shimmer and Percentage */}
                                                <div className="flex items-center justify-between gap-2">
                                                    <div className="flex-1 h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden relative shadow-inner">
                                                        <div
                                                            className="h-full bg-gradient-to-r from-amber-500 via-indigo-500 to-indigo-600 rounded-full transition-all duration-500 relative"
                                                            style={{ width: `${Math.max(3, Math.min(100, item.progress || 0))}%` }}
                                                        >
                                                            <div className="absolute inset-0 bg-white/25 animate-pulse" />
                                                        </div>
                                                    </div>
                                                    <span className="text-xs font-bold text-gray-700 dark:text-gray-200 tabular-nums">
                                                        {Math.round(item.progress || 0)}%
                                                    </span>
                                                </div>

                                                {/* Line 2: Multi-Metric Telemetry (Scenes, Cues, Dynamic ETA) */}
                                                <div className="flex items-center justify-between text-[11px] text-gray-500 dark:text-gray-400 leading-tight">
                                                    <div className="flex items-center gap-1.5 font-medium truncate max-w-[140px]">
                                                        {item.scenes_total > 0 && (
                                                            <span className="text-gray-700 dark:text-gray-300 font-semibold shrink-0">
                                                                Scene {item.scenes_done || 0}/{item.scenes_total}
                                                            </span>
                                                        )}
                                                        {item.scenes_total > 0 && (item.cues_total > 0 || item.cues_translated > 0) && (
                                                            <span className="text-gray-300 dark:text-gray-600">&middot;</span>
                                                        )}
                                                        {item.cues_total > 0 ? (
                                                            <span className="truncate">Cues: {item.cues_translated || 0}/{item.cues_total}</span>
                                                        ) : item.cues_translated > 0 ? (
                                                            <span className="truncate">{item.cues_translated} cues</span>
                                                        ) : null}
                                                    </div>

                                                    {item.eta_seconds != null && (
                                                        <span className="text-indigo-600 dark:text-indigo-400 font-semibold flex items-center gap-1 shrink-0 ml-1">
                                                            <Clock size={10} className="shrink-0" />
                                                            <span>ETA: {formatEta(item.eta_seconds)}</span>
                                                        </span>
                                                    )}
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
                                                {/* The stored error is "message\n\ntraceback"; the row shows the message, the hover card has all of it. */}
                                                {item.last_error.split('\n')[0]}
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
                                                type="button"
                                                onClick={() => onViewLogs(item.id, `${item.project_name} - ${item.episode_name}`)}
                                                className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-750 text-indigo-500 rounded-lg transition-colors"
                                                title="View execution logs"
                                            >
                                                <Terminal size={16} />
                                            </button>
                                            {item.status === 'pending' && (
                                                <button
                                                    type="button"
                                                    onClick={() => handlePrepareAgentTask(item)}
                                                    disabled={preparingAgentId === item.id}
                                                    className="p-1.5 hover:bg-purple-100 dark:hover:bg-purple-950/40 text-purple-600 dark:text-purple-400 rounded-lg transition-colors disabled:opacity-50"
                                                    title="Claim for an AI agent (Claude Code, Antigravity): builds its brief and work file"
                                                >
                                                    <Bot size={16} className={preparingAgentId === item.id ? "animate-spin" : ""} />
                                                </button>
                                            )}
                                            {item.status === 'running' && item.agent?.task_id && (
                                                <button
                                                    type="button"
                                                    onClick={() => onAction(api.releaseAgentTask, item.agent.task_id, "Released back to the queue")}
                                                    className="p-1.5 hover:bg-purple-100 dark:hover:bg-purple-950/40 text-purple-600 dark:text-purple-400 rounded-lg transition-colors"
                                                    title="Release the agent's claim and put the episode back in the queue"
                                                >
                                                    <X size={16} />
                                                </button>
                                            )}
                                            {['pending', 'failed', 'paused_daily_limit'].includes(item.status) && item.priority > 0 && (
                                                <button
                                                    type="button"
                                                    onClick={() => onAction(api.prioritizeQueueItem, item.id, "Bumped task priority")}
                                                    className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-750 text-indigo-600 rounded-lg transition-colors"
                                                    title="Prioritize to top"
                                                >
                                                    <ArrowUp size={16} />
                                                </button>
                                            )}
                                            {['pending', 'failed', 'paused_daily_limit'].includes(item.status) && (
                                                <button
                                                    type="button"
                                                    onClick={() => onAction(api.retryQueueItem, item.id, "Forced queue item retry")}
                                                    className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-750 text-emerald-600 rounded-lg transition-colors"
                                                    title="Retry now"
                                                >
                                                    <Play size={16} />
                                                </button>
                                            )}
                                            {['pending', 'running', 'failed', 'paused_daily_limit'].includes(item.status) && (
                                                <button
                                                    type="button"
                                                    onClick={() => onAction(api.cancelQueueItem, item.id, "Cancelled queue task")}
                                                    className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-750 text-rose-500 rounded-lg transition-colors"
                                                    title="Cancel task"
                                                >
                                                    <X size={16} />
                                                </button>
                                            )}
                                            {onDelete && (
                                                <button
                                                    type="button"
                                                    onClick={() => onDelete(item.id)}
                                                    className="p-1.5 hover:bg-rose-100 dark:hover:bg-rose-950/40 text-gray-400 hover:text-rose-500 rounded-lg transition-colors"
                                                    title="Delete from queue"
                                                >
                                                    <Trash2 size={15} />
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

            {/* Floating Bulk Action Bar */}
            <AnimatePresence>
                {selectedIds.size > 0 && (
                    <motion.div
                        initial={{ y: 50, opacity: 0 }}
                        animate={{ y: 0, opacity: 1 }}
                        exit={{ y: 50, opacity: 0 }}
                        className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 bg-gray-900/95 dark:bg-gray-800/95 backdrop-blur-md text-white px-5 py-3 rounded-2xl shadow-2xl border border-gray-700/60 flex items-center gap-4 text-sm"
                    >
                        <div className="flex items-center gap-2 pr-3 border-r border-gray-700">
                            <span className="w-2 h-2 rounded-full bg-indigo-500 animate-ping" />
                            <span className="font-semibold text-xs text-gray-200">
                                {selectedIds.size} task{selectedIds.size !== 1 ? 's' : ''} selected
                            </span>
                        </div>

                        <div className="flex items-center gap-2">
                            {activeTab === 'active' && (
                                <>
                                    <button
                                        type="button"
                                        onClick={handleBulkPrioritize}
                                        disabled={bulkProcessing}
                                        className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white rounded-xl text-xs font-semibold flex items-center gap-1.5 shadow-sm transition-all"
                                        title="Bump priority for selected pending tasks"
                                    >
                                        <ArrowUp size={13} /> Prioritize Selected
                                    </button>

                                    <button
                                        type="button"
                                        onClick={handleBulkRetry}
                                        disabled={bulkProcessing}
                                        className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white rounded-xl text-xs font-semibold flex items-center gap-1.5 shadow-sm transition-all"
                                        title="Retry selected pending or failed tasks"
                                    >
                                        <Play size={13} /> Retry Selected
                                    </button>

                                    <button
                                        type="button"
                                        onClick={handleBulkCancel}
                                        disabled={bulkProcessing}
                                        className="px-3 py-1.5 bg-rose-600 hover:bg-rose-700 disabled:opacity-50 text-white rounded-xl text-xs font-semibold flex items-center gap-1.5 shadow-sm transition-all"
                                        title="Cancel selected active tasks"
                                    >
                                        <X size={13} /> Cancel Selected
                                    </button>
                                </>
                            )}

                            {activeTab === 'history' && (
                                <button
                                    type="button"
                                    onClick={handleBulkRetry}
                                    disabled={bulkProcessing}
                                    className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white rounded-xl text-xs font-semibold flex items-center gap-1.5 shadow-sm transition-all"
                                    title="Re-enqueue selected history tasks"
                                >
                                    <Play size={13} /> Re-run Selected
                                </button>
                            )}

                            <button
                                type="button"
                                onClick={handleBulkDeleteAction}
                                disabled={bulkProcessing}
                                className="px-3 py-1.5 bg-rose-800/80 hover:bg-rose-800 disabled:opacity-50 text-white rounded-xl text-xs font-semibold flex items-center gap-1.5 shadow-sm transition-all"
                                title="Delete selected tasks from queue"
                            >
                                <Trash2 size={13} /> Delete Selected
                            </button>

                            <button
                                type="button"
                                onClick={() => setSelectedIds(new Set())}
                                className="px-2.5 py-1.5 text-xs text-gray-400 hover:text-gray-200 hover:bg-gray-800 rounded-lg transition-colors"
                            >
                                Clear
                            </button>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Agent Task Workspace Modal */}
            <AgentTaskModal 
                agentModalData={agentModalData} 
                onClose={() => setAgentModalData(null)} 
            />
        </div>
    );
};

export default QueueItemList;
