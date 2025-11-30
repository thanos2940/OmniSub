import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { api } from '../api';

const JobContext = createContext();

export const useJobs = () => useContext(JobContext);

export const JobProvider = ({ children }) => {
    const [activeJobs, setActiveJobs] = useState({});
    const [isPolling, setIsPolling] = useState(false);

    // Use a ref to keep track of whether we are currently polling to avoid overlapping
    const isPollingRef = useRef(false);

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
                logs: []
            }
        }));
        setIsPolling(true);
    };

    useEffect(() => {
        if (!isPolling) return;

        const pollJobs = async () => {
            if (isPollingRef.current) return;
            isPollingRef.current = true;

            try {
                // We need to know which jobs to poll. 
                // We can't rely solely on closure 'activeJobs' if we want to be perfectly safe, 
                // but 'activeJobs' in the dependency array ensures we restart with fresh list.
                // However, to avoid the race condition where we overwrite new state:

                // 1. Snapshot the jobs we intend to poll
                const jobsToPoll = Object.values(activeJobs).filter(
                    job => job.status !== 'completed' && job.status !== 'failed'
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
                        const serverJob = res.data;
                        updates[job.id] = {
                            ...job,
                            status: serverJob.status,
                            progress: serverJob.progress,
                            message: serverJob.message,
                            logs: serverJob.logs,
                            result: serverJob.result
                        };

                        if (serverJob.status === 'completed') {
                            console.log(`Job ${job.id} completed`, serverJob.result);
                        }
                    } catch (err) {
                        console.error(`Failed to poll job ${job.id}`, err);
                        updates[job.id] = { ...job, status: 'failed', message: 'Failed to connect to server' };
                    }
                }));

                // 2. Merge updates into the *current* state
                setActiveJobs(prev => {
                    const next = { ...prev };
                    let hasActive = false;

                    // Apply updates
                    for (const [id, updatedJob] of Object.entries(updates)) {
                        next[id] = updatedJob;
                    }

                    // Check if we still have active jobs in the *new* state
                    for (const job of Object.values(next)) {
                        if (job.status === 'running' || job.status === 'pending') {
                            hasActive = true;
                        }
                    }

                    // If no active jobs, stop polling (but we do this in the effect cleanup/dependency cycle usually)
                    // But here we can set the flag.
                    if (!hasActive) {
                        // We can't set isPolling(false) here directly if we want to be clean, 
                        // but it's fine to trigger a re-render.
                        // Actually, let's let the next cycle decide or set it here.
                        // Better to set it here to stop the interval if we are done.
                    }

                    return next;
                });

                // Update polling status based on the *updates* we just processed? 
                // No, rely on the state update to trigger re-evaluation if needed, 
                // but we are in a setInterval.

                // If we determined no active jobs were found in the *snapshot*, we stopped.
                // But we should check the *merged* result.

            } finally {
                isPollingRef.current = false;
            }
        };

        const interval = setInterval(pollJobs, 1000);
        return () => clearInterval(interval);
    }, [isPolling, activeJobs]); // Re-run if activeJobs changes (new job added)

    const removeJob = (jobId) => {
        setActiveJobs(prev => {
            const next = { ...prev };
            delete next[jobId];
            return next;
        });
    };

    return (
        <JobContext.Provider value={{ activeJobs, addJob, removeJob }}>
            {children}
        </JobContext.Provider>
    );
};
