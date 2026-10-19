import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Library, RefreshCw } from 'lucide-react';
import { api } from '../../api';
import { Button, Card, StatusChip, Toggle } from '../ui';
import { errorText, fmtAgo, fmtDateTime } from './autopilotMeta';

const TONE = { ok: 'success', bad: 'danger', warn: 'warning', neutral: 'neutral', off: 'neutral' };

const arrChip = (name, a) => {
    if (!a || !a.configured) return { kind: 'off', value: 'not set up', title: `${name} is not set up (Settings, Connections).` };
    const err = a.last_error || a.error;
    if (err) return { kind: 'bad', value: 'unreachable', title: `${err}${a.since ? ` (since ${fmtDateTime(a.since)})` : ''}` };
    if (a.last_poll_at || a.ok) return { kind: 'ok', value: 'connected', title: a.since ? `Connected since ${fmtDateTime(a.since)}` : 'Connected' };
    return { kind: 'neutral', value: 'waiting for the first check', title: `${name} is set up; no check has run yet.` };
};

const engineChip = (e) => {
    if (!e) return { kind: 'off', value: 'unknown', title: '' };
    const parts = [];
    if (e.runner_running) parts.push(`runner ${e.runner_status || 'running'}`);
    else if (e.runner_status && e.runner_status !== 'stopped') parts.push(`runner ${e.runner_status}`);
    if (e.worker_paused === false) parts.push('worker');
    return {
        kind: e.ok ? 'ok' : 'warn',
        value: parts.join(' + ') || 'none running',
        title: e.message || (e.ok ? 'A translation engine is running.' : 'No translation engine is running, so items wait in the queue.'),
    };
};

const plexChip = (p) => {
    if (!p || !p.enabled) return { kind: 'off', value: 'off', title: 'Plex refresh is off (Settings, Automation).' };
    return p.ok
        ? { kind: 'ok', value: 'connected', title: 'Plex refresh works.' }
        : { kind: 'bad', value: 'error', title: p.error || 'Plex is not reachable.' };
};

const StatusHeader = ({ status, toast, onChanged }) => {
    const [busy, setBusy] = useState('');
    const enabled = !!status.enabled;
    const poll = status.loops?.poll || {};

    const toggle = async (next) => {
        setBusy('toggle');
        try {
            await api.updateSettings({ autopilot_enabled: next });
            toast.success(next ? 'Autopilot is on.' : 'Autopilot is off.');
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

    return (
        <Card
            title="Autopilot"
            subtitle="Watches Sonarr and Radarr and translates new files that have no subtitle in your language."
            actions={(
                <Toggle
                    checked={enabled}
                    onChange={toggle}
                    disabled={busy === 'toggle'}
                    label={enabled ? (status.running === false ? 'On, starting' : 'On') : 'Off'}
                />
            )}
        >
            <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                    {chips.map(([label, c]) => (
                        <StatusChip key={label} tone={TONE[c.kind] || 'neutral'} title={c.title}>{label}: {c.value}</StatusChip>
                    ))}
                    <span className="text-xs text-gray-500 dark:text-gray-400" title={poll.last_error || (poll.last_run ? fmtDateTime(poll.last_run) : '')}>
                        Last check {poll.last_run ? fmtAgo(poll.last_run) : 'never'}
                        {poll.last_error && <span className="text-rose-500"> (error)</span>}
                    </span>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" icon={RefreshCw} loading={busy === 'poll'} disabled={!enabled || !!busy} onClick={() => run('poll', api.autopilotPollNow, 'Sonarr/Radarr check')}
                        title="Look for newly imported files right now">Check now</Button>
                    <Button size="sm" icon={Library} loading={busy === 'reconcile'} disabled={!enabled || !!busy} onClick={() => run('reconcile', api.autopilotReconcileNow, 'Reconcile')}
                        title="Walk the libraries and add files that were missed">Reconcile library</Button>
                    <Link to="/settings?section=automation" className="ml-auto text-xs font-medium text-indigo-600 hover:underline dark:text-indigo-400">Autopilot settings</Link>
                </div>

                {!enabled && (
                    <p className="rounded-lg bg-gray-50 p-3 text-sm text-gray-600 dark:bg-gray-800/60 dark:text-gray-300">
                        Autopilot is off, so new episodes and movies are not picked up. Only files imported after you switch it on are handled, unless you enable the backlog option in the settings.
                    </p>
                )}
                {enabled && status.engine && status.engine.ok === false && (
                    <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
                        {status.engine.message || 'No translation engine is running. Start the runner above, otherwise queued items just wait.'}
                    </p>
                )}
            </div>
        </Card>
    );
};

export default StatusHeader;
