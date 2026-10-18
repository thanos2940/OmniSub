import { useState, useEffect, useRef, useCallback } from 'react';
import { Bot, Play, Pause, Square, RefreshCw, Settings, ChevronDown, ChevronUp, Loader2, Gauge, AlertTriangle, Activity, Clock, ListTodo } from 'lucide-react';
import { api } from '../../api';

// The direct agent runner: translates the queue through an agent CLI (Antigravity, Claude Code,
// Gemini CLI) with one prompt per episode, pausing on quota and resuming by itself.

const ENGINES = { antigravity: 'Antigravity', claude: 'Claude Code', gemini: 'Gemini CLI' };

const STATUS_STYLE = {
    running: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400',
    idle: 'bg-blue-500/10 border-blue-500/30 text-blue-600 dark:text-blue-400',
    waiting: 'bg-amber-500/10 border-amber-500/30 text-amber-600 dark:text-amber-400',
    stopping: 'bg-amber-500/10 border-amber-500/30 text-amber-600 dark:text-amber-400',
    error: 'bg-rose-500/10 border-rose-500/30 text-rose-600 dark:text-rose-400',
    done: 'bg-gray-500/10 border-gray-500/30 text-gray-600 dark:text-gray-400',
    stopped: 'bg-gray-500/10 border-gray-500/30 text-gray-600 dark:text-gray-400',
};

const LOG_STYLE = {
    ok: 'text-emerald-400',
    warn: 'text-amber-400',
    error: 'text-rose-400',
    info: 'text-gray-300',
};

const fmtTime = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });
};

// Minutes -> "~45 min" / "~2 h 10 min" / "~2 d 3 h"; null while there is no estimate.
const fmtMinutes = (minutes) => {
    if (minutes == null || !Number.isFinite(Number(minutes)) || Number(minutes) < 0) return null;
    const total = Math.round(Number(minutes));
    if (total < 1) return '< 1 min';
    if (total < 60) return `~${total} min`;
    const hours = Math.floor(total / 60);
    if (hours >= 48) {
        const days = Math.floor(hours / 24);
        return hours % 24 ? `~${days} d ${hours % 24} h` : `~${days} d`;
    }
    return total % 60 ? `~${hours} h ${total % 60} min` : `~${hours} h`;
};

// Lines per minute with one decimal below 10 ("2.5"), whole numbers above; null when there is no speed yet.
const fmtRate = (linesPerMin) => {
    const v = Number(linesPerMin);
    if (!Number.isFinite(v) || v <= 0) return null;
    return v >= 10 ? String(Math.round(v)) : v.toFixed(1);
};

const toForm = (cfg = {}) => ({
    engine: cfg.engine || 'antigravity',
    model: cfg.model || '',
    fallback_models: (cfg.fallback_models || []).join(', '),
    lanes: cfg.lanes ?? 3,
    per_show: cfg.per_show ?? 2,
    max_episodes: cfg.max_episodes ?? 0,
    watch: !!cfg.watch,
    api_rescue: cfg.api_rescue ?? true,
    rescue_model: cfg.rescue_model || '',
});

