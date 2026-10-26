import { useState, useRef, useCallback } from 'react';
import { api } from '../../api';
import { useJobs } from '../../context/JobContext';
import { usePoll } from '../../hooks/usePoll';

// Live state of whatever translates: the agent runner (default) or, when `translation_engine` is "model",
// the Gemini worker. Without `withLog` it is a view of the `/api/activity` snapshot JobContext already
// polls and makes no request of its own. With `withLog` (the Activity page) it polls
// `GET /api/agent/runner?since=` for the log lines and refreshes at once on runner events.

export const ENGINE_LABEL = { antigravity: 'agy', claude: 'Claude Code', gemini: 'Gemini CLI' };

const LOG_EVENTS = ['agent_runner', 'agent_runner_log', 'agent_runner_submitted'];

// One short description of the runner for the top-bar pill and the Activity panel.
// -> { label, tone: neutral | success | warning | danger | info | accent }
export function describeRunner(info, engine, queue) {
    if (engine === 'model') {
        const summary = queue?.summary || {};
        if (queue?.paused) return { label: 'worker · paused', tone: 'warning' };
        if ((summary.running || 0) > 0) return { label: `worker · translating ${summary.running}`, tone: 'success' };
        if ((summary.pending || 0) > 0) return { label: `worker · ${summary.pending} queued`, tone: 'info' };
        return { label: 'worker · idle', tone: 'neutral' };
    }
    if (!info) return { label: 'runner', tone: 'neutral' };
    const name = ENGINE_LABEL[info.config?.engine] || 'agy';
    const status = info.status || 'stopped';
    const active = (info.lanes || []).filter(l => l.task).length;
    if (info.paused) return { label: 'paused', tone: 'warning' };
    if (status === 'error') return { label: `${name} · error`, tone: 'danger' };
    if (!info.running || status === 'stopped' || status === 'done') return { label: 'stopped', tone: 'neutral' };
    if (status === 'waiting') return { label: `${name} · waiting`, tone: 'warning' };
    if (status === 'stopping') return { label: `${name} · stopping`, tone: 'warning' };
    if (active > 0) return { label: `${name} · translating ${active}`, tone: 'success' };
    if (status === 'running') return { label: `${name} · working`, tone: 'success' };
    return { label: `${name} · idle`, tone: 'info' };
}

export default function useRunnerStatus({ intervalMs = 3000, withLog = false } = {}) {
    const { snapshot, refresh: refreshSnapshot } = useJobs();
    const [own, setOwn] = useState(null);     // the runner as GET /api/agent/runner last reported it (withLog)
    const [log, setLog] = useState([]);
    const seq = useRef(0);
    const quotaRef = useRef(false);

    const fetchLog = useCallback(async ({ signal } = {}) => {
        const quota = quotaRef.current;
        quotaRef.current = false;
        const res = await api.getAgentRunner(seq.current, quota, signal);
        const data = res.data || {};
        setOwn(data);
        const since = seq.current;
        if ((data.log_seq || 0) < since) { // the server restarted: reload the whole log next time
            seq.current = 0;
            setLog([]);
        } else {
            const fresh = (data.log || []).filter(e => e.seq > since);
            seq.current = Math.max(since, data.log_seq || 0);
            if (fresh.length) setLog(prev => [...prev, ...fresh].slice(-400));
        }
    }, []);

    const { refresh: refreshLog } = usePoll(fetchLog, { intervalMs, enabled: withLog, refreshOn: LOG_EVENTS });

    // refresh(quota): with the log view, asks the runner (and agy's quota when `quota`); otherwise
    // refreshes the shared snapshot.
    const refresh = useCallback(async (quota = false) => {
        if (withLog) {
            if (quota) quotaRef.current = true;
            await refreshLog();
        }
        await refreshSnapshot();
    }, [withLog, refreshLog, refreshSnapshot]);

    const engine = snapshot?.translation_engine === 'model' ? 'model' : 'agent';
    const info = withLog ? (own || snapshot?.runner || null) : (snapshot?.runner || null);
    const queue = snapshot ? { summary: snapshot.summary || {}, paused: !!snapshot.worker?.paused } : null;
    const loading = !snapshot && !(withLog && own);

    return { info, engine, queue, log, loading, refresh };
}
