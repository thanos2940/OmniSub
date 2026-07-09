import React, { useState } from 'react';
import { useJobs } from '../context/JobContext';
import { X, ChevronDown, ChevronUp, Activity, CheckCircle, AlertCircle, Terminal, ExternalLink, Sparkles, Maximize2, Minimize2, Copy } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';

const renderColorizedLog = (log) => {
    if (!log) return null;
    
    // Check keywords to determine color class
    let colorClass = "text-gray-300"; // default
    
    const lowerLog = log.toLowerCase();
    
    if (lowerLog.includes("failed") || lowerLog.includes("failure") || lowerLog.includes("error") || lowerLog.includes("exception")) {
        colorClass = "text-red-400 font-semibold"; // Red
    } else if (lowerLog.includes("warning") || lowerLog.includes("incomplete") || lowerLog.includes("limit")) {
        colorClass = "text-amber-500 font-medium"; // Orange/Amber
    } else if (lowerLog.includes("export") || lowerLog.includes("saved") || lowerLog.includes("save") || lowerLog.includes("stored") || lowerLog.includes("checkpoint file")) {
        colorClass = "text-emerald-400"; // Green
    } else if (lowerLog.includes("translating") || lowerLog.includes("translation") || lowerLog.includes("retranslate") || lowerLog.includes("reasoning:") || lowerLog.includes("reused")) {
        colorClass = "text-purple-400"; // Purple
    } else if (lowerLog.includes("running") || lowerLog.includes("starting") || lowerLog.includes("initializing") || lowerLog.includes("syncing") || lowerLog.includes("extracting") || lowerLog.includes("reviewing")) {
        colorClass = "text-blue-400"; // Blue
    }
    
    return <span className={colorClass}>{log}</span>;
};

