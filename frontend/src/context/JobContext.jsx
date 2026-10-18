import React, { createContext, useContext, useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { api } from '../api';
import { useAdaptivePolling } from '../hooks/useAdaptivePolling';

const JobContext = createContext(null);

export const useJobs = () => {
    const context = useContext(JobContext);
    if (!context) {
        throw new Error('useJobs must be used within a JobProvider');
    }
    return context;
};

const STORAGE_KEY = 'omnisub_active_jobs';
const TERMINAL_STATUSES = ['completed', 'failed', 'cancelled'];
const TERMINAL_RETENTION_MS = 10 * 60 * 1000; // 10 minutes

/**
 * Synchronously writes active jobs to localStorage, truncating logs to avoid quota blowup.
 */
const persistJobsSync = (jobs) => {
    try {
        const sanitized = {};
        for (const [id, j] of Object.entries(jobs || {})) {
            if (!j) continue;
            sanitized[id] = {
                ...j,
                logs: Array.isArray(j.logs) ? j.logs.slice(-50) : []
            };
        }
        localStorage.setItem(STORAGE_KEY, JSON.stringify(sanitized));
    } catch (err) {
        console.warn('Failed to persist active jobs to localStorage', err);
    }
};

/**
 * Loads persisted jobs from localStorage, retaining terminal jobs within 10-minute TTL.
 */
const loadPersistedJobs = () => {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return {};
        const parsed = JSON.parse(raw);
        const now = Date.now();
        const live = {};

        for (const [id, job] of Object.entries(parsed)) {
            if (!job || typeof job !== 'object') continue;

            if (TERMINAL_STATUSES.includes(job.status)) {
                let finishedAt = job.finished_at;
                if (!finishedAt) {
                    finishedAt = now;
                } else if (finishedAt < 1e11) {
                    finishedAt = finishedAt * 1000;
                }

                if (now - finishedAt < TERMINAL_RETENTION_MS) {
                    live[id] = { ...job, finished_at: finishedAt };
                }
            } else {
                live[id] = job;
            }
        }
        return live;
    } catch {
        return {};
    }
};

const hasActiveJobsToPoll = (jobs) =>
    Object.values(jobs || {}).some(job => job && !TERMINAL_STATUSES.includes(job.status));

const hasJobChanged = (prevJob, nextJob) => {
    if (!prevJob || !nextJob) return true;
    if (prevJob.status !== nextJob.status) return true;
    if (prevJob.parentId !== nextJob.parentId) return true;
    if (prevJob.progress !== nextJob.progress) return true;
    if (prevJob.message !== nextJob.message) return true;
    if (prevJob.pipeline_stage !== nextJob.pipeline_stage) return true;
    if (prevJob.pipeline_mode !== nextJob.pipeline_mode) return true;
    if (prevJob.logs?.length !== nextJob.logs?.length) return true;
    if (prevJob.eta_seconds !== nextJob.eta_seconds) return true;
    if (prevJob.cues_translated !== nextJob.cues_translated) return true;
    if (prevJob.total_cues !== nextJob.total_cues) return true;
    if (prevJob.cues_total !== nextJob.cues_total) return true;
    if (prevJob.current_scene !== nextJob.current_scene) return true;
    if (prevJob.total_scenes !== nextJob.total_scenes) return true;
    if (prevJob.scenes_done !== nextJob.scenes_done) return true;
    if (prevJob.scenes_total !== nextJob.scenes_total) return true;
    if (prevJob.daily_limit_reached !== nextJob.daily_limit_reached) return true;
    if (prevJob.rate_limit_hits !== nextJob.rate_limit_hits) return true;
    if (prevJob.completed_episodes?.length !== nextJob.completed_episodes?.length) return true;
    if (prevJob.failed_episodes?.length !== nextJob.failed_episodes?.length) return true;
    if (prevJob.project_name !== nextJob.project_name) return true;
    if (prevJob.episode_name !== nextJob.episode_name) return true;

    // Sub-jobs array equality
    const pSub = prevJob.sub_jobs || [];
    const nSub = nextJob.sub_jobs || [];
    if (pSub.length !== nSub.length) return true;
    for (let i = 0; i < nSub.length; i++) {
        if (String(pSub[i]) !== String(nSub[i])) return true;
    }

    const pScenes = prevJob.scene_status || {};
    const nScenes = nextJob.scene_status || {};
    const pKeys = Object.keys(pScenes);
    const nKeys = Object.keys(nScenes);
    if (pKeys.length !== nKeys.length) return true;
    for (const key of nKeys) {
        if (pScenes[key] !== nScenes[key]) return true;
    }

    return false;
};

