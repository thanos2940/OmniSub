import { useCallback, useMemo, useState } from 'react';
import { api } from '../../api';
import { useJobs } from '../../context/JobContext';
import { useToast } from '../../context/ToastContext';
import { backgroundJobs, jobTitle, liveQueueRows } from './liveWork';

const errorText = (err) => err?.response?.data?.detail || err?.response?.data?.message || err?.message || 'unknown error';

// The live work from the shared activity snapshot (no request of its own) and what can be done about it:
// cancel or put back an episode, cancel or dismiss a background job, read a job's log.
export function useLiveWork() {
    const { snapshot, activeJobs, cancelJob, removeJob, refresh } = useJobs();
    const toast = useToast();
    const [logs, setLogs] = useState(null);   // { title, lines } for QueueLogsModal

    const rows = useMemo(() => liveQueueRows(snapshot, activeJobs), [snapshot, activeJobs]);
    const { live: jobs, finished } = useMemo(() => backgroundJobs(activeJobs), [activeJobs]);

    const cancelRow = useCallback(async (id) => {
        try {
            await api.cancelQueueItem(id);
            toast.success('Cancelled.');
        } catch (err) {
            toast.error(`Could not cancel it: ${errorText(err)}`);
        }
        refresh();
    }, [toast, refresh]);

    const releaseRow = useCallback(async (id) => {
        try {
            await api.releaseQueueItem(id);
            toast.success('Put back in the queue.');
        } catch (err) {
            toast.error(errorText(err));
        }
        refresh();
    }, [toast, refresh]);

    const cancelBackgroundJob = useCallback(async (id) => {
        await cancelJob(id);
        toast.info('Cancel requested.');
    }, [cancelJob, toast]);

    // The snapshot carries the last 50 log lines; the job itself has them all.
    const showLogs = useCallback(async (job) => {
        const title = jobTitle(job);
        setLogs({ title, lines: job.logs || [] });
        try {
            const res = await api.getJob(job.id);
            if (Array.isArray(res.data?.logs)) setLogs({ title, lines: res.data.logs });
        } catch {
            // keep the lines the snapshot had
        }
    }, []);

    return {
        snapshot, rows, jobs, finished, logs,
        closeLogs: useCallback(() => setLogs(null), []),
        cancelRow, releaseRow, cancelJob: cancelBackgroundJob, dismissJob: removeJob, showLogs,
    };
}
