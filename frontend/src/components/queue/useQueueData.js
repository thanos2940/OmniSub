import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../../api';
import { usePoll } from '../../hooks/usePoll';

const QUEUE_EVENTS = ['queue_updated', 'agent_submission', 'sync_completed', 'agent_runner_submitted'];

export const useQueueData = ({ toast, activeJobs }) => {
    const [queueData, setQueueData] = useState({ items: [], summary: {}, rate_limits: {}, paused: false });
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [selectedJobLogs, setSelectedJobLogs] = useState(null);
    const [checkingLimit, setCheckingLimit] = useState(false);
    const isMounted = useRef(true);

    const loadQueue = useCallback(async ({ signal } = {}) => {
        try {
            const res = await api.getQueue(false, undefined, signal);
            if (isMounted.current) {
                setQueueData(res.data || { items: [], summary: {}, rate_limits: {} });
                setError(null);
            }
        } catch (e) {
            if (signal?.aborted) return;
            console.error("Failed to fetch queue:", e);
            if (isMounted.current) {
                setError("Failed to fetch queue data");
            }
            throw e; // lets the poller back off
        } finally {
            if (isMounted.current) {
                setLoading(false);
            }
        }
    }, []);

    // The queue list: every 10 s (60 s hidden), and at once when the server says it changed.
    const { refresh } = usePoll(loadQueue, { intervalMs: 10000, hiddenIntervalMs: 60000, refreshOn: QUEUE_EVENTS });
    const fetchQueue = useCallback(() => refresh().catch(() => {}), [refresh]);

    useEffect(() => {
        isMounted.current = true;
        const handleAgentSubmitted = (e) => {
            const detail = e.detail || {};
            if (toast?.success) {
                toast.success(`Agent translation submitted: ${detail.project_name || 'Project'} ${detail.episode_name || ''}`);
            }
        };
        window.addEventListener('omnisub:agent-submitted', handleAgentSubmitted);
        return () => {
            isMounted.current = false;
            window.removeEventListener('omnisub:agent-submitted', handleAgentSubmitted);
        };
    }, [toast]);

    const handleAction = useCallback(async (actionFn, itemId, successMsg) => {
        try {
            await actionFn(itemId);
            if (toast?.success) toast.success(successMsg);
            fetchQueue();
        } catch (e) {
            console.error(`Action failed:`, e);
            if (toast?.error) toast.error(`Failed to perform action: ${e.response?.data?.message || e.response?.data?.detail || e.message}`);
        }
    }, [fetchQueue, toast]);

    const handlePauseResume = useCallback(async () => {
        try {
            if (queueData.paused) {
                await api.resumeQueue();
                if (toast?.success) toast.success('Translations resumed.');
            } else {
                await api.pauseQueue();
                if (toast?.success) toast.success('Translations paused.');
            }
            fetchQueue();
        } catch (e) {
            console.error("Failed to toggle queue pause:", e);
            if (toast?.error) toast.error('Failed to toggle queue worker state');
        }
    }, [queueData.paused, fetchQueue, toast]);

    const handleCheckDailyLimit = useCallback(async () => {
        setCheckingLimit(true);
        try {
            const res = await api.checkDailyLimit();
            const cleared = res.data.cleared || [];
            if (cleared.length > 0) {
                if (toast?.success) toast.success(`Daily limit lifted — resuming: ${cleared.join(', ')}`);
            } else if ((res.data.checked || 0) === 0) {
                if (toast?.info) toast.info('No models are currently daily-limited.');
            } else {
                if (toast?.info) toast.info('Daily limit still in effect. Will keep auto-checking.');
            }
            fetchQueue();
        } catch (e) {
            console.error("Failed to check daily limit:", e);
            if (toast?.error) toast.error('Failed to check daily limit');
        } finally {
            if (isMounted.current) {
                setCheckingLimit(false);
            }
        }
    }, [fetchQueue, toast]);

    const handleClearHistory = useCallback(async () => {
        try {
            const res = await api.clearQueueHistory();
            if (toast?.success) toast.success(`Cleared ${res.data.cleared_count} history items`);
            fetchQueue();
        } catch (e) {
            console.error("Failed to clear queue history:", e);
            if (toast?.error) toast.error('Failed to clear queue history');
        }
    }, [fetchQueue, toast]);

    const handleViewLogs = useCallback(async (itemId, title) => {
        try {
            let logs = [];
            // A running queue row's card carries no logs of its own (the activity snapshot lists work,
            // not log lines); ask the queue for them unless a live job already has some.
            const runningJob = Object.values(activeJobs || {}).find(j => j.id === `bg_${itemId}`);
            if (runningJob && Array.isArray(runningJob.logs) && runningJob.logs.length > 0) {
                logs = runningJob.logs;
            } else {
                const res = await api.getQueueItemLogs(itemId);
                logs = res.data.logs || [];
            }
            if (isMounted.current) {
                setSelectedJobLogs({ title, itemId, logs });
            }
        } catch (e) {
            console.error("Failed to fetch logs:", e);
            if (toast?.error) toast.error('Failed to load task logs');
        }
    }, [activeJobs, toast]);

    const [syncingTasks, setSyncingTasks] = useState(false);

    const handleSyncTasks = useCallback(async () => {
        setSyncingTasks(true);
        try {
            const res = await api.syncAgentTasks();
            const released = res.data?.released_claims?.length || 0;
            const reconciled = res.data?.reconciled_queue_items || 0;
            if (released > 0 || reconciled > 0) {
                if (toast?.success) toast.success(`Synced: ${released} abandoned agent claim(s) released, ${reconciled} already-translated item(s) completed.`);
            } else {
                if (toast?.info) toast.info('All agent tasks and queue items are up to date.');
            }
            fetchQueue();
        } catch (e) {
            console.error("Failed to sync agent tasks:", e);
            if (toast?.error) toast.error('Failed to sync agent tasks');
        } finally {
            if (isMounted.current) {
                setSyncingTasks(false);
            }
        }
    }, [fetchQueue, toast]);

    const [syncingDisk, setSyncingDisk] = useState(false);

    const handleSyncDisk = useCallback(async () => {
        setSyncingDisk(true);
        try {
            const res = await api.syncDisk({ re_queue_removed: true });
            const data = res.data || {};
            const found = data.total_translations_found || 0;
            const removed = data.total_translations_removed || 0;
            const requeued = data.total_queue_requeued || 0;
            if (found > 0 || removed > 0 || requeued > 0) {
                if (toast?.success) toast.success(`Disk sync complete: ${found} found, ${removed} removed, ${requeued} re-queued.`);
            } else {
                if (toast?.info) toast.info('All disk subtitles and project statuses are up to date.');
            }
            fetchQueue();
        } catch (e) {
            console.error("Failed to sync disk subtitles:", e);
            if (toast?.error) toast.error('Failed to sync disk subtitles');
        } finally {
            if (isMounted.current) {
                setSyncingDisk(false);
            }
        }
    }, [fetchQueue, toast]);

    // 'active': everything waiting and running (episodes being translated stop at once); 'pending': waiting only.
    const handleCancelAll = useCallback(async (status = 'active') => {
        try {
            const res = await api.cancelAllQueueItems(status);
            const n = res.data?.cancelled_count ?? 0;
            if (toast?.success) toast.success(`Cancelled ${n} translation${n === 1 ? '' : 's'}.`);
            fetchQueue();
        } catch (e) {
            console.error("Failed to cancel all tasks:", e);
            if (toast?.error) toast.error(`Failed to cancel all tasks: ${e.response?.data?.detail || e.message}`);
        }
    }, [fetchQueue, toast]);

    const handleClearQueue = useCallback(async (status = 'history') => {
        try {
            const res = await api.clearQueue(status);
            if (status === 'all' || status === 'active' || status === 'pending') {
                try {
                    await api.clearAgentTasks(status);
                } catch {
                    // optional
                }
            }
            const label = status === 'all' ? 'all' : status === 'pending' ? 'pending' : status === 'active' ? 'active' : 'history';
            if (toast?.success) toast.success(`Cleared ${res.data?.cleared_count ?? 0} ${label} queue item(s)`);
            fetchQueue();
        } catch (e) {
            console.error(`Failed to clear queue (${status}):`, e);
            if (toast?.error) toast.error(`Failed to clear queue: ${e.response?.data?.detail || e.message}`);
        }
    }, [fetchQueue, toast]);

    const handleDeleteQueueItem = useCallback(async (itemId) => {
        try {
            await api.deleteQueueItem(itemId);
            if (toast?.success) toast.success('Queue item removed');
            fetchQueue();
        } catch (e) {
            console.error("Failed to delete queue item:", e);
            if (toast?.error) toast.error(`Failed to delete queue item: ${e.response?.data?.detail || e.message}`);
        }
    }, [fetchQueue, toast]);

    const handleBulkDelete = useCallback(async (itemIds) => {
        if (!itemIds || itemIds.length === 0) return;
        try {
            await Promise.all(itemIds.map(id => api.deleteQueueItem(id).catch(err => console.warn(`Failed to delete ${id}:`, err))));
            if (toast?.success) toast.success(`Deleted ${itemIds.length} item(s)`);
            fetchQueue();
        } catch (e) {
            console.error("Failed to bulk delete queue items:", e);
            if (toast?.error) toast.error('Failed to delete selected queue items');
        }
    }, [fetchQueue, toast]);

    return {
        queueData,
        loading,
        error,
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
    };
};