export const JobProvider = ({ children }) => {
    const [activeJobs, setActiveJobs] = useState(loadPersistedJobs);
    const [isPolling, setIsPolling] = useState(() => hasActiveJobsToPoll(loadPersistedJobs()));

    const activeJobsRef = useRef(activeJobs);
    const pollErrorsRef = useRef({});
    const wsConnectedRef = useRef(false);

    const BASE_INTERVAL = 1500;
    const MAX_BACKOFF_INTERVAL = 5000;
    const BACKGROUND_INTERVAL = 10000;
    const MAX_POLL_ERRORS = 25;

    // Debounce writes only for ongoing background poll updates
    useEffect(() => {
        activeJobsRef.current = activeJobs;
        const timer = setTimeout(() => {
            persistJobsSync(activeJobs);
        }, 500);
        return () => clearTimeout(timer);
    }, [activeJobs]);

    // Synchronous beforeunload and pagehide flush
    useEffect(() => {
        const handleFlush = () => {
            if (activeJobsRef.current) {
                persistJobsSync(activeJobsRef.current);
            }
        };

        window.addEventListener('beforeunload', handleFlush);
        window.addEventListener('pagehide', handleFlush);
        return () => {
            window.removeEventListener('beforeunload', handleFlush);
            window.removeEventListener('pagehide', handleFlush);
        };
    }, []);

    // Periodic 60s cleanup of expired terminal jobs
    useEffect(() => {
        const cleanupInterval = setInterval(() => {
            const now = Date.now();
            setActiveJobs(prev => {
                let changed = false;
                const next = { ...prev };
                for (const [id, job] of Object.entries(next)) {
                    if (job && TERMINAL_STATUSES.includes(job.status) && job.finished_at) {
                        if (now - job.finished_at >= TERMINAL_RETENTION_MS) {
                            delete next[id];
                            changed = true;
                        }
                    }
                }
                if (changed) {
                    activeJobsRef.current = next;
                    persistJobsSync(next);
                    return next;
                }
                return prev;
            });
        }, 60000);
        return () => clearInterval(cleanupInterval);
    }, []);

    const removeJob = useCallback((jobId) => {
        delete pollErrorsRef.current[jobId];
        setActiveJobs(prev => {
            if (!prev[jobId]) return prev;
            const next = { ...prev };
            delete next[jobId];
            for (const [id, j] of Object.entries(next)) {
                if (j && j.parentId === jobId) {
                    delete next[id];
                    delete pollErrorsRef.current[id];
                }
            }
            activeJobsRef.current = next;
            persistJobsSync(next);
            return next;
        });
    }, []);

    const cancelJob = useCallback(async (jobId) => {
        try {
            await api.cancelJob(jobId);
            setActiveJobs(prev => {
                if (!prev[jobId]) return prev;
                const now = Date.now();
                const next = {
                    ...prev,
                    [jobId]: {
                        ...prev[jobId],
                        status: 'cancelled',
                        message: 'Cancelled by user',
                        finished_at: now
                    }
                };
                for (const [id, j] of Object.entries(next)) {
                    if (j && j.parentId === jobId && !TERMINAL_STATUSES.includes(j.status)) {
                        next[id] = {
                            ...j,
                            status: 'cancelled',
                            message: 'Cancelled by parent job',
                            finished_at: now
                        };
                    }
                }
                activeJobsRef.current = next;
                persistJobsSync(next);
                return next;
            });
        } catch (err) {
            console.error(`Failed to cancel job ${jobId}`, err);
        }
    }, []);

    const addJob = useCallback((jobId, type, description, metadata = {}) => {
        setActiveJobs(prev => {
            const subJobIds = metadata?.sub_job_ids || metadata?.sub_jobs || [];
            const next = {
                ...prev,
                [jobId]: {
                    id: jobId,
                    type,
                    description,
                    metadata,
                    parentId: metadata?.parentId || null,
                    sub_jobs: subJobIds.length > 0 ? subJobIds.map(String) : (prev[jobId]?.sub_jobs || null),
                    status: 'pending',
                    progress: 0,
                    message: 'Initializing...',
                    logs: [],
                    pipeline_stage: null,
                    pipeline_mode: null,
                    project_name: metadata?.projectId || null,
                    episode_name: metadata?.episodeName || null,
                }
            };

            // If parent job provided initial sub-jobs, pre-seed children immediately with parentId
            if (subJobIds.length > 0 && ['batch_translate', 'auto_translate', 'simple_pipeline', 'pipeline'].includes(type)) {
                subJobIds.forEach((childId, idx) => {
                    const childIdStr = String(childId);
                    const childKey = childIdStr.startsWith('bg_') ? childIdStr : `bg_${childIdStr}`;
                    const epName = metadata.episodeNames?.[idx] || null;
                    const existing = next[childKey] || prev[childKey];
                    next[childKey] = {
                        id: childKey,
                        type: 'translate_episode',
                        description: `Worker: ${epName || metadata.projectId || `Queue #${childIdStr}`}`,
                        metadata: {
                            projectId: metadata.projectId,
                            episodeName: epName,
                            parentId: jobId,
                            source: 'queue_worker'
                        },
                        parentId: jobId,
                        status: existing?.status || 'pending',
                        progress: existing?.progress || 0,
                        message: existing?.message || 'Queued in translation worker...',
                        logs: existing?.logs || [],
                        pipeline_stage: null,
                        pipeline_mode: null,
                        project_name: metadata.projectId || null,
                        episode_name: epName,
                    };
                });
            }

            activeJobsRef.current = next;
            persistJobsSync(next);
            return next;
        });
        setIsPolling(true);
    }, []);

    // Background Worker Job Discovery via /api/queue
    useEffect(() => {
        const discoverBackgroundJobs = async () => {
            if (document.hidden) return;
            try {
                const res = await api.getQueue();
                const items = res.data?.items || [];
                const currentJobs = activeJobsRef.current || {};

                const runningItems = items.filter(it => it.status === 'running');

                // Episodes claimed by external AI agents have no server-side job to poll:
                // mirror their work-file progress from the queue, and close the job once
                // the item is submitted or handed back.
                const agentItems = new Map(runningItems.filter(it => it.agent).map(it => [`bg_${it.id}`, it]));
                setActiveJobs(prev => {
                    let changed = false;
                    const next = { ...prev };
                    for (const [jobId, item] of agentItems) {
                        const cur = prev[jobId];
                        const message = `AI agent translating: ${item.agent.units_done ?? 0}/${item.agent.units ?? '?'} lines`;
                        if (cur && cur.status === 'running' && cur.message === message) continue;
                        const link = `/project/${encodeURIComponent(item.project_name)}/episode/${encodeURIComponent(item.episode_name)}`;
                        next[jobId] = {
                            ...(cur || {}),
                            id: jobId,
                            type: 'translate_episode',
                            description: `Agent: ${item.episode_name || item.project_name}`,
                            metadata: { projectId: item.project_name, episodeName: item.episode_name, link, source: 'agent' },
                            parentId: cur?.parentId || null,
                            logs: cur?.logs || [],
                            project_name: item.project_name,
                            episode_name: item.episode_name,
                            status: 'running',
                            progress: item.progress || 0,
                            message,
                        };
                        changed = true;
                    }
                    for (const [jobId, job] of Object.entries(prev)) {
                        if (job?.metadata?.source !== 'agent' || TERMINAL_STATUSES.includes(job.status) || agentItems.has(jobId)) continue;
                        const item = items.find(it => `bg_${it.id}` === jobId);
                        const done = item?.status === 'completed';
                        next[jobId] = {
                            ...job,
                            status: done ? 'completed' : 'cancelled',
                            progress: done ? 100 : job.progress,
                            message: done ? 'Translation completed.' : 'Returned to the queue.',
                            finished_at: Date.now(),
                        };
                        changed = true;
                    }
                    if (!changed) return prev;
                    activeJobsRef.current = next;
                    persistJobsSync(next);
                    return next;
                });

                for (const item of runningItems) {
                    if (item.agent) continue;
                    const bgJobId = `bg_${item.id}`;
                    const itemIdStr = String(item.id);
                    const projectName = item.project_name || null;
                    const episodeName = item.episode_name || null;

                    // Resolve parent job ID across 3 tiers
                    let parentJobId = null;

                    // Tier 1: Check known sub_jobs across all active jobs
                    for (const [pId, pJob] of Object.entries(currentJobs)) {
                        if (!pJob) continue;
                        const subList = pJob.sub_jobs || pJob.metadata?.sub_job_ids || pJob.metadata?.child_jobs;
                        if (Array.isArray(subList)) {
                            if (subList.some(s => String(s) === itemIdStr || String(s) === bgJobId)) {
                                parentJobId = pId;
                                break;
                            }
                        }
                    }

                    // Tier 2: Check item.options for parent_job_id
                    if (!parentJobId && item.options) {
                        try {
                            const opts = typeof item.options === 'string' ? JSON.parse(item.options) : item.options;
                            if (opts.parent_job_id && currentJobs[opts.parent_job_id]) {
                                parentJobId = opts.parent_job_id;
                            }
                        } catch {
                            // ignore JSON parse error
                        }
                    }

                    // Tier 3: Correlate with in-flight parent jobs for same project
                    if (!parentJobId && projectName) {
                        for (const [pId, pJob] of Object.entries(currentJobs)) {
                            if (!pJob || TERMINAL_STATUSES.includes(pJob.status) || pJob.parentId) continue;
                            if (['batch_translate', 'auto_translate', 'simple_pipeline', 'pipeline'].includes(pJob.type)) {
                                const pProj = pJob.project_name || pJob.metadata?.projectId;
                                if (pProj && pProj.toLowerCase() === projectName.toLowerCase()) {
                                    const epList = pJob.metadata?.episodeNames;
                                    if (Array.isArray(epList) && episodeName) {
                                        if (epList.some(e => e.toLowerCase() === episodeName.toLowerCase())) {
                                            parentJobId = pId;
                                            break;
                                        }
                                    } else {
                                        parentJobId = pId;
                                        break;
                                    }
                                }
                            }
                        }
                    }

                    // If already tracked
                    const existing = currentJobs[bgJobId];
                    if (existing) {
                        if (parentJobId && existing.parentId !== parentJobId) {
                            existing.parentId = parentJobId;
                            setActiveJobs(prev => {
                                if (prev[bgJobId] && prev[bgJobId].parentId !== parentJobId) {
                                    return {
                                        ...prev,
                                        [bgJobId]: { ...prev[bgJobId], parentId: parentJobId }
                                    };
                                }
                                return prev;
                            });
                        }
                        continue;
                    }

                    const link = projectName && episodeName
                        ? `/project/${encodeURIComponent(projectName)}/episode/${encodeURIComponent(episodeName)}`
                        : (projectName ? `/project/${encodeURIComponent(projectName)}` : '/queue');

                    addJob(
                        bgJobId,
                        'translate_episode',
                        `Worker: ${episodeName || projectName || `Queue #${item.id}`}`,
                        {
                            projectId: projectName,
                            episodeName: episodeName,
                            link,
                            source: 'queue_worker',
                            parentId: parentJobId
                        }
                    );

                    // If parent job found, ensure parent's sub_jobs includes this ID
                    if (parentJobId && currentJobs[parentJobId]) {
                        const parent = currentJobs[parentJobId];
                        const existingSubs = Array.isArray(parent.sub_jobs) ? parent.sub_jobs : [];
                        if (!existingSubs.includes(itemIdStr)) {
                            setActiveJobs(prev => {
                                if (prev[parentJobId]) {
                                    const prevSubs = Array.isArray(prev[parentJobId].sub_jobs) ? prev[parentJobId].sub_jobs : [];
                                    if (!prevSubs.includes(itemIdStr)) {
                                        return {
                                            ...prev,
                                            [parentJobId]: {
                                                ...prev[parentJobId],
                                                sub_jobs: [...prevSubs, itemIdStr]
                                            }
                                        };
                                    }
                                }
                                return prev;
                            });
                        }
                    }
                }
            } catch (err) {
                console.warn('Background queue discovery error:', err);
            }
        };

        const initialTimer = setTimeout(discoverBackgroundJobs, 1000);
        const interval = setInterval(discoverBackgroundJobs, 10000);

        return () => {
            clearTimeout(initialTimer);
            clearInterval(interval);
        };
    }, [addJob]);

    // Real-time WebSocket connection to backend
    useEffect(() => {
        let ws = null;
        let reconnectTimeout = null;
        let pollInterval = null;
        let lastEventId = null;
        let isUnmounted = false;

        const connectWs = () => {
            if (isUnmounted) return;
            try {
                const apiBase = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://localhost:8000' : '');
                const base = apiBase || window.location.origin;
                const proto = base.startsWith('https') ? 'wss:' : 'ws:';
                const host = base.replace(/^https?:\/\//, '');
                // Behind a reverse-proxy path the socket lives under it too (window.__OMBISUB_BASE__).
                const wsUrl = `${proto}//${host}${apiBase ? '' : (window.__OMBISUB_BASE__ || '')}/api/ws`;

                ws = new WebSocket(wsUrl);

                ws.onopen = () => {
                    wsConnectedRef.current = true;
                    ws._pingInterval = setInterval(() => {
                        if (ws && ws.readyState === WebSocket.OPEN) {
                            ws.send('ping');
                        }
                    }, 30000);
                };

                ws.onmessage = (event) => {
                    if (event.data === 'pong') return;
                    try {
                        const payload = JSON.parse(event.data);
                        handleIncomingEvent(payload);
                    } catch (e) {
                        console.debug('Failed to parse WS event:', e);
                    }
                };

                ws.onclose = () => {
                    wsConnectedRef.current = false;
                    if (ws && ws._pingInterval) clearInterval(ws._pingInterval);
                    if (!isUnmounted) {
                        reconnectTimeout = setTimeout(connectWs, 3000);
                    }
                };

                ws.onerror = () => {
                    wsConnectedRef.current = false;
                    if (ws) ws.close();
                };
            } catch (err) {
                wsConnectedRef.current = false;
                if (!isUnmounted) {
                    reconnectTimeout = setTimeout(connectWs, 5000);
                }
            }
        };

        const handleIncomingEvent = (payload) => {
            if (!payload || !payload.type) return;
            if (payload.id) {
                lastEventId = Math.max(lastEventId ?? 0, payload.id);
            }

            window.dispatchEvent(new CustomEvent('omnisub:event', { detail: payload }));

            if (payload.type === 'agent_submission' || payload.type === 'task_completed') {
                const epData = payload.data || payload;
                const queueId = epData.queue_id;
                const bgJobId = queueId ? `bg_${queueId}` : null;
                const now = Date.now();

                setActiveJobs(prev => {
                    let updated = false;
                    const next = { ...prev };
                    if (bgJobId && next[bgJobId]) {
                        next[bgJobId] = {
                            ...next[bgJobId],
                            status: 'completed',
                            progress: 100.0,
                            message: 'Translation completed.',
                            finished_at: now,
                        };
                        updated = true;
                    }
                    for (const [id, j] of Object.entries(next)) {
                        if (!j) continue;
                        const pMatch = j.project_name === epData.project_name || j.metadata?.projectId === epData.project_name;
                        const eMatch = j.episode_name === epData.episode_name || j.metadata?.episodeName === epData.episode_name;
                        if (pMatch && eMatch && !TERMINAL_STATUSES.includes(j.status)) {
                            next[id] = {
                                ...next[id],
                                status: 'completed',
                                progress: 100.0,
                                message: 'Translation completed.',
                                finished_at: now,
                            };
                            updated = true;
                        }
                    }
                    if (updated) {
                        activeJobsRef.current = next;
                        persistJobsSync(next);
                        return next;
                    }
                    return prev;
                });

                setIsPolling(true);
                window.dispatchEvent(new CustomEvent('omnisub:agent-submitted', { detail: epData }));
            }

            if (payload.type === 'queue_updated') {
                setIsPolling(true);
                window.dispatchEvent(new CustomEvent('omnisub:queue-updated', { detail: payload.data || payload }));
            }

            if (payload.type === 'sync_completed') {
                setIsPolling(true);
                window.dispatchEvent(new CustomEvent('omnisub:sync-completed', { detail: payload.data || payload }));
            }
        };

        const pollEventFallback = async () => {
            if (document.hidden) return;
            // Skip HTTP polling when WebSocket is connected and pushing events
            if (wsConnectedRef.current || (ws && ws.readyState === WebSocket.OPEN)) return;
            try {
                const res = await api.getEvents(lastEventId ?? 0);
                const latestId = res.data?.latest_id ?? 0;
                const events = res.data?.events || [];

                if (lastEventId === null) {
                    // First calibration run: record latest ID without replaying historical events
                    lastEventId = latestId || (events.length > 0 ? events[events.length - 1].id : 0);
                    return;
                }

                for (const ev of events) {
                    handleIncomingEvent(ev);
                }
                if (latestId > lastEventId) {
                    lastEventId = latestId;
                }
            } catch {
                // ignore polling errors
            }
        };

        connectWs();
        pollInterval = setInterval(pollEventFallback, 6000);

        return () => {
            isUnmounted = true;
            wsConnectedRef.current = false;
            if (reconnectTimeout) clearTimeout(reconnectTimeout);
            if (pollInterval) clearInterval(pollInterval);
            if (ws) {
                if (ws._pingInterval) clearInterval(ws._pingInterval);
                ws.close();
            }
        };
    }, []);


    const pollJobs = useCallback(async () => {
        const currentJobs = activeJobsRef.current;
        const jobsToPoll = Object.values(currentJobs).filter(job => {
            if (!job || TERMINAL_STATUSES.includes(job.status)) return false;
            if (job.metadata?.source === 'agent') return false;  // fed from /api/queue, no server job
            const isChild = Boolean(job.parentId || job.id.startsWith('bg_'));
            if (isChild) {
                // Only poll children if they are actively running or on first tick without status
                return job.status === 'running' || !job.status;
            }
            return true;
        });

        if (jobsToPoll.length === 0) {
            setIsPolling(false);
            return '';
        }

        const updates = {};
        let currentProgressSig = '';

        await Promise.all(jobsToPoll.map(async (job) => {
            try {
                const res = await api.getJob(job.id);
                const s = res.data;
                pollErrorsRef.current[job.id] = 0;

                const isTerminal = TERMINAL_STATUSES.includes(s.status);
                const existingFinishedAt = job.finished_at;
                const serverFinishedAt = s.finished_at ? (s.finished_at < 1e11 ? s.finished_at * 1000 : s.finished_at) : null;
                const finishedAt = isTerminal ? (existingFinishedAt || serverFinishedAt || Date.now()) : null;

                updates[job.id] = {
                    ...job,
                    project_name: s.project_name || job.project_name || job.metadata?.projectId || null,
                    episode_name: s.episode_name || job.episode_name || job.metadata?.episodeName || null,
                    status: s.status,
                    progress: typeof s.progress === 'number' ? s.progress : job.progress,
                    message: s.message || job.message,
                    logs: s.logs || job.logs || [],
                    result: s.result !== undefined ? s.result : job.result,
                    pipeline_stage: s.pipeline_stage ?? job.pipeline_stage,
                    pipeline_mode: s.pipeline_mode ?? job.pipeline_mode,
                    cancelled: Boolean(s.cancelled || job.cancelled),
                    sub_jobs: s.sub_jobs || job.sub_jobs || null,
                    scene_status: s.scene_status || job.scene_status || {},
                    current_scene: s.current_scene ?? s.scenes_done ?? job.current_scene ?? null,
                    total_scenes: s.total_scenes ?? s.scenes_total ?? job.total_scenes ?? null,
                    scenes_done: s.scenes_done ?? s.current_scene ?? job.scenes_done ?? null,
                    scenes_total: s.scenes_total ?? s.total_scenes ?? job.scenes_total ?? null,
                    cues_translated: s.cues_translated ?? job.cues_translated ?? null,
                    total_cues: s.total_cues ?? s.cues_total ?? job.total_cues ?? null,
                    cues_total: s.cues_total ?? s.total_cues ?? job.cues_total ?? null,
                    eta_seconds: s.eta_seconds ?? job.eta_seconds ?? null,
                    completed_episodes: s.completed_episodes || job.completed_episodes || [],
                    failed_episodes: s.failed_episodes || job.failed_episodes || [],
                    daily_limit_reached: Boolean(s.daily_limit_reached ?? job.daily_limit_reached),
                    parentId: s.parent_id || job.parentId || null,
                    rate_limit_hits: s.rate_limit_hits ?? job.rate_limit_hits ?? 0,
                    finished_at: finishedAt,
                };

                // Self-healing reconciliation: when parent jobs sync sub_jobs, reconcile active child jobs
                if (Array.isArray(s.sub_jobs) && s.sub_jobs.length > 0) {
                    s.sub_jobs.forEach(subId => {
                        const childIdStr = String(subId);
                        const childKey = childIdStr.startsWith('bg_') ? childIdStr : `bg_${childIdStr}`;
                        const existingChild = updates[childKey] || currentJobs[childKey];
                        if (existingChild && existingChild.parentId !== job.id) {
                            updates[childKey] = {
                                ...existingChild,
                                parentId: job.id
                            };
                        }
                    });
                }

                currentProgressSig += `${job.id}:${s.status}:${s.progress}:${s.pipeline_stage}:${s.scenes_done ?? s.current_scene ?? ''}:${s.cues_translated ?? ''}:${s.eta_seconds ?? ''};`;
            } catch (err) {
                console.error(`Failed to poll job ${job.id}`, err);
                const n = (pollErrorsRef.current[job.id] || 0) + 1;
                pollErrorsRef.current[job.id] = n;
                if (n >= MAX_POLL_ERRORS) {
                    updates[job.id] = { ...job, status: 'failed', message: 'Failed to connect to server', finished_at: Date.now() };
                } else {
                    updates[job.id] = { ...job, message: `Reconnecting to server (${n}/${MAX_POLL_ERRORS})...` };
                }
                currentProgressSig += `${job.id}:error:${n};`;
            }
        }));

        setActiveJobs(prev => {
            let hasChanges = false;
            const next = { ...prev };
            for (const [id, updatedJob] of Object.entries(updates)) {
                if (next[id]) {
                    if (hasJobChanged(next[id], updatedJob)) {
                        next[id] = updatedJob;
                        hasChanges = true;
                    }
                }
            }
            return hasChanges ? next : prev;
        });

        return currentProgressSig;
    }, []);

    useAdaptivePolling({
        enabled: isPolling,
        pollFn: pollJobs,
        baseInterval: BASE_INTERVAL,
        maxInterval: MAX_BACKOFF_INTERVAL,
        backgroundInterval: BACKGROUND_INTERVAL,
    });

    const contextValue = useMemo(() => ({
        activeJobs,
        addJob,
        removeJob,
        cancelJob
    }), [activeJobs, addJob, removeJob, cancelJob]);

    return (
        <JobContext.Provider value={contextValue}>
            {children}
        </JobContext.Provider>
    );
};
