import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../../api';

// Live state of whatever translates: the agent runner (default) or, when `translation_engine` is "model",
// the Gemini worker. Polls on a timer and refreshes at once on `agent_runner` events (JobContext
// re-dispatches every server event as `omnisub:event`).

export const ENGINE_LABEL = { antigravity: 'agy', claude: 'Claude Code', gemini: 'Gemini CLI' };

const NO_LOG = Number.MAX_SAFE_INTEGER; // "give me log lines after this" -> none
const ENGINE_REFRESH_MS = 60000;

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

export default function useRunnerStatus({ intervalMs = 15000, withLog = false } = {}) {
    const [info, setInfo] = useState(null);
    const [engine, setEngine] = useState('agent');
    const [queue, setQueue] = useState(null);
    const [log, setLog] = useState([]);
    const [loading, setLoading] = useState(true);
    const seq = useRef(0);
    const alive = useRef(true);
    const engineRef = useRef('agent');
    const lastRefresh = useRef(0);

    const refresh = useCallback(async (quota = false) => {
        lastRefresh.current = Date.now();
        try {
            const res = await api.getAgentRunner(withLog ? seq.current : NO_LOG, quota);
            if (!alive.current) return;
            const data = res.data || {};
            setInfo(data);
            if (withLog) {
                const since = seq.current;
                if ((data.log_seq || 0) < since) { // the server restarted: reload the whole log next time
                    seq.current = 0;
                    setLog([]);
                } else {
                    const fresh = (data.log || []).filter(e => e.seq > since);
                    seq.current = Math.max(since, data.log_seq || 0);
                    if (fresh.length) setLog(prev => [...prev, ...fresh].slice(-400));
                }
            }
        } catch {
            // an unreachable server shows up elsewhere
        }
        if (engineRef.current === 'model') {
            try {
                const res = await api.getQueue();
                if (alive.current) setQueue(res.data || null);
            } catch {
                // see above
            }
        }
        if (alive.current) setLoading(false);
    }, [withLog]);

    useEffect(() => {
        alive.current = true;
        const loadEngine = async () => {
            try {
                const res = await api.getSettings();
                const value = res.data?.translation_engine === 'model' ? 'model' : 'agent';
                engineRef.current = value;
                if (alive.current) setEngine(value);
            } catch {
                // keep the last known engine
            }
        };
        loadEngine().then(() => refresh());
        const tick = setInterval(() => { if (!document.hidden) refresh(); }, intervalMs);
        const engineTick = setInterval(() => { if (!document.hidden) loadEngine(); }, ENGINE_REFRESH_MS);
        const onVisible = () => { if (!document.hidden) refresh(); };
        const onEvent = (e) => {
            const type = e.detail?.type || '';
            if (type.startsWith('agent_runner') && Date.now() - lastRefresh.current > 1000) refresh();
        };
        document.addEventListener('visibilitychange', onVisible);
        window.addEventListener('omnisub:event', onEvent);
        return () => {
            alive.current = false;
            clearInterval(tick);
            clearInterval(engineTick);
            document.removeEventListener('visibilitychange', onVisible);
            window.removeEventListener('omnisub:event', onEvent);
        };
    }, [refresh, intervalMs]);

    return { info, engine, queue, log, loading, refresh };
}