const AgentRunnerPanel = ({ toast }) => {
    const [info, setInfo] = useState(null);
    const [log, setLog] = useState([]);
    const [busy, setBusy] = useState(false);
    const [open, setOpen] = useState(false);
    const [form, setForm] = useState(null);
    const [checkingQuota, setCheckingQuota] = useState(false);
    const seq = useRef(0);
    const logBox = useRef(null);
    const alive = useRef(true);
    const prevStatus = useRef(null);
    const toastRef = useRef(toast);
    const lastToast = useRef({ text: '', at: 0 });

    useEffect(() => { toastRef.current = toast; }, [toast]);

    const notify = useCallback((kind, text) => {
        lastToast.current = { text, at: Date.now() };
        toastRef.current?.[kind]?.(text);
    }, []);

    // Toasts for status changes seen while polling. The first answer after mount is the starting point,
    // not a change, so it never toasts.
    const announce = useCallback((data) => {
        const status = data.status || 'stopped';
        const before = prevStatus.current;
        prevStatus.current = status;
        if (before === null || before === status) return;
        const msg = (data.message || '').trim();
        const send = (kind, text) => {
            // An action of ours (e.g. "Stop now") may have said the same thing a moment ago.
            if (lastToast.current.text === text && Date.now() - lastToast.current.at < 8000) return;
            notify(kind, text);
        };
        if (status === 'waiting') send('warning', msg ? `Runner paused: ${msg}` : 'Runner paused.');
        else if (status === 'done') send('success', msg ? `Runner finished: ${msg}` : 'Runner finished.');
        else if (status === 'error') send('error', msg ? `Runner error: ${msg}` : 'The runner stopped with an error.');
        else if (status === 'stopped' && before !== 'done' && before !== 'error') send('info', 'Runner stopped.');
    }, [notify]);

    const refresh = useCallback(async (quota = false) => {
        try {
            const res = await api.getAgentRunner(seq.current, quota);
            if (!alive.current) return;  // the panel was closed while the request was in flight
            const data = res.data || {};
            setInfo(data);
            announce(data);
            setForm(f => f || toForm(data.config));
            const since = seq.current;
            if ((data.log_seq || 0) < since) {  // the server restarted: reload the whole log next time
                seq.current = 0;
                setLog([]);
                return;
            }
            const fresh = (data.log || []).filter(e => e.seq > since);
            seq.current = Math.max(since, data.log_seq || 0);
            if (fresh.length) setLog(prev => [...prev, ...fresh].slice(-400));
        } catch {
            // the queue panel already reports an unreachable server
        }
    }, [announce]);

    useEffect(() => {
        alive.current = true;
        refresh();
        const t = setInterval(() => { if (!document.hidden) refresh(); }, 3000);
        return () => {
            alive.current = false;
            clearInterval(t);
        };
    }, [refresh]);

    useEffect(() => {
        const box = logBox.current;
        if (box && box.scrollHeight - box.scrollTop - box.clientHeight < 80) box.scrollTop = box.scrollHeight;
    }, [log]);

    const act = async (fn, okMsg) => {
        setBusy(true);
        try {
            await fn();
            if (okMsg) notify('success', okMsg);
            await refresh();
        } catch (err) {
            const msg = err.response?.data?.detail || err.response?.data?.message || err.message;
            if (toast?.error) toast.error(`Runner: ${msg}`);
        } finally {
            setBusy(false);
        }
    };

    const start = () => act(() => api.startAgentRunner({
        engine: form.engine,
        model: form.model.trim(),
        fallback_models: form.fallback_models,
        lanes: Number(form.lanes) || 1,
        per_show: Number(form.per_show) || 1,
        max_episodes: Number(form.max_episodes) || 0,
        watch: form.watch,
        api_rescue: form.api_rescue,
        rescue_model: form.rescue_model.trim(),
    }), info?.running ? 'Runner settings updated.' : 'Runner started.');

    const checkQuota = async () => {
        setCheckingQuota(true);
        await refresh(true);
        setCheckingQuota(false);
    };

    if (!info || !form) return null;

    const running = info.running;
    const status = info.status || 'stopped';
    const session = info.session || {};
    const pauses = Object.entries(info.pauses || {});
    const lanes = (info.lanes || []).filter(l => l.task);
    const engineMissing = info.engines && !info.engines[form.engine];
    // Queue stats: each one only shows when the server reports it.
    const pending = info.pending != null && Number.isFinite(Number(info.pending)) ? Number(info.pending) : null;
    const throughput = info.throughput || null;
    const rate = fmtRate(throughput?.lines_per_min);
    const eta = fmtMinutes(info.eta_minutes);
    const perHour = Number(throughput?.episodes_per_hour);
    const activeMin = Number(throughput?.active_minutes);
    const rateTitle = [
        perHour > 0 ? `${perHour.toFixed(1)} episodes/hour` : null,
        activeMin > 0 ? `measured over ${Math.round(activeMin)} active min` : null,
    ].filter(Boolean).join(' · ') || 'Translation speed of this session';
    const showStats = running || (pending ?? 0) > 0;
    const field = 'w-full px-2 py-1 text-xs rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100';

    return (
        <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm space-y-3">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
                <div className="flex items-start gap-3">
                    <Bot className="text-purple-500 mt-0.5 shrink-0" size={22} />
                    <div>
                        <div className="flex items-center gap-2 flex-wrap">
                            <h3 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">Agent runner</h3>
                            <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${STATUS_STYLE[status] || STATUS_STYLE.stopped}`}>
                                {status}
                            </span>
                        </div>
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                            {info.message || `Translates the queue through ${ENGINES[info.config?.engine] || 'an agent CLI'} — one prompt per episode, pauses on quota and carries on by itself.`}
                        </p>
                        {showStats && (
                            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-1.5 text-[11px] text-gray-500 dark:text-gray-400">
                                {pending != null && (
                                    <span className="flex items-center gap-1" title="Episodes waiting in the queue for the runner">
                                        <ListTodo size={12} className="text-blue-500" /> Pending:{' '}
                                        <strong className="text-gray-700 dark:text-gray-200">{pending}</strong>
                                    </span>
                                )}
                                {throughput && (
                                    <span className="flex items-center gap-1" title={rateTitle}>
                                        <Activity size={12} className="text-emerald-500" /> Speed:{' '}
                                        <strong className="text-gray-700 dark:text-gray-200">{rate ? `${rate} lines/min` : '—'}</strong>
                                    </span>
                                )}
                                {info.eta_minutes !== undefined && (
                                    <span className="flex items-center gap-1" title="Estimated time left for the queued episodes at the current speed">
                                        <Clock size={12} className="text-amber-500" /> ETA:{' '}
                                        <strong className="text-gray-700 dark:text-gray-200">{eta || '—'}</strong>
                                    </span>
                                )}
                            </div>
                        )}
                    </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    {!running || status === 'stopping' ? (
                        <button type="button" onClick={start} disabled={busy || engineMissing}
                            title={engineMissing ? `${ENGINES[form.engine]} is not installed` : 'Start translating the queue'}
                            className="flex items-center gap-1.5 text-sm font-semibold border px-3 py-1.5 rounded-lg shadow-sm bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/20 disabled:opacity-50">
                            {busy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} {status === 'stopping' ? 'Keep going' : 'Start'}
                        </button>
                    ) : (
                        <>
                            <button type="button" onClick={() => act(() => api.stopAgentRunner(false), 'The runner stops after the episodes in hand.')} disabled={busy}
                                title="Finish the episodes in hand, then stop"
                                className="flex items-center gap-1.5 text-sm font-semibold border px-3 py-1.5 rounded-lg shadow-sm bg-amber-500/10 border-amber-500/30 text-amber-600 dark:text-amber-400 hover:bg-amber-500/20 disabled:opacity-50">
                                <Pause size={14} /> Stop
                            </button>
                            <button type="button" onClick={() => act(() => api.stopAgentRunner(true), 'Runner stopped.')} disabled={busy}
                                title="Stop now (cuts off requests in flight; translated lines are kept and the episodes resume next time)"
                                className="flex items-center gap-1.5 text-sm font-semibold border px-3 py-1.5 rounded-lg shadow-sm bg-rose-500/10 border-rose-500/30 text-rose-600 dark:text-rose-400 hover:bg-rose-500/20 disabled:opacity-50">
                                <Square size={14} /> Stop now
                            </button>
                        </>
                    )}
                    {pauses.length > 0 && (
                        <button type="button" onClick={() => act(() => api.resumeAgentRunnerNow(), 'Pauses cleared.')} disabled={busy}
                            title="Clear quota pauses (e.g. after adding credits) and try again now"
                            className="flex items-center gap-1.5 text-sm font-semibold border px-3 py-1.5 rounded-lg shadow-sm bg-blue-500/10 border-blue-500/30 text-blue-600 dark:text-blue-400 hover:bg-blue-500/20 disabled:opacity-50">
                            <RefreshCw size={14} /> Resume now
                        </button>
                    )}
                    <button type="button" onClick={() => setOpen(o => !o)}
                        className="flex items-center gap-1.5 text-sm font-semibold border px-3 py-1.5 rounded-lg shadow-sm bg-gray-500/10 border-gray-500/30 text-gray-600 dark:text-gray-300 hover:bg-gray-500/20">
                        <Settings size={14} /> Settings {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </button>
                </div>
            </div>

            {open && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-3 rounded-xl border border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-900/30">
                    <label className="text-[11px] font-semibold text-gray-500 space-y-1">
                        <span>Engine</span>
                        <select className={field} value={form.engine} disabled={running}
                            onChange={e => setForm({ ...form, engine: e.target.value })}>
                            {Object.entries(ENGINES).map(([k, v]) => (
                                <option key={k} value={k}>{v}{info.engines && !info.engines[k] ? ' (not installed)' : ''}</option>
                            ))}
                        </select>
                    </label>
                    <label className="text-[11px] font-semibold text-gray-500 space-y-1">
                        <span>Model</span>
                        <input className={field} value={form.model} placeholder="the CLI's default"
                            onChange={e => setForm({ ...form, model: e.target.value })} />
                    </label>
                    <label className="text-[11px] font-semibold text-gray-500 space-y-1 col-span-2">
                        <span>Fallback models (while the main one is out of quota)</span>
                        <input className={field} value={form.fallback_models} placeholder="e.g. claude-sonnet-4-6"
                            onChange={e => setForm({ ...form, fallback_models: e.target.value })} />
                    </label>
                    <label className="text-[11px] font-semibold text-gray-500 space-y-1">
                        <span>Episodes at once</span>
                        <input type="number" min={1} max={8} className={field} value={form.lanes} disabled={running}
                            onChange={e => setForm({ ...form, lanes: e.target.value })} />
                    </label>
                    <label className="text-[11px] font-semibold text-gray-500 space-y-1">
                        <span>Of one show at once</span>
                        <input type="number" min={1} max={8} className={field} value={form.per_show}
                            onChange={e => setForm({ ...form, per_show: e.target.value })} />
                    </label>
                    <label className="text-[11px] font-semibold text-gray-500 space-y-1">
                        <span>Stop after (episodes, 0 = no limit)</span>
                        <input type="number" min={0} className={field} value={form.max_episodes}
                            onChange={e => setForm({ ...form, max_episodes: e.target.value })} />
                    </label>
                    <label className="flex items-center gap-2 text-[11px] font-semibold text-gray-500 pt-4">
                        <input type="checkbox" checked={form.watch} onChange={e => setForm({ ...form, watch: e.target.checked })}
                            className="w-4 h-4 text-purple-600 rounded border-gray-300" />
                        Keep watching the queue
                    </label>
                    <label className="flex items-center gap-2 text-[11px] font-semibold text-gray-500 pt-4 col-span-2"
                        title="When the CLI's safety filter refuses a part, its lines are set aside and sent on their own at the end of the episode: to the Gemini API (Settings → Gemini safety filter applies), else to the fallback model.">
                        <input type="checkbox" checked={form.api_rescue} onChange={e => setForm({ ...form, api_rescue: e.target.checked })}
                            className="w-4 h-4 text-purple-600 rounded border-gray-300" />
                        Send lines the CLI refuses to the Gemini API
                    </label>
                    <label className="text-[11px] font-semibold text-gray-500 space-y-1 col-span-2">
                        <span>Gemini API model for refused lines</span>
                        <input className={field} value={form.rescue_model} placeholder="the project's translation model"
                            disabled={!form.api_rescue}
                            onChange={e => setForm({ ...form, rescue_model: e.target.value })} />
                    </label>
                    <div className="col-span-2 md:col-span-4 flex items-center justify-between gap-2 text-[11px] text-gray-400">
                        <span>
                            {running ? 'Engine and lanes change when the runner starts again; the rest applies right away.'
                                : 'Uses your signed-in CLI; only lines its safety filter refuses go to the Gemini API. The runner resumes by itself when Omnisub restarts.'}
                        </span>
                        {running && (
                            <button type="button" onClick={start} disabled={busy}
                                className="px-2.5 py-1 font-bold text-white bg-purple-600 hover:bg-purple-700 rounded-lg disabled:opacity-50">
                                Apply
                            </button>
                        )}
                    </div>
                </div>
            )}

            {(lanes.length > 0 || pauses.length > 0 || session.calls > 0) && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                    <div className="space-y-1.5">
                        {lanes.map(l => {
                            const unitsTotal = Number(l.units_total) || 0;
                            const unitsDone = Math.min(Number(l.units_done) || 0, unitsTotal);
                            const pct = unitsTotal > 0 ? Math.round((unitsDone / unitsTotal) * 100) : 0;
                            return (
                                <div key={l.lane} className="space-y-1">
                                    <div className="flex items-center gap-2 text-gray-700 dark:text-gray-300">
                                        <Loader2 size={12} className="animate-spin text-purple-500 shrink-0" />
                                        <span className="font-semibold truncate">{l.project} {l.episode}</span>
                                        <span className="text-gray-400 truncate">— {l.state}{l.progress ? ` (${l.progress})` : ''}</span>
                                    </div>
                                    {unitsTotal > 0 && (
                                        <div className="flex items-center gap-2 pl-5" title={`${unitsDone} of ${unitsTotal}`}>
                                            <div
                                                className="flex-1 h-1.5 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden"
                                                role="progressbar" aria-valuemin={0} aria-valuemax={unitsTotal} aria-valuenow={unitsDone}
                                            >
                                                <div
                                                    className="h-full rounded-full bg-gradient-to-r from-purple-500 to-indigo-500 transition-all duration-500"
                                                    style={{ width: `${pct}%` }}
                                                />
                                            </div>
                                            <span className="w-8 text-right text-[10px] tabular-nums text-gray-400 shrink-0">{pct}%</span>
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                        {pauses.map(([model, until]) => (
                            <div key={model} className="flex items-center gap-2 text-amber-600 dark:text-amber-400">
                                <AlertTriangle size={12} className="shrink-0" />
                                <span>{model} paused until {fmtTime(until)}</span>
                            </div>
                        ))}
                    </div>
                    <div className="text-gray-500 dark:text-gray-400 md:text-right">
                        This session: <strong>{session.episodes || 0}</strong> episode(s), <strong>{session.lines || 0}</strong> lines,{' '}
                        {session.calls || 0} requests, {((session.tokens_in || 0) / 1000).toFixed(0)}k tokens in
                        {session.quota_hits ? `, ${session.quota_hits} quota stop(s)` : ''}
                    </div>
                </div>
            )}

            {info.config?.engine === 'antigravity' && (
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-gray-500 dark:text-gray-400">
                    <button type="button" onClick={checkQuota} disabled={checkingQuota}
                        className="flex items-center gap-1 font-semibold text-gray-600 dark:text-gray-300 hover:text-purple-600">
                        {checkingQuota ? <Loader2 size={12} className="animate-spin" /> : <Gauge size={12} />} Quota
                    </button>
                    {(info.quota || []).map(g => (
                        <span key={g.name}>
                            {g.name}:{' '}
                            {(g.buckets || []).map((b, i) => (
                                <span key={i} className={b.remaining != null && b.remaining <= 0.05 ? 'text-rose-500 font-semibold' : ''}>
                                    {i > 0 ? ' · ' : ''}{b.window ? `${b.window} ` : ''}{b.remaining != null ? `${Math.round(b.remaining * 100)}%` : '?'}
                                    {b.reset ? ` (resets ${fmtTime(b.reset)})` : ''}
                                </span>
                            ))}
                        </span>
                    ))}
                    {!info.quota && <span>{info.quota_fetched ? 'no quota report' : 'not checked yet'}</span>}
                </div>
            )}

            {log.length > 0 && (
                <div ref={logBox} className="max-h-56 overflow-y-auto rounded-xl bg-gray-950 p-3 font-mono text-[11px] leading-relaxed space-y-0.5 select-text">
                    {log.map(e => (
                        <div key={e.seq} className={LOG_STYLE[e.level] || LOG_STYLE.info}>
                            <span className="text-gray-500">{e.t}</span> {e.text}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

export default AgentRunnerPanel;
