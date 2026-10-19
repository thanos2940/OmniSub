import { useEffect, useRef } from 'react';
import { useJobs } from '../../context/JobContext';

const belongsTo = (job, projectName) =>
    !!job && (job.project_name === projectName || job.id?.includes(projectName) || job.description?.includes(projectName)
        || job.metadata?.projectId === projectName);

// Follows the background jobs of one kind for this project: returns the one running now and calls
// onCompleted(job) / onFailed(job) once per finished job (a completed job is removed from the tracker).
export function useProjectJob(types, projectName, { onCompleted, onFailed } = {}) {
    const { activeJobs, removeJob } = useJobs();
    const handled = useRef(new Set());
    const callbacks = useRef({});
    callbacks.current = { onCompleted, onFailed };
    const typeKey = types.join('|');

    const mine = Object.values(activeJobs || {}).filter(j => j && types.includes(j.type) && belongsTo(j, projectName));
    const running = mine.find(j => j.status === 'running' || j.status === 'pending') || null;

    useEffect(() => {
        Object.values(activeJobs || {}).forEach(job => {
            if (!job || !typeKey.split('|').includes(job.type) || !belongsTo(job, projectName) || handled.current.has(job.id)) return;
            if (job.status === 'completed') {
                handled.current.add(job.id);
                callbacks.current.onCompleted?.(job);
                removeJob?.(job.id);
            } else if (job.status === 'failed') {
                handled.current.add(job.id);
                callbacks.current.onFailed?.(job);
            }
        });
    }, [activeJobs, projectName, removeJob, typeKey]);

    return running;
}
