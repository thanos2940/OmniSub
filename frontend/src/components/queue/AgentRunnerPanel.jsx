import { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { Activity, AlertTriangle, Clock, Gauge, Loader2, Pause, Play, RefreshCw, ScrollText, Settings } from 'lucide-react';
import { api } from '../../api';
import { Button, Card, OverflowMenu, ProgressBar, Stat, StatusChip } from '../ui';
import { describeRunner, ENGINE_LABEL } from '../activity/useRunnerStatus';
import { fmtMinutes, fmtRate, fmtTime } from '../activity/runnerFormat';

const LOG_STYLE = {
    ok: 'text-emerald-400',
    warn: 'text-amber-400',
    error: 'text-rose-400',
    info: 'text-gray-300',
};

const errorText = (err) => err.response?.data?.detail || err.response?.data?.message || err.message;

// State, controls and log of whatever translates. With the agent engine it shows the runner (lanes,
// quota pauses, speed); with the model engine it shows the Gemini worker. Its settings live in
// Settings > Translation.
export default function AgentRunnerPanel({ runner, queuePaused, onTogglePause, toast }) {
    const { info, engine, queue, log, refresh } = runner;
    const [busy, setBusy] = useState(false);
    const [showLog, setShowLog] = useState(false);
    const logBox = useRef(null);

    useEffect(() => {
        const box = logBox.current;
        if (box && box.scrollHeight - box.scrollTop - box.clientHeight < 80) box.scrollTop = box.scrollHeight;
    }, [log, showLog]);

    const act = async (fn, okMsg) => {
        setBusy(true);
        try {
            await fn();
            if (okMsg) toast.success(okMsg);
            await refresh();
        } catch (err) {
            toast.error(`Runner: ${errorText(err)}`);
        } finally {
            setBusy(false);
        }
    };

    const settingsLink = (
        <Link to="/settings?section=translation" className="inline-flex items-center gap-1 text-xs font-medium text-indigo-600 hover:underline dark:text-indigo-400">
            <Settings size={13} /> Runner settings
        </Link>
    );

    // ---- model engine: the Gemini worker
    if (engine === 'model') {
        const summary = queue?.summary || {};
        const paused = !!(queue?.paused ?? queuePaused);
        const state = describeRunner(null, 'model', queue);
        return (
            <Card
                title="Gemini worker"
                subtitle="Translating through the Gemini API (model engine)."
                actions={(
                    <>
                        <StatusChip tone={state.tone}>{state.label}</StatusChip>
                        <Button size="sm" icon={paused ? Play : Pause} onClick={onTogglePause}>{paused ? 'Resume translations' : 'Pause translations'}</Button>
                        {settingsLink}
                    </>
                )}
            >
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                    <Stat label="Running" value={summary.running || 0} tone={summary.running ? 'accent' : 'neutral'} />
                    <Stat label="Waiting" value={summary.pending || 0} />
                    <Stat label="Done today" value={summary.completed_today || 0} tone="success" />
                    <Stat label="Failed" value={summary.failed || 0} tone={summary.failed ? 'danger' : 'neutral'} />
                </div>
            </Card>
        );
    }

    if (!info) {
        return <Card title="Translation runner"><div className="flex items-center gap-2 text-sm text-gray-500"><Loader2 size={16} className="animate-spin" /> Loading...</div></Card>;
    }

    // ---- agent engine: the runner
    const running = !!info.running;
    const status = info.status || 'stopped';
    const paused = info.paused ?? queuePaused ?? false;
    const state = describeRunner(info, 'agent', null);
    const config = info.config || {};
    const cli = ENGINE_LABEL[config.engine] || 'agent CLI';
    const lanes = (info.lanes || []).filter(l => l.task);
    const pauses = Object.entries(info.pauses || {});
    const session = info.session || {};
    const pending = Number.isFinite(Number(info.pending)) ? Number(info.pending) : null;
    const throughput = info.throughput || null;
    const rate = fmtRate(throughput?.lines_per_min);
    const eta = fmtMinutes(info.eta_minutes);
    const engineMissing = info.engines && !info.engines[config.engine];
    const lastError = [...log].reverse().find(e => e.level === 'error');
    const startRunner = () => act(() => api.startAgentRunner({ ...config, watch: true }), 'Runner started.');

    const checkQuota = async () => {
        setBusy(true);
        await refresh(true);
        setBusy(false);
    };

    const primary = paused
        ? <Button size="sm" variant="primary" icon={Play} loading={busy} onClick={() => act(async () => { await api.resumeQueue(); if (!running) await api.startAgentRunner({ ...config, watch: true }); }, 'Translations resumed.')}>Resume translations</Button>
        : !running || status === 'stopping'
            ? <Button size="sm" variant="primary" icon={Play} loading={busy} disabled={engineMissing} onClick={startRunner}
                title={engineMissing ? `${cli} is not installed on this computer` : 'Start translating the queue'}>{status === 'stopping' ? 'Keep going' : 'Start runner'}</Button>
            : <Button size="sm" icon={Pause} loading={busy} onClick={() => act(() => api.pauseQueue(), 'Translations paused. Episodes in hand are finished first.')}>Pause translations</Button>;

    return (
        <Card
            title="Translation runner"
            subtitle={info.message || `${cli}${config.model ? ` · ${config.model}` : ' · default model'}`}
            actions={(
                <>
                    <StatusChip tone={state.tone}>{state.label}</StatusChip>
                    {pauses.length > 0 && (
                        <Button size="sm" icon={RefreshCw} loading={busy} onClick={() => act(() => api.resumeAgentRunnerNow(), 'Pauses cleared.')}
                            title="Clear quota pauses (for example after adding credits) and try again now">Resume now</Button>
                    )}
                    {primary}
                    <OverflowMenu
                        items={[
                            { label: 'Stop after the current episodes', icon: Pause, hidden: !running, onClick: () => act(() => api.stopAgentRunner(false), 'The runner stops after the episodes in hand.') },
                            { label: 'Stop now', icon: AlertTriangle, danger: true, hidden: !running, onClick: () => act(() => api.stopAgentRunner(true), 'Runner stopped.') },
                            { label: 'Check agy quota', icon: Gauge, hidden: config.engine !== 'antigravity', onClick: checkQuota },
                        ]}
                        label="More runner actions"
                    />
                </>
            )}
        >
            <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                    <Stat label="Waiting" value={pending ?? '-'} hint="episodes for the runner" />
                    <Stat label="Speed" value={rate ? `${rate}/min` : '-'} hint="lines translated" />
                    <Stat label="Time left" value={eta || '-'} hint="at the current speed" />
                    <Stat label="This session" value={session.episodes || 0} hint={`${session.lines || 0} lines · ${session.calls || 0} requests`} />
                </div>

                {paused && (
                    <p className="flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
                        <Pause size={16} className="mt-0.5 shrink-0" />
                        Translations are paused. Lanes finish the episode in hand and take no new one until you resume.
                    </p>
                )}
                {pauses.map(([model, until]) => (
                    <p key={model} className="flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
                        <Clock size={16} className="mt-0.5 shrink-0" />
                        <span><strong>{model}</strong> is out of quota until {fmtTime(until)}. The runner carries on by itself, or press Resume now after adding credits.</span>
                    </p>
                ))}
                {(status === 'error' || lastError) && (
                    <p className="flex items-start gap-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-800 dark:bg-rose-950/30 dark:text-rose-300">
                        <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                        <span>Last error: {status === 'error' && info.message ? info.message : lastError?.text}</span>
                    </p>
                )}
                {engineMissing && (
                    <p className="flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
                        <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                        {cli} was not found on this computer, so the runner cannot start. Check the runner settings.
                    </p>
                )}

                {lanes.length > 0 && (
                    <ul className="space-y-3">
                        {lanes.map(l => {
                            const total = Number(l.units_total) || 0;
                            const done = Math.min(Number(l.units_done) || 0, total);
                            return (
                                <li key={l.lane}>
                                    <div className="flex items-center gap-2 text-sm text-gray-800 dark:text-gray-100">
                                        <Loader2 size={14} className="shrink-0 animate-spin text-indigo-500" />
                                        <span className="truncate font-medium">{l.project} · {l.episode}</span>
                                        <span className="ml-auto shrink-0 text-xs text-gray-500 dark:text-gray-400">{l.state}{l.progress ? ` (${l.progress})` : ''}</span>
                                    </div>
                                    {total > 0 && (
                                        <div className="mt-1.5 flex items-center gap-2" title={`${done} of ${total} lines`}>
                                            <ProgressBar value={done} max={total} />
                                            <span className="w-10 shrink-0 text-right text-xs tabular-nums text-gray-500">{Math.round((done / total) * 100)}%</span>
                                        </div>
                                    )}
                                </li>
                            );
                        })}
                    </ul>
                )}
                {lanes.length === 0 && running && !paused && pauses.length === 0 && (
                    <p className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400"><Activity size={15} /> {status === 'idle' ? 'Nothing to translate; watching the queue.' : 'No episode in progress right now.'}</p>
                )}

                {info.quota && info.quota.length > 0 && (
                    <div className="space-y-1 text-xs text-gray-600 dark:text-gray-300">
                        {info.quota.map(g => (
                            <div key={g.name}>
                                <strong>{g.name}</strong>:{' '}
                                {(g.buckets || []).map((b, i) => (
                                    <span key={i} className={b.remaining != null && b.remaining <= 0.05 ? 'font-semibold text-rose-500' : ''}>
                                        {i > 0 ? ' · ' : ''}{b.window ? `${b.window} ` : ''}{b.remaining != null ? `${Math.round(b.remaining * 100)}% left` : '?'}
                                        {b.reset ? ` (resets ${fmtTime(b.reset)})` : ''}
                                    </span>
                                ))}
                            </div>
                        ))}
                    </div>
                )}

                <div className="flex flex-wrap items-center justify-between gap-2">
                    <Button size="sm" variant="ghost" icon={ScrollText} onClick={() => setShowLog(v => !v)}>{showLog ? 'Hide log' : `Show log${log.length ? ` (${log.length})` : ''}`}</Button>
                    {settingsLink}
                </div>
                {showLog && (
                    log.length === 0
                        ? <p className="text-sm text-gray-500 dark:text-gray-400">The runner has not written anything yet.</p>
                        : (
                            <div ref={logBox} className="max-h-60 select-text space-y-0.5 overflow-y-auto rounded-lg bg-gray-950 p-3 font-mono text-xs leading-relaxed" role="log">
                                {log.map(e => (
                                    <div key={e.seq} className={`break-words ${LOG_STYLE[e.level] || LOG_STYLE.info}`}>
                                        <span className="text-gray-500">{e.t}</span> {e.text}
                                    </div>
                                ))}
                            </div>
                        )
                )}
            </div>
        </Card>
    );
}
