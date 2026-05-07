import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { api } from '../api';

const JobContext = createContext();

export const useJobs = () => useContext(JobContext);

export const JobProvider = ({ children }) => {
    const [activeJobs, setActiveJobs] = useState({});
    const [isPolling, setIsPolling] = useState(false);
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
                const jobsToPoll = Object.values(activeJobs).filter(
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
                        updates[job.id] = { ...job, status: 'failed', message: 'Failed to connect to server' };
                    }
                }));

                setActiveJobs(prev => {
                    const next = { ...prev };
                    for (const [id, updatedJob] of Object.entries(updates)) {
                        next[id] = updatedJob;
                    }
                    return next;
                });
            } finally {
                isPollingRef.current = false;
            }
        };

        const interval = setInterval(pollJobs, 1000);
        return () => clearInterval(interval);
    }, [isPolling, activeJobs]);

    const removeJob = (jobId) => {
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
