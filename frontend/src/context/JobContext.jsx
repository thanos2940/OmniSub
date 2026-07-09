import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { api } from '../api';

const JobContext = createContext();

export const useJobs = () => useContext(JobContext);

const STORAGE_KEY = 'omnisub_active_jobs';

const loadPersistedJobs = () => {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return {};
        const parsed = JSON.parse(raw);
        // Only resume jobs worth polling — drop any that already finished.
        const live = {};
        for (const [id, job] of Object.entries(parsed)) {
            if (!['completed', 'failed', 'cancelled'].includes(job.status)) {
                live[id] = job;
            }
        }
        return live;
    } catch {
        return {};
    }
};

export const JobProvider = ({ children }) => {
    const [activeJobs, setActiveJobs] = useState(loadPersistedJobs);
    const [isPolling, setIsPolling] = useState(() => Object.keys(loadPersistedJobs()).length > 0);
    const isPollingRef = useRef(false);
    const activeJobsRef = useRef({});
    // Per-job consecutive poll-error counter. A single failed poll (server restart,
    // transient 502) must NOT mark a still-running job terminally 'failed' — the job
    // keeps running server-side. Only give up after several consecutive failures.
    const pollErrorsRef = useRef({});
    const MAX_POLL_ERRORS = 5;

    // Keep activeJobsRef in sync, and persist so a page reload re-hydrates in-flight
    // jobs instead of orphaning them (they keep running server-side).
    useEffect(() => {
        activeJobsRef.current = activeJobs;
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(activeJobs));
        } catch {
            // ignore quota / serialization errors
        }
    }, [activeJobs]);

    const addJob = (jobId, type, description, metadata = {}) => {
        setActiveJobs(prev => ({
            ...prev,
            [jobId]: {
                id: jobId,
                type,
                description,
                metadata,
                status: 'pending',
                progress: 0,
                message: 'Initializing...',
                logs: [],
                pipeline_stage: null,
                pipeline_mode: null,
            }
        }));
        setIsPolling(true);
    };

    const cancelJob = async (jobId) => {
        try {
            await api.cancelJob(jobId);
            setActiveJobs(prev => ({
                ...prev,
                [jobId]: { ...prev[jobId], status: 'cancelled', message: 'Cancelled by user' }
            }));
        } catch (err) {
            console.error(`Failed to cancel job ${jobId}`, err);
        }
    };

    useEffect(() => {
        if (!isPolling) return;

        const pollJobs = async () => {
            if (isPollingRef.current) return;
            isPollingRef.current = true;

            try {
                const currentJobs = activeJobsRef.current;
                const jobsToPoll = Object.values(currentJobs).filter(
                    job => !['completed', 'failed', 'cancelled'].includes(job.status)
                );

                if (jobsToPoll.length === 0) {
                    setIsPolling(false);
                    isPollingRef.current = false;
                    return;
                }

                const updates = {};

                await Promise.all(jobsToPoll.map(async (job) => {
                    try {
                        const res = await api.getJob(job.id);
                        const s = res.data;
                        pollErrorsRef.current[job.id] = 0;
                        updates[job.id] = {
                            ...job,
                            status: s.status,
                            progress: s.progress,
                            message: s.message,
                            logs: s.logs,
                            result: s.result,
                            pipeline_stage: s.pipeline_stage,
                            pipeline_mode: s.pipeline_mode,
                            cancelled: s.cancelled,
                            scene_status: s.scene_status,
                            sub_jobs: s.sub_jobs
                        };
                    } catch (err) {
                        console.error(`Failed to poll job ${job.id}`, err);
                        const n = (pollErrorsRef.current[job.id] || 0) + 1;
                        pollErrorsRef.current[job.id] = n;
                        if (n >= MAX_POLL_ERRORS) {
                            updates[job.id] = { ...job, status: 'failed', message: 'Failed to connect to server' };
                        } else {
                            // Transient — keep the job alive and keep polling.
                            updates[job.id] = { ...job, message: `Reconnecting to server (${n}/${MAX_POLL_ERRORS})...` };
                        }
                    }
                }));

                setActiveJobs(prev => {
                    const next = { ...prev };
                    for (const [id, updatedJob] of Object.entries(updates)) {
                        if (next[id]) {
                            next[id] = updatedJob;
                        }
                    }
                    return next;
                });
            } finally {
                isPollingRef.current = false;
            }
        };

        const interval = setInterval(pollJobs, 1000);
        return () => clearInterval(interval);
    }, [isPolling]);

    const removeJob = (jobId) => {
        delete pollErrorsRef.current[jobId];  // don't leak the error counter
        setActiveJobs(prev => {
            const next = { ...prev };
            delete next[jobId];
            return next;
        });
    };

    return (
        <JobContext.Provider value={{ activeJobs, addJob, removeJob, cancelJob }}>
            {children}
        </JobContext.Provider>
    );
};