const JobProgressWidget = () => {
    const { activeJobs, removeJob, cancelJob } = useJobs();
    const [isExpanded, setIsExpanded] = useState(true);
    const [showLogs, setShowLogs] = useState(null); // jobId to show logs for
    const [maximizedLogsJobId, setMaximizedLogsJobId] = useState(null);
    const navigate = useNavigate();

    const jobs = Object.values(activeJobs);
    // Don't show pipeline jobs here (they have their own stepper)
    const nonPipelineJobs = jobs.filter(j => j.type !== 'pipeline');
    if (nonPipelineJobs.length === 0) return null;

    const isActive = (job) => ['running', 'pending', 'awaiting_review'].includes(job.status);

    return (
        <div className="fixed bottom-4 right-4 z-50 w-96 flex flex-col gap-2">
            <AnimatePresence>
                {isExpanded && (
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: 20 }}
                        className="bg-white dark:bg-gray-800 rounded-lg shadow-2xl border border-gray-200 dark:border-gray-700 overflow-hidden max-h-[80vh] flex flex-col"
                    >
                        <div className="p-3 bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center">
                            <h3 className="font-semibold text-sm text-gray-700 dark:text-gray-300 flex items-center gap-2">
                                <Activity size={16} className="text-indigo-500" />
                                Active Tasks ({nonPipelineJobs.length})
                            </h3>
                            <button onClick={() => setIsExpanded(false)} className="text-gray-500 hover:text-gray-700">
                                <ChevronDown size={16} />
                            </button>
                        </div>

                        <div className="overflow-y-auto p-2 space-y-2">
                            {nonPipelineJobs.map(job => (
                                <div key={job.id} className="bg-gray-50 dark:bg-gray-700/50 rounded-lg p-3 border border-gray-100 dark:border-gray-600">
                                    <div className="flex justify-between items-start mb-2">
                                        <div>
                                            <p className="font-medium text-sm text-gray-800 dark:text-gray-200">
                                                {job.metadata?.episodeName ? (
                                                    <span className="flex flex-col">
                                                        <span className="text-[10px] uppercase tracking-wider text-indigo-500 font-bold">{job.metadata.projectId}</span>
                                                        <span>{job.metadata.episodeName}</span>
                                                    </span>
                                                ) : job.description}
                                            </p>
                                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{job.message}</p>
                                        </div>
                                        <div className="flex items-center gap-2">
                                            {job.status === 'completed' && <CheckCircle size={16} className="text-green-500" />}
                                            {job.status === 'failed' && <AlertCircle size={16} className="text-red-500" />}
                                            {job.status === 'cancelled' && <X size={16} className="text-gray-400" />}
                                            {job.status === 'running' && (
                                                <div className="relative">
                                                    <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                                                    {job.logs.some(l => l.includes("Reasoning:")) && (
                                                        <Sparkles size={10} className="absolute -top-1 -right-1 text-yellow-500 animate-pulse" />
                                                    )}
                                                </div>
                                            )}
                                            {isActive(job) && (
                                                <button onClick={() => cancelJob(job.id)} className="flex items-center gap-1 text-xs text-red-400 hover:text-red-600 font-medium px-2 py-1 rounded bg-red-50 dark:bg-red-900/20" title="Cancel Job">
                                                    Cancel
                                                </button>
                                            )}
                                            <button
                                                onClick={() => {
                                                    if (isActive(job)) {
                                                        if (window.confirm("This job is still active. Dismissing it will hide it from the UI but it will continue running in the background. Are you sure?")) {
                                                            removeJob(job.id);
                                                        }
                                                    } else {
                                                        removeJob(job.id);
                                                    }
                                                }}
                                                className="text-gray-400 hover:text-gray-600 ml-1"
                                                title="Dismiss"
                                            >
                                                <X size={16} />
                                            </button>
                                        </div>
                                    </div>

                                    {/* Progress Bar */}
                                    <div className="w-full bg-gray-200 dark:bg-gray-600 rounded-full h-1.5 mb-2 overflow-hidden">
                                        <div
                                            className={`h-full rounded-full transition-all duration-500 ${job.status === 'failed' ? 'bg-red-500' :
                                                job.status === 'completed' ? 'bg-green-500' : 'bg-indigo-500'
                                                }`}
                                            style={{ width: `${job.progress}%` }}
                                        />
                                    </div>

                                    <div className="flex items-center justify-between mt-1">
                                        {/* Logs Toggle */}
                                        <button
                                            onClick={() => setShowLogs(showLogs === job.id ? null : job.id)}
                                            className="text-xs text-indigo-600 hover:text-indigo-800 flex items-center gap-1"
                                        >
                                            <Terminal size={12} />
                                            {showLogs === job.id ? 'Hide Logs' : 'Show Logs'}
                                        </button>

                                        {/* Navigation Link */}
                                        {job.metadata?.link && (
                                            <button
                                                onClick={() => navigate(job.metadata.link, { state: { highlightJobId: job.id } })}
                                                className="text-xs text-blue-600 hover:text-blue-800 flex items-center gap-1"
                                            >
                                                <ExternalLink size={12} />
                                                View
                                            </button>
                                        )}
                                    </div>

                                    {/* Episodes Progress */}
                                    {(job.completed_episodes?.length > 0 || job.failed_episodes?.length > 0) && (
                                        <div className="mt-2 mb-1 flex items-center justify-between text-[10px] font-bold text-gray-500 uppercase tracking-wider">
                                            <span>Episode Progress</span>
                                            <span className="text-indigo-600 dark:text-indigo-400">
                                                {job.completed_episodes?.length || 0} / {(job.completed_episodes?.length || 0) + (job.failed_episodes?.length || 0)}
                                            </span>
                                        </div>
                                    )}

                                    {/* Daily Limit Warning */}
                                    {job.daily_limit_reached && (
                                        <div className="mt-2 mb-1 p-2 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700 rounded-md flex items-start gap-2">
                                            <AlertCircle size={14} className="text-amber-500 shrink-0 mt-0.5" />
                                            <div className="text-[10px] text-amber-700 dark:text-amber-400">
                                                <p className="font-bold">Daily API Limit Reached</p>
                                                <p>Remaining episodes queued. Check settings for RPM/RPD limits.</p>
                                            </div>
                                        </div>
                                    )}

                                    {/* Scenes Progress */}
                                    {job.scene_status && Object.keys(job.scene_status).length > 0 && (
                                        <div className="mt-2 flex flex-wrap gap-1">
                                            {Object.entries(job.scene_status).map(([sceneName, status]) => {
                                                let bgColor = 'bg-gray-200 text-gray-600 dark:bg-gray-600 dark:text-gray-300';
                                                if (status === 'running') bgColor = 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 border border-blue-400';
                                                if (status === 'completed') bgColor = 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300';
                                                if (status === 'failed') bgColor = 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300 border border-red-400';
                                                
                                                return (
                                                    <span key={sceneName} className={`text-[10px] px-1.5 py-0.5 rounded ${bgColor}`}>
                                                        {sceneName.replace('Scene ', 'S')}
                                                    </span>
                                                );
                                            })}
                                        </div>
                                    )}

                                    {/* Logs View */}
                                    <AnimatePresence>
                                        {showLogs === job.id && (
                                            <motion.div
                                                initial={{ height: 0, opacity: 0 }}
                                                animate={{ height: 'auto', opacity: 1 }}
                                                exit={{ height: 0, opacity: 0 }}
                                                className="mt-2 bg-black text-gray-300 p-2 rounded text-xs font-mono overflow-hidden"
                                            >
                                                <div className="flex justify-between items-center border-b border-gray-800 pb-1 mb-1 bg-black shrink-0">
                                                    <span className="text-[10px] text-gray-500 uppercase tracking-wider">Console Output</span>
                                                    <button
                                                        onClick={() => setMaximizedLogsJobId(job.id)}
                                                        className="text-[10px] text-indigo-400 hover:text-indigo-300 flex items-center gap-0.5"
                                                    >
                                                        <Maximize2 size={10} /> Maximize
                                                    </button>
                                                </div>
                                                <div className="max-h-32 overflow-y-auto space-y-1">
                                                    {job.logs.slice().reverse().map((log, i) => (
                                                        <div key={i}>{renderColorizedLog(log)}</div>
                                                    ))}
                                                </div>
                                            </motion.div>
                                        )}
                                    </AnimatePresence>
                                </div>
                            ))}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {!isExpanded && (
                <motion.button
                    initial={{ scale: 0.9, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    onClick={() => setIsExpanded(true)}
                    className="bg-white dark:bg-gray-800 p-3 rounded-full shadow-lg border border-gray-200 dark:border-gray-700 flex items-center gap-2 self-end hover:bg-gray-50 transition-colors"
                >
                    <Activity size={20} className="text-indigo-500" />
                    <span className="font-semibold text-sm text-gray-700 dark:text-gray-300">{jobs.length}</span>
                </motion.button>
            )}

            {/* Maximized Logs Overlay */}
            <AnimatePresence>
                {maximizedLogsJobId && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[100] flex items-center justify-center p-4 md:p-10"
                    >
                        <motion.div
                            initial={{ scale: 0.95, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            exit={{ scale: 0.95, opacity: 0 }}
                            className="bg-gray-950 text-gray-100 rounded-xl shadow-2xl border border-gray-800 w-full max-w-4xl h-[80vh] flex flex-col overflow-hidden font-mono"
                        >
                            <div className="p-4 bg-gray-900 border-b border-gray-800 flex justify-between items-center shrink-0">
                                <div className="flex items-center gap-2">
                                    <Terminal size={18} className="text-indigo-400" />
                                    <span className="font-semibold text-sm">
                                        Logs: {activeJobs[maximizedLogsJobId]?.metadata?.episodeName || activeJobs[maximizedLogsJobId]?.description || 'Job Console'}
                                    </span>
                                </div>
                                <div className="flex items-center gap-3">
                                    <button
                                        onClick={() => {
                                            const logsText = activeJobs[maximizedLogsJobId]?.logs.join('\n') || '';
                                            navigator.clipboard.writeText(logsText);
                                            const btn = document.getElementById("copy-btn");
                                            if (btn) {
                                                const origText = btn.innerText;
                                                btn.innerText = "Copied!";
                                                setTimeout(() => btn.innerText = origText, 2000);
                                            }
                                        }}
                                        id="copy-btn"
                                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-lg transition-colors border border-gray-700 font-sans"
                                    >
                                        <Copy size={12} />
                                        Copy Logs
                                    </button>
                                    <button
                                        onClick={() => setMaximizedLogsJobId(null)}
                                        className="p-1 hover:bg-gray-850 rounded-lg transition-colors text-gray-400 hover:text-gray-200"
                                    >
                                        <X size={20} />
                                    </button>
                                </div>
                            </div>
                            <div className="flex-1 p-4 overflow-y-auto space-y-1.5 text-xs select-text">
                                {activeJobs[maximizedLogsJobId]?.logs.map((log, i) => (
                                    <div key={i} className="whitespace-pre-wrap leading-relaxed">
                                        {renderColorizedLog(log)}
                                    </div>
                                ))}
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
};

export default JobProgressWidget;
