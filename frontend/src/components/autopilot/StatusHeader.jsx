import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Plane, Loader2, RefreshCw, Library, Settings as SettingsIcon } from 'lucide-react';
import { api } from '../../api';
import { errorText, fmtAgo, fmtDateTime } from './autopilotMeta';

const CHIP = {
    ok: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400',
    bad: 'bg-rose-500/10 border-rose-500/30 text-rose-600 dark:text-rose-400',
    warn: 'bg-amber-500/10 border-amber-500/30 text-amber-600 dark:text-amber-400',
    neutral: 'bg-slate-500/10 border-slate-500/30 text-slate-600 dark:text-slate-400',
    off: 'bg-gray-500/10 border-gray-500/30 text-gray-500 dark:text-gray-400',
};

const Chip = ({ label, value, kind, ok, error, last_error, last_poll_at, title }) => {
    let finalKind = kind;
    let finalValue = value;
    if (!finalKind) {
        const err = last_error || error;
        if (Boolean(err)) {
            finalKind = 'bad';
            finalValue = finalValue || 'error';
        } else if (Boolean((last_poll_at || ok) && !err)) {
            finalKind = 'ok';
            finalValue = finalValue || 'connected';
        } else {
            finalKind = 'neutral';
            finalValue = finalValue || 'awaiting poll / webhook';
        }
    }
    return (
        <span title={title} className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold border ${CHIP[finalKind] || CHIP.neutral}`}>
            <span className="w-1.5 h-1.5 rounded-full bg-current" />
            <span>{label}</span>
            {finalValue && <span className="font-medium opacity-80">{finalValue}</span>}
        </span>
    );
};

export const StatusPill = Chip;

const arrChip = (name, a) => {
    if (!a || !a.configured) return { kind: 'off', value: 'not set up', title: `${name} is not configured (Settings, Media Servers).` };
    const err = a.last_error || a.error;
    const isPolled = Boolean(a.last_poll_at || a.ok);
    if (Boolean(err)) {
        return { kind: 'bad', value: 'unreachable', title: `${err}${a.since ? ` (since ${fmtDateTime(a.since)})` : ''}` };
    }
    if (isPolled) {
        return { kind: 'ok', value: 'connected', title: a.since ? `Connected since ${fmtDateTime(a.since)}` : 'Connected' };
    }
    return { kind: 'neutral', value: 'awaiting poll / webhook', title: `${name} is configured; awaiting first poll or webhook event.` };
};

const engineChip = (e) => {
    if (!e) return { kind: 'off', value: 'unknown', title: '' };
    const parts = [];
    if (e.runner_running) parts.push(`runner ${e.runner_status || 'running'}`);
    else if (e.runner_status && e.runner_status !== 'stopped') parts.push(`runner ${e.runner_status}`);
    if (e.worker_paused === false) parts.push('worker');
    const value = parts.join(' + ') || 'none running';
    return {
        kind: e.ok ? 'ok' : 'warn',
        value,
        title: e.message || (e.ok ? 'A translation engine is running.' : 'No translation engine is running, so items wait in the queue.'),
    };
};

const plexChip = (p) => {
    if (!p || !p.enabled) return { kind: 'off', value: 'off', title: 'Plex refresh is off (Settings, Autopilot).' };
    return p.ok
        ? { kind: 'ok', value: 'connected', title: 'Plex refresh works.' }
        : { kind: 'bad', value: 'error', title: p.error || 'Plex is not reachable.' };
};

const StatusHeader = ({ status, toast, onChanged }) => {
    const [busy, setBusy] = useState('');
    const enabled = !!status.enabled;
    const poll = status.loops?.poll || {};

    const toggle = async () => {
        setBusy('toggle');
        try {
            await api.updateSettings({ autopilot_enabled: !enabled });
            toast.success(!enabled ? 'Autopilot is on.' : 'Autopilot is off.');
            await onChanged();
        } catch (e) {
            toast.error(`Could not change Autopilot: ${errorText(e)}`);
        } finally {
            setBusy('');
        }
    };

    const run = async (key, fn, label) => {
        setBusy(key);
        try {
            const res = await fn();
            const d = res.data || {};
            if (d.success === false) toast.warning(`${label}: ${d.summary || 'nothing done'}`);
            else toast.success(d.summary ? `${label}: ${d.summary}` : `${label} done.`);
            await onChanged();
        } catch (e) {
            toast.error(`${label} failed: ${errorText(e)}`);
        } finally {
            setBusy('');
        }
    };

    const chips = [
        ['Sonarr', arrChip('Sonarr', status.arr?.sonarr)],
        ['Radarr', arrChip('Radarr', status.arr?.radarr)],
        ['Engine', engineChip(status.engine)],
        ['Plex', plexChip(status.plex)],
    ];
    const btn = 'flex items-center gap-1.5 text-xs font-semibold border px-3 py-1.5 rounded-lg shadow-sm bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50';

    return (
        <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm space-y-3">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
                <div className="flex items-start gap-3">
                    <Plane className={`mt-0.5 shrink-0 ${enabled ? 'text-emerald-500' : 'text-gray-400'}`} size={22} />
                    <div>
                        <div className="flex items-center gap-2 flex-wrap">
                            <h3 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">Autopilot</h3>
                            <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${enabled ? CHIP.ok : CHIP.off}`}>
                                {enabled ? (status.running === false ? 'on, starting' : 'on') : 'off'}
                            </span>
                        </div>
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                            Watches Sonarr and Radarr, waits for Bazarr, and makes Greek subtitles for new files that have none.
                        </p>
                    </div>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                    <span className="text-xs font-semibold text-gray-600 dark:text-gray-300">{enabled ? 'Enabled' : 'Disabled'}</span>
                    <button
                        type="button" role="switch" aria-checked={enabled} aria-label="Autopilot on or off"
                        onClick={toggle} disabled={busy === 'toggle'}
                        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors disabled:opacity-50 ${enabled ? 'bg-emerald-500' : 'bg-gray-300 dark:bg-gray-600'}`}
                    >
                        <span className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${enabled ? 'translate-x-5' : 'translate-x-0.5'}`} />
                    </button>
                </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
                {chips.map(([label, c]) => <Chip key={label} label={label} {...c} />)}
                <span className="text-[11px] text-gray-500 dark:text-gray-400 ml-1" title={poll.last_error || (poll.last_run ? fmtDateTime(poll.last_run) : '')}>
                    Last check: <strong className="text-gray-700 dark:text-gray-200">{poll.last_run ? fmtAgo(poll.last_run) : 'never'}</strong>
                    {poll.last_error && <span className="text-rose-500"> (error)</span>}
                </span>
            </div>

            <div className="flex flex-wrap items-center gap-2 pt-1">
                <button type="button" className={btn} disabled={!enabled || !!busy} onClick={() => run('poll', api.autopilotPollNow, 'Sonarr/Radarr check')}
                    title="Look for newly imported files right now">
                    {busy === 'poll' ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Check Sonarr/Radarr now
                </button>
                <button type="button" className={btn} disabled={!enabled || !!busy} onClick={() => run('reconcile', api.autopilotReconcileNow, 'Reconcile')}
                    title="Walk the libraries and add files that were missed">
                    {busy === 'reconcile' ? <Loader2 size={13} className="animate-spin" /> : <Library size={13} />} Reconcile library
                </button>
                <Link to="/settings" state={{ tab: 'autopilot' }} className={btn}>
                    <SettingsIcon size={13} /> Autopilot settings
                </Link>
            </div>

            {!enabled && (
                <div className="text-xs rounded-xl bg-gray-50 dark:bg-gray-800/60 border border-gray-200 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-300">
                    Autopilot is off, so new episodes and movies are not picked up. Switch it on above. Only files imported after you switch it on are handled, unless you enable the backlog option in the settings.
                </div>
            )}
            {enabled && status.engine && status.engine.ok === false && (
                <div className="text-xs rounded-xl bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-800/50 p-3 text-amber-800 dark:text-amber-300">
                    {status.engine.message || 'No translation engine is running. Start the agent runner (Queue page) or the Gemini worker, otherwise queued items just wait.'}
                </div>
            )}
        </div>
    );
};

export default StatusHeader;
