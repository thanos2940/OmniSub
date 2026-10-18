import React from 'react';
import { AlertCircle, Terminal, Copy, X } from 'lucide-react';
import { useToast } from '../context/ToastContext';
import { useJobs } from '../context/JobContext';
import { useQueueData } from './queue/useQueueData';
import QueueControls from './queue/QueueControls';
import QueueStats from './queue/QueueStats';
import QueueItemList, { renderColorizedLog } from './queue/QueueItemList';
import AgentRunnerPanel from './queue/AgentRunnerPanel';

const TranslationQueuePanel = () => {
    const toast = useToast();
    const { activeJobs } = useJobs();

    const {
        queueData,
        loading,
        error,
        activeTab,
        setActiveTab,
        selectedJobLogs,
        setSelectedJobLogs,
        checkingLimit,
        syncingTasks,
        fetchQueue,
        handleAction,
        handlePauseResume,
        handleCheckDailyLimit,
        handleClearHistory,
        handleCancelAll,
        handleClearQueue,
        handleDeleteQueueItem,
        handleBulkDelete,
        handleViewLogs,
        handleSyncTasks,
        syncingDisk,
        handleSyncDisk,
    } = useQueueData({ toast, activeJobs });

    const items = queueData.items || [];
    const summary = queueData.summary || {};
    const rateLimits = queueData.rate_limits || {};
    const hasDailyLimit = Object.values(rateLimits).some(s => s.daily_exhausted)
        || (summary.paused_daily_limit || 0) > 0;

    const activeCount = items.filter(i => ['pending', 'queued', 'running', 'paused_daily_limit', 'postponed'].includes(i.status)).length;
    const historyCount = items.filter(i => !['pending', 'queued', 'running', 'paused_daily_limit', 'postponed'].includes(i.status)).length;

    const getModalLogs = () => {
        if (!selectedJobLogs) return [];
        const runningJob = Object.values(activeJobs || {}).find(j => j.id === `bg_${selectedJobLogs.itemId}`);
        return runningJob ? (runningJob.logs || []) : (selectedJobLogs.logs || []);
    };
    const modalLogs = getModalLogs();

    return (
        <div className="space-y-6">
            {/* Header Controls */}
            <QueueControls
                paused={queueData.paused}
                hasDailyLimit={hasDailyLimit}
                checkingLimit={checkingLimit}
                loading={loading}
                syncingTasks={syncingTasks}
                syncingDisk={syncingDisk}
                activeCount={activeCount}
                historyCount={historyCount}
                onPauseResume={handlePauseResume}
                onCheckDailyLimit={handleCheckDailyLimit}
                onClearHistory={handleClearHistory}
                onCancelAll={handleCancelAll}
                onClearQueue={handleClearQueue}
                onRefresh={fetchQueue}
                onSyncTasks={handleSyncTasks}
                onSyncDisk={handleSyncDisk}
            />

            {error && (
                <div className="p-4 bg-rose-50 dark:bg-rose-950/20 border border-rose-200 dark:border-rose-900/50 rounded-xl text-rose-700 dark:text-rose-400 flex gap-2">
                    <AlertCircle size={20} className="shrink-0" />
                    <div>{error}</div>
                </div>
            )}

            {/* Direct agent runner */}
            <AgentRunnerPanel toast={toast} />

            {/* Metric Summary & Quotas */}
            <QueueStats
                summary={summary}
                rateLimits={rateLimits}
            />

            {/* Tasks Table & Tabs */}
            <QueueItemList
                items={items}
                activeTab={activeTab}
                setActiveTab={setActiveTab}
                onViewLogs={handleViewLogs}
                onAction={handleAction}
                onDelete={handleDeleteQueueItem}
                onBulkDelete={handleBulkDelete}
                onRefresh={fetchQueue}
            />


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
                                    type="button"
                                    onClick={() => {
                                        const logsText = modalLogs.join('\n');
                                        navigator.clipboard.writeText(logsText);
                                        const btn = document.getElementById("copy-modal-btn");
                                        if (btn) {
                                            const origText = btn.innerText;
                                            btn.innerText = "Copied!";
                                            setTimeout(() => { if (btn) btn.innerText = origText; }, 2000);
                                        }
                                    }}
                                    id="copy-modal-btn"
                                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-lg transition-colors border border-gray-700 font-sans"
                                >
                                    <Copy size={12} />
                                    Copy Logs
                                </button>
                                <button
                                    type="button"
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
