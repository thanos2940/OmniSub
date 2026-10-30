import { useState, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { AlertCircle, Ban, FolderSync, History, Plane, ListTodo, Sparkles, Trash2, XCircle } from 'lucide-react';
import { useJobs } from '../../context/JobContext';
import { useToast } from '../../context/ToastContext';
import { useQueueData } from '../queue/useQueueData';
import { isQueueStatus } from '../queue/queueStatus';
import AgentRunnerPanel from '../queue/AgentRunnerPanel';
import QueueStats from '../queue/QueueStats';
import QueueItemList from '../queue/QueueItemList';
import QueueLogsModal from '../queue/QueueLogsModal';
import AutopilotPanel from '../autopilot/AutopilotPanel';
import { ConfirmDialog, OverflowMenu, Page, PageHeader, Spinner, Tabs } from '../ui';
import useRunnerStatus from './useRunnerStatus';

const TAB_IDS = ['queue', 'history', 'autopilot'];

export default function ActivityPage() {
    const toast = useToast();
    const { activeJobs } = useJobs();
    const [searchParams, setSearchParams] = useSearchParams();
    const tab = TAB_IDS.includes(searchParams.get('tab')) ? searchParams.get('tab') : 'queue';
    const [confirm, setConfirm] = useState(null); // { title, message, confirmLabel, run }
    const [confirming, setConfirming] = useState(false);

    const runner = useRunnerStatus({ intervalMs: 3000, withLog: true });
    const {
        queueData, loading, error, selectedJobLogs, setSelectedJobLogs, checkingLimit, fetchQueue,
        handleAction, handlePauseResume, handleCheckDailyLimit, handleCancelAll, handleClearQueue,
        handleDeleteQueueItem, handleBulkDelete, handleViewLogs, handleSyncTasks, handleSyncDisk,
    } = useQueueData({ toast, activeJobs });

    const items = useMemo(() => queueData.items || [], [queueData.items]);
    const queueCount = useMemo(() => items.filter(i => isQueueStatus(i.status)).length, [items]);
    const historyCount = items.length - queueCount;

    const logs = useMemo(() => {
        if (!selectedJobLogs) return null;
        const live = Object.values(activeJobs || {}).find(j => j.id === `bg_${selectedJobLogs.itemId}`);
        return { title: selectedJobLogs.title, lines: live ? (live.logs || []) : (selectedJobLogs.logs || []) };
    }, [selectedJobLogs, activeJobs]);

    const setTab = (id) => setSearchParams(id === 'queue' ? {} : { tab: id }, { replace: true });

    const ask = (title, message, confirmLabel, run) => setConfirm({ title, message, confirmLabel, run });
    const runConfirmed = async () => {
        setConfirming(true);
        try {
            await confirm.run();
        } finally {
            setConfirming(false);
            setConfirm(null);
        }
    };

    const tabs = [
        { id: 'queue', label: 'Queue', icon: ListTodo, count: queueCount },
        { id: 'history', label: 'History', icon: History, count: historyCount },
        { id: 'autopilot', label: 'Autopilot', icon: Plane },
    ];

    const maintenance = [
        { label: 'Sync subtitles on disk', icon: FolderSync, onClick: handleSyncDisk },
        { label: 'Sync agent tasks', icon: Sparkles, onClick: handleSyncTasks },
        {
            label: 'Cancel waiting translations', icon: Ban, danger: true,
            onClick: () => ask('Cancel the waiting translations?', 'Every translation that has not started is cancelled. Episodes being translated right now finish.', 'Cancel them', () => handleCancelAll('pending')),
        },
        {
            label: 'Cancel all translations', icon: Ban, danger: true,
            onClick: () => ask('Cancel all translations?', 'Every waiting translation is cancelled, and the episodes being translated right now stop at once (nothing is saved for them). Subtitles already written stay.', 'Cancel all', () => handleCancelAll('active')),
        },
        { label: 'Clear finished history', icon: Trash2, onClick: () => handleClearQueue('history') },
        { label: 'Clear cancelled items', icon: XCircle, onClick: () => handleClearQueue('cancelled') },
        {
            label: 'Clear the whole queue', icon: Trash2, danger: true,
            onClick: () => ask('Clear the whole queue?', 'Every queue record and every agent workspace is removed, including work that is waiting. Subtitles already written stay.', 'Clear everything', () => handleClearQueue('all')),
        },
    ];

    return (
        <Page>
            <PageHeader
                title="Activity"
                subtitle="What is translating now, what waits, and what Autopilot has picked up."
                actions={<OverflowMenu items={maintenance} label="Maintenance actions" />}
            />

            <div className="space-y-5">
                <AgentRunnerPanel runner={runner} queuePaused={queueData.paused} onTogglePause={handlePauseResume} toast={toast} />

                <Tabs tabs={tabs} value={tab} onChange={setTab} className="!mb-0" />

                {tab !== 'autopilot' && error && (
                    <p className="flex items-center gap-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-950/30 dark:text-rose-300">
                        <AlertCircle size={16} className="shrink-0" /> {error}
                    </p>
                )}

                {tab === 'queue' && (
                    loading ? <Spinner label="Loading the queue..." /> : (
                        <>
                            <QueueStats
                                summary={queueData.summary || {}}
                                rateLimits={queueData.rate_limits || {}}
                                engine={runner.engine}
                                onCheckDailyLimit={handleCheckDailyLimit}
                                checkingLimit={checkingLimit}
                            />
                            <QueueItemList
                                items={items}
                                view="queue"
                                onViewLogs={handleViewLogs}
                                onAction={handleAction}
                                onDelete={handleDeleteQueueItem}
                                onBulkDelete={handleBulkDelete}
                                onRefresh={fetchQueue}
                            />
                        </>
                    )
                )}
                {tab === 'history' && (
                    loading ? <Spinner label="Loading the history..." /> : (
                        <QueueItemList
                            items={items}
                            view="history"
                            onViewLogs={handleViewLogs}
                            onAction={handleAction}
                            onDelete={handleDeleteQueueItem}
                            onBulkDelete={handleBulkDelete}
                            onRefresh={fetchQueue}
                        />
                    )
                )}
                {tab === 'autopilot' && <AutopilotPanel />}
            </div>

            <QueueLogsModal logs={logs} onClose={() => setSelectedJobLogs(null)} />

            <ConfirmDialog
                open={!!confirm}
                title={confirm?.title}
                message={confirm?.message}
                confirmLabel={confirm?.confirmLabel}
                danger
                busy={confirming}
                onConfirm={runConfirmed}
                onCancel={() => setConfirm(null)}
            />
        </Page>
    );
}
