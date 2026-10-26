import { createContext, useContext, useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { api } from '../api';
import { usePoll } from '../hooks/usePoll';

// Live work on the server, as one snapshot (docs/REVIEW_activity_runner_polling_2026-10-26.md, Step 2).
//
// `GET /api/activity` is polled every 5 s (15 s hidden) and refreshed at once on server events; it lists
// the in-memory jobs that are live and the queue rows that are running right now. Everything the panel
// shows derives from the last snapshot, so a card can never outlive the work it stands for:
//   - a snapshot job or queue row -> a card while it is listed, then a 10 s "just finished" grace;
//   - a job this browser started (`addJob`) -> "Starting..." until the snapshot lists it, and once it
//     is gone its final state is fetched once (`GET /api/jobs/{id}`) and kept for 10 minutes so pages
//     waiting for `status === 'completed'` still see it.
// localStorage keeps only the ids started here (`omnisub_started_jobs`), 10 minutes at most.

const JobContext = createContext(null);

export const useJobs = () => {
    const context = useContext(JobContext);
    if (!context) {
        throw new Error('useJobs must be used within a JobProvider');
    }
    return context;
};

export const STARTED_KEY = 'omnisub_started_jobs';
const STARTED_TTL_MS = 10 * 60 * 1000;   // ids started here live this long in localStorage
const FINAL_TTL_MS = 10 * 60 * 1000;     // a fetched final state stays this long in the panel
const STARTING_WINDOW_MS = 30 * 1000;    // "Starting..." card before we ask the server what happened
const GRACE_MS = 10 * 1000;              // a vanished snapshot entry stays this long with its last state
const HOUSEKEEPING_MS = 30 * 1000;
const FINAL_FETCH_ATTEMPTS = 3;          // a non-terminal answer is re-checked this many times
const TERMINAL_STATUSES = ['completed', 'failed', 'cancelled'];
const REFRESH_EVENTS = [
    'job_updated', 'queue_updated', 'agent_runner', 'agent_runner_submitted',
    'agent_submission', 'task_completed', 'sync_completed',
];

const loadStarted = () => {
    try {
        const raw = localStorage.getItem(STARTED_KEY);
        if (!raw) return {};
        const parsed = JSON.parse(raw);
        const now = Date.now();
        const live = {};
        for (const [id, entry] of Object.entries(parsed || {})) {
            if (!entry || typeof entry !== 'object') continue;
            if (typeof entry.started_at !== 'number' || now - entry.started_at >= STARTED_TTL_MS) continue;
            live[id] = { ...entry, id };
        }
        return live;
    } catch {
        return {};
    }
};

const saveStarted = (started) => {
    try {
        localStorage.setItem(STARTED_KEY, JSON.stringify(started || {}));
    } catch {
        // storage full or blocked: the panel still works for this page load
    }
};

const pruneStarted = (started, now = Date.now()) => {
    let changed = false;
    const next = {};
    for (const [id, entry] of Object.entries(started || {})) {
        if (entry && now - entry.started_at < STARTED_TTL_MS) next[id] = entry;
        else changed = true;
    }
    return changed ? next : started;
};

const linkFor = (project, episode) => {
    if (project && episode) return `/project/${encodeURIComponent(project)}/episode/${encodeURIComponent(episode)}`;
    if (project) return `/project/${encodeURIComponent(project)}`;
    return null;
};

const describeJob = (job) => {
    const type = String(job.type || 'job').replace(/_/g, ' ');
    if (job.episode && job.project) return `${type}: ${job.project} ${job.episode}`;
    if (job.project) return `${type}: ${job.project}`;
    return type;
};

// A snapshot job in the shape every consumer of activeJobs has always read.
const jobCard = (job, startedEntry) => {
    const meta = startedEntry?.metadata || {};
    const project = job.project ?? meta.projectId ?? null;
    const episode = job.episode ?? meta.episodeName ?? null;
    return {
        id: job.id,
        type: job.type || startedEntry?.type || 'job',
        description: startedEntry?.description || describeJob(job),
        status: job.status || 'running',
        progress: typeof job.progress === 'number' ? job.progress : 0,
        message: job.message || '',
        logs: Array.isArray(job.logs) ? job.logs : [],
        project_name: project,
        episode_name: episode,
        metadata: { ...meta, projectId: project, episodeName: episode, link: job.link || meta.link || linkFor(project, episode) },
        pipeline_stage: job.stage ?? null,
        eta_seconds: job.eta_seconds ?? null,
        parentId: job.parent_id || null,
        started_at: job.started_at ?? null,
        finished_at: null,
    };
};

// A running queue row (the worker, the extraction lane or an agent) as a card.
const queueCard = (row) => {
    const engine = row.engine === 'agent' ? 'agent' : 'queue_worker';
    const who = engine === 'agent' ? 'Agent' : (row.kind === 'extraction' ? 'Extracting' : 'Worker');
    const name = row.episode || row.project || `Queue #${row.id}`;
    const units = Number.isFinite(row.units_total) && row.units_total > 0
        ? `${row.units_done ?? 0}/${row.units_total} lines`
        : '';
    return {
        id: `bg_${row.id}`,
        type: 'translate_episode',
        description: `${who}: ${name}`,
        status: row.status === 'orphaned' ? 'orphaned' : 'running',
        progress: typeof row.progress === 'number' ? row.progress : 0,
        message: row.message || units || (row.status === 'orphaned' ? 'Marked running, but nothing is working on it.' : ''),
        logs: [],
        project_name: row.project || null,
        episode_name: row.episode || null,
        metadata: {
            projectId: row.project || null,
            episodeName: row.episode || null,
            link: row.link || linkFor(row.project, row.episode),
            source: engine,
            kind: row.kind || 'translation',
            units_done: row.units_done ?? null,
            units_total: row.units_total ?? null,
            lane: row.lane ?? null,
        },
        pipeline_stage: null,
        eta_seconds: null,
        parentId: null,
        finished_at: null,
    };
};

const startingCard = (entry) => ({
    id: entry.id,
    type: entry.type,
    description: entry.description,
    status: 'pending',
    progress: 0,
    message: 'Starting…',
    logs: [],
    project_name: entry.metadata?.projectId || null,
    episode_name: entry.metadata?.episodeName || null,
    metadata: entry.metadata || {},
    pipeline_stage: null,
    eta_seconds: null,
    parentId: entry.metadata?.parentId || null,
    finished_at: null,
});

const toMillis = (value) => {
    if (typeof value !== 'number') return null;
    return value < 1e11 ? value * 1000 : value;
};

export const JobProvider = ({ children }) => {
    const [snapshot, setSnapshot] = useState(null);
    const [connected, setConnected] = useState(false);
    const [started, setStarted] = useState(loadStarted);
    const [finals, setFinals] = useState({});   // id -> final card of a job started here (React state only)
    const [graced, setGraced] = useState({});   // id -> { card, until } for vanished snapshot entries
    const [hidden, setHidden] = useState({});   // ids dismissed from the panel while still listed
    const [clock, setClock] = useState(() => Date.now()); // advanced when a time-based expiry passes

    const startedRef = useRef(started);
    startedRef.current = started;
    const finalsRef = useRef(finals);
    finalsRef.current = finals;
    const lastCardsRef = useRef({});            // cards of the previous snapshot, to notice what vanished
    const lastSummaryRef = useRef(null);
    const fetchingRef = useRef(new Set());
    const attemptsRef = useRef({});
    const wsConnectedRef = useRef(false);

    const updateStarted = useCallback((updater) => {
        setStarted(prev => {
            const next = updater(prev);
            if (next !== prev) saveStarted(next);
            return next;
        });
    }, []);

    // ---- the final state of a job this browser started, fetched once it left the snapshot
    const fetchFinal = useCallback(async (id) => {
        if (fetchingRef.current.has(id)) return;
        if (TERMINAL_STATUSES.includes(finalsRef.current[id]?.status)) return; // already known
        fetchingRef.current.add(id);
        const entry = startedRef.current[id];
        try {
            const res = await api.getJob(id);
            const s = res.data || {};
            const attempts = (attemptsRef.current[id] || 0) + 1;
            attemptsRef.current[id] = attempts;
            const terminal = TERMINAL_STATUSES.includes(s.status);
            if (!terminal && attempts < FINAL_FETCH_ATTEMPTS) {
                // The snapshot no longer lists it but the server still calls it live: look again next tick.
                return;
            }
            const project = s.project_name || entry?.metadata?.projectId || null;
            const episode = s.episode_name || entry?.metadata?.episodeName || null;
            const card = {
                id,
                type: s.type || entry?.type || 'job',
                description: entry?.description || describeJob({ type: s.type, project, episode }),
                status: terminal ? s.status : 'cancelled',
                progress: typeof s.progress === 'number' ? s.progress : (s.status === 'completed' ? 100 : 0),
                message: terminal ? (s.message || '') : 'No longer on the server',
                logs: Array.isArray(s.logs) ? s.logs : [],
                result: s.result,
                completed_episodes: s.completed_episodes || [],
                failed_episodes: s.failed_episodes || [],
                project_name: project,
                episode_name: episode,
                metadata: { ...(entry?.metadata || {}), projectId: project, episodeName: episode, link: entry?.metadata?.link || linkFor(project, episode) },
                pipeline_stage: s.pipeline_stage ?? null,
                eta_seconds: null,
                parentId: s.parent_id || entry?.metadata?.parentId || null,
                finished_at: Date.now(),
                server_finished_at: toMillis(s.finished_at),
            };
            setFinals(prev => ({ ...prev, [id]: card }));
        } catch (err) {
            if (err?.response?.status === 404) {
                setFinals(prev => ({
                    ...prev,
                    [id]: {
                        id,
                        type: entry?.type || 'job',
                        description: entry?.description || id,
                        status: 'cancelled',
                        progress: 0,
                        message: 'No longer on the server',
                        logs: [],
                        project_name: entry?.metadata?.projectId || null,
                        episode_name: entry?.metadata?.episodeName || null,
                        metadata: entry?.metadata || {},
                        pipeline_stage: null,
                        eta_seconds: null,
                        parentId: null,
                        finished_at: Date.now(),
                    },
                }));
            }
            // any other error: the next snapshot tick tries again
        } finally {
            fetchingRef.current.delete(id);
        }
    }, []);

    // ---- the snapshot poll
    const fetchActivity = useCallback(async ({ signal } = {}) => {
        try {
            const res = await api.getActivity(signal);
            const data = res.data;
            if (!data || typeof data !== 'object') return;
            setSnapshot(data);
            setConnected(true);
        } catch (err) {
            if (!signal?.aborted) setConnected(false);
            throw err;
        }
    }, []);

    const { refresh } = usePoll(fetchActivity, {
        intervalMs: 5000,
        hiddenIntervalMs: 15000,
        refreshOn: REFRESH_EVENTS,
    });

    // Each new snapshot: notice what vanished (grace or final fetch) and what never showed up.
    useEffect(() => {
        if (!snapshot) return;
        const now = Date.now();
        const cards = {};
        for (const job of snapshot.jobs || []) cards[job.id] = jobCard(job, startedRef.current[job.id]);
        for (const row of snapshot.queue || []) cards[`bg_${row.id}`] = queueCard(row);

        const prev = lastCardsRef.current;
        const prevSummary = lastSummaryRef.current;
        const completedRose = !!(prevSummary && snapshot.summary
            && (snapshot.summary.completed || 0) > (prevSummary.completed || 0));
        const newlyGraced = {};
        for (const [id, card] of Object.entries(prev)) {
            if (cards[id]) continue;
            if (startedRef.current[id]) {
                fetchFinal(id);
                continue;
            }
            const done = (card.progress || 0) >= 99 || completedRose;
            newlyGraced[id] = {
                until: now + GRACE_MS,
                card: {
                    ...card,
                    status: done ? 'completed' : 'cancelled',
                    progress: done ? 100 : card.progress,
                    message: done ? 'Finished' : 'No longer running',
                    finished_at: now,
                },
            };
        }
        if (Object.keys(newlyGraced).length) setGraced(g => ({ ...g, ...newlyGraced }));

        // Started here, never listed, and past the starting window: ask the server what happened.
        for (const [id, entry] of Object.entries(startedRef.current)) {
            if (cards[id] || finalsRef.current[id]) continue;
            if (now - entry.started_at >= STARTING_WINDOW_MS) fetchFinal(id);
        }

        lastCardsRef.current = cards;
        lastSummaryRef.current = snapshot.summary || null;
    }, [snapshot, fetchFinal]);

    // Housekeeping: expire started ids (localStorage), finals, grace entries and hidden ids.
    useEffect(() => {
        const sweep = () => {
            const now = Date.now();
            updateStarted(prev => pruneStarted(prev, now));
            setFinals(prev => {
                const next = {};
                let changed = false;
                for (const [id, card] of Object.entries(prev)) {
                    if (now - card.finished_at < FINAL_TTL_MS) next[id] = card;
                    else changed = true;
                }
                return changed ? next : prev;
            });
            setGraced(prev => {
                const next = {};
                let changed = false;
                for (const [id, g] of Object.entries(prev)) {
                    if (g.until > now) next[id] = g;
                    else changed = true;
                }
                return changed ? next : prev;
            });
            setClock(now);
        };
        const interval = setInterval(sweep, HOUSEKEEPING_MS);
        return () => clearInterval(interval);
    }, [updateStarted]);

    // Grace entries leave on time, not at the next housekeeping sweep.
    useEffect(() => {
        const untils = Object.values(graced).map(g => g.until);
        if (!untils.length) return undefined;
        const delay = Math.max(0, Math.min(...untils) - Date.now()) + 50;
        const timer = setTimeout(() => {
            const now = Date.now();
            setGraced(prev => {
                const next = {};
                let changed = false;
                for (const [id, g] of Object.entries(prev)) {
                    if (g.until > now) next[id] = g;
                    else changed = true;
                }
                return changed ? next : prev;
            });
        }, delay);
        return () => clearTimeout(timer);
    }, [graced]);

    // A "Starting..." card turns into a final fetch at 30 s even when no snapshot changes meanwhile.
    useEffect(() => {
        const waiting = Object.values(started).filter(e => !finals[e.id] && !lastCardsRef.current[e.id]);
        if (!waiting.length) return undefined;
        const soonest = Math.min(...waiting.map(e => e.started_at + STARTING_WINDOW_MS));
        const timer = setTimeout(() => {
            const now = Date.now();
            for (const e of waiting) {
                if (now - e.started_at >= STARTING_WINDOW_MS && !lastCardsRef.current[e.id]) fetchFinal(e.id);
            }
            setClock(now);
        }, Math.max(0, soonest - Date.now()) + 50);
        return () => clearTimeout(timer);
    }, [started, finals, fetchFinal]);

    // ---- public actions
    const addJob = useCallback((jobId, type, description, metadata = {}) => {
        if (!jobId) return;
        const id = String(jobId);
        updateStarted(prev => ({ ...prev, [id]: { id, type, description, metadata: metadata || {}, started_at: Date.now() } }));
        setFinals(prev => {
            if (!prev[id]) return prev;
            const next = { ...prev };
            delete next[id];
            return next;
        });
        setHidden(prev => {
            if (!prev[id]) return prev;
            const next = { ...prev };
            delete next[id];
            return next;
        });
        delete attemptsRef.current[id];
        refresh();
    }, [updateStarted, refresh]);

    const removeJob = useCallback((jobId) => {
        const id = String(jobId);
        updateStarted(prev => {
            if (!prev[id]) return prev;
            const next = { ...prev };
            delete next[id];
            return next;
        });
        setFinals(prev => {
            if (!prev[id]) return prev;
            const next = { ...prev };
            delete next[id];
            return next;
        });
        setGraced(prev => {
            if (!prev[id]) return prev;
            const next = { ...prev };
            delete next[id];
            return next;
        });
        if (lastCardsRef.current[id]) setHidden(prev => ({ ...prev, [id]: true }));
    }, [updateStarted]);

    const clearAll = useCallback(() => {
        updateStarted(() => ({}));
        setFinals({});
        setGraced({});
        const live = {};
        for (const id of Object.keys(lastCardsRef.current)) live[id] = true;
        setHidden(live);
    }, [updateStarted]);

    const cancelJob = useCallback(async (jobId) => {
        try {
            await api.cancelJob(jobId);
        } catch (err) {
            console.error(`Failed to cancel job ${jobId}`, err);
        }
        refresh();
    }, [refresh]);

    const isLive = useCallback((project, episode) => {
        if (!snapshot || !project) return false;
        const p = String(project).toLowerCase();
        const e = episode == null ? null : String(episode).toLowerCase();
        const match = (jp, je) => String(jp || '').toLowerCase() === p && (e === null || String(je || '').toLowerCase() === e);
        return (snapshot.jobs || []).some(j => match(j.project, j.episode))
            || (snapshot.queue || []).some(q => match(q.project, q.episode));
    }, [snapshot]);

    // ---- WebSocket to the server, with the HTTP events poll as fallback. Every event is re-dispatched
    // on `window` as `omnisub:event` (usePoll subscribers) and the legacy per-type events stay.
    useEffect(() => {
        let ws = null;
        let reconnectTimeout = null;
        let pollInterval = null;
        let lastEventId = null;
        let isUnmounted = false;

        const handleIncomingEvent = (payload) => {
            if (!payload || !payload.type) return;
            if (payload.id) {
                lastEventId = Math.max(lastEventId ?? 0, payload.id);
            }

            window.dispatchEvent(new CustomEvent('omnisub:event', { detail: payload }));

            const data = payload.data || payload;
            if (payload.type === 'job_updated') {
                const id = data.id ? String(data.id) : null;
                if (id && startedRef.current[id] && TERMINAL_STATUSES.includes(data.status)) fetchFinal(id);
            }
            if (payload.type === 'agent_submission' || payload.type === 'task_completed') {
                window.dispatchEvent(new CustomEvent('omnisub:agent-submitted', { detail: data }));
            }
            if (payload.type === 'queue_updated') {
                window.dispatchEvent(new CustomEvent('omnisub:queue-updated', { detail: data }));
            }
            if (payload.type === 'sync_completed') {
                window.dispatchEvent(new CustomEvent('omnisub:sync-completed', { detail: data }));
            }
        };

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
            } catch {
                wsConnectedRef.current = false;
                if (!isUnmounted) {
                    reconnectTimeout = setTimeout(connectWs, 5000);
                }
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

        if (typeof WebSocket !== 'undefined') connectWs();
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
    }, [fetchFinal]);

    // ---- what the panel and the pages see: a dict keyed by id, as it always was
    const activeJobs = useMemo(() => {
        const now = Math.max(clock, Date.now());
        const out = {};
        for (const job of snapshot?.jobs || []) {
            if (!hidden[job.id]) out[job.id] = jobCard(job, started[job.id]);
        }
        for (const row of snapshot?.queue || []) {
            const id = `bg_${row.id}`;
            if (!hidden[id]) out[id] = queueCard(row);
        }
        for (const [id, g] of Object.entries(graced)) {
            if (!out[id] && !hidden[id] && g.until > now) out[id] = g.card;
        }
        for (const [id, card] of Object.entries(finals)) {
            if (!out[id] && now - card.finished_at < FINAL_TTL_MS) out[id] = card;
        }
        for (const [id, entry] of Object.entries(started)) {
            if (out[id] || finals[id]) continue;
            if (now - entry.started_at < STARTING_WINDOW_MS) out[id] = startingCard(entry);
        }
        return out;
    }, [snapshot, started, finals, graced, hidden, clock]);

    // Hidden ids are forgotten once the server stops listing them.
    useEffect(() => {
        if (!snapshot || !Object.keys(hidden).length) return;
        const listed = new Set([
            ...(snapshot.jobs || []).map(j => j.id),
            ...(snapshot.queue || []).map(q => `bg_${q.id}`),
            ...Object.keys(graced),
        ]);
        setHidden(prev => {
            const next = {};
            let changed = false;
            for (const id of Object.keys(prev)) {
                if (listed.has(id)) next[id] = true;
                else changed = true;
            }
            return changed ? next : prev;
        });
    }, [snapshot, graced, hidden]);

    const contextValue = useMemo(() => ({
        activeJobs,
        snapshot,
        connected,
        addJob,
        removeJob,
        clearAll,
        cancelJob,
        refresh,
        isLive,
    }), [activeJobs, snapshot, connected, addJob, removeJob, clearAll, cancelJob, refresh, isLive]);

    return (
        <JobContext.Provider value={contextValue}>
            {children}
        </JobContext.Provider>
    );
};

