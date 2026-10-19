import { useState, useEffect, useCallback, useRef } from 'react';
import { RefreshCw, Power } from 'lucide-react';
import { api } from '../../api';
import { useToast } from '../../context/ToastContext';
import { Button, Card, ConfirmDialog, ProgressBar, Spinner, StatusChip } from '../ui';
import LibraryTranslationCheck from '../health/LibraryTranslationCheck';
import { formatBytes, formatUptime } from './systemFormat';
import { waitForServer } from './waitForServer';

const HEALTH_CHIP = {
    pass: { tone: 'success', label: 'OK' },
    warn: { tone: 'warning', label: 'Warning' },
    fail: { tone: 'danger', label: 'Problem' },
    skip: { tone: 'neutral', label: 'Not used' },
};

function Row({ label, children, hint }) {
    return (
        <div className="grid grid-cols-1 gap-1 py-2.5 sm:grid-cols-[11rem_1fr] sm:gap-4">
            <dt className="text-sm text-gray-500 dark:text-gray-400">{label}</dt>
            <dd className="min-w-0 break-words text-sm text-gray-900 dark:text-gray-100">
                {children}
                {hint && <div className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">{hint}</div>}
            </dd>
        </div>
    );
}

function HealthRow({ label, status, detail, hint }) {
    const chip = HEALTH_CHIP[status] || HEALTH_CHIP.skip;
    return (
        <li className="flex items-start gap-3 py-2.5">
            <StatusChip tone={chip.tone} className="mt-0.5 w-20 shrink-0 justify-center">{chip.label}</StatusChip>
            <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-gray-900 dark:text-white">{label}</div>
                {detail && <div className="break-words text-sm text-gray-500 dark:text-gray-400">{detail}</div>}
                {hint && status !== 'pass' && <div className="mt-0.5 text-xs text-amber-700 dark:text-amber-400">{hint}</div>}
            </div>
        </li>
    );
}

// The health checks as rows. The Gemini key is optional (only refused lines use it), and the Gemini worker
// only matters on the model engine, so neither is reported as a problem otherwise.
function buildHealthRows(health, engine) {
    if (!health) return [];
    const rows = [];
    const key = health.api_key || {};
    const optional = key.status === 'fail' && key.detail === 'missing' && engine !== 'model';
    rows.push({
        label: 'Gemini API key',
        status: optional ? 'skip' : key.status,
        detail: optional ? 'Not set. It is only needed for lines the agent refuses.' : key.detail,
        hint: optional ? '' : key.hint,
    });
    rows.push({ label: 'Sonarr', ...health.sonarr });
    rows.push({ label: 'Radarr', ...health.radarr });
    rows.push({ label: 'Write access to media folders', ...health.paths?.write_test });
    (health.paths?.mappings || []).forEach(m => rows.push({ label: `Path mapping ${m.remote || '(any)'} to ${m.local}`, status: m.status, hint: m.hint }));
    if (engine === 'model' && health.worker) {
        rows.push({
            label: 'Gemini worker',
            status: health.worker.status,
            detail: `${health.worker.running ? 'running' : 'stopped'}${health.worker.paused ? ' (paused)' : ''} · ${health.worker.queue?.pending || 0} waiting, ${health.worker.queue?.running || 0} running`,
            hint: health.worker.hint,
        });
    }
    if (health.ffmpeg) rows.push({ label: 'ffmpeg (subtitles inside videos)', ...health.ffmpeg });
    rows.push({ label: 'Disk space', ...health.disk });
    if (health.webhooks) {
        rows.push({
            label: 'Webhook secret',
            status: health.webhooks.status,
            detail: health.webhooks.secured ? 'Set. Sonarr and Radarr webhooks are accepted.' : 'Not set. Webhooks are rejected.',
            hint: health.webhooks.hint,
        });
    }
    return rows;
}

const overallOf = (rows) => (rows.some(r => r.status === 'fail') ? 'fail' : rows.some(r => r.status === 'warn') ? 'warn' : 'pass');

export default function StatusPanel() {
    const toast = useToast();
    const [status, setStatus] = useState(null);
    const [health, setHealth] = useState(null);
    const [engine, setEngine] = useState('agent');
    const [checking, setChecking] = useState(false);
    const [confirmRestart, setConfirmRestart] = useState(false);
    const [restarting, setRestarting] = useState(false);
    const alive = useRef(true);

    const loadStatus = useCallback(async () => {
        try {
            const res = await api.getSystemStatus();
            if (alive.current) setStatus(res.data);
            return res.data;
        } catch (err) {
            if (alive.current) toast.error(`Could not load the server status: ${err.response?.data?.detail || err.message}`);
            return null;
        }
    }, [toast]);

    const runChecks = useCallback(async () => {
        setChecking(true);
        try {
            const res = await api.getFullHealth();
            if (alive.current) setHealth(res.data);
        } catch (err) {
            if (alive.current) toast.error(`The health checks failed: ${err.response?.data?.detail || err.message}`);
        } finally {
            if (alive.current) setChecking(false);
        }
    }, [toast]);

    useEffect(() => {
        alive.current = true;
        loadStatus();
        runChecks();
        api.getSettings().then(res => { if (alive.current && res.data?.translation_engine === 'model') setEngine('model'); }).catch(() => {});
        return () => { alive.current = false; };
    }, [loadStatus, runChecks]);

    // Restart, then wait until the server answers again (it can take a minute under the supervisor).
    const restart = async () => {
        setRestarting(true);
        setConfirmRestart(false);
        const before = status?.started_at;
        try {
            await api.restartServer();
        } catch (err) {
            toast.error(`Could not restart: ${err.response?.data?.detail || err.message}`);
            setRestarting(false);
            return;
        }
        toast.info('Restarting. This page continues when the server is back.');
        const fresh = await waitForServer({ startedBefore: before, cancelled: () => !alive.current });
        if (!alive.current) return;
        setRestarting(false);
        if (fresh) {
            setStatus(fresh);
            toast.success('The server is back.');
            runChecks();
        } else {
            toast.error('The server did not come back within two minutes. Check the service or start OmbiSub again.');
        }
    };

    if (!status) return <Spinner label="Loading the server status..." />;

    const rows = buildHealthRows(health, engine);
    const overall = HEALTH_CHIP[overallOf(rows)];
    const disk = status.disk || {};
    const diskUsedPct = disk.total_bytes ? Math.round(((disk.total_bytes - disk.free_bytes) / disk.total_bytes) * 100) : null;
    const runner = status.agent_runner || {};
    const clis = status.agent_clis || {};
    const unsupervised = !status.supervisor || status.supervisor === 'none';

    return (
        <div className="space-y-5">
            <Card
                title="This server"
                actions={<Button size="sm" variant="danger" icon={Power} loading={restarting} onClick={() => setConfirmRestart(true)}>Restart</Button>}
            >
                <dl className="divide-y divide-gray-100 dark:divide-gray-750">
                    <Row label="Version" hint={status.commit_message}>
                        {status.app_version ? `${status.app_version} · ` : ''}
                        <span className="font-mono">{status.commit_short || 'unknown commit'}</span>
                        {status.branch && <span className="text-gray-500 dark:text-gray-400"> on {status.branch}</span>}
                    </Row>
                    <Row label="Running for" hint={status.started_at ? `Started ${new Date(status.started_at).toLocaleString()}` : undefined}>{formatUptime(status.uptime_seconds)}</Row>
                    <Row label="Address">
                        {status.port ? `Port ${status.port}` : 'Port not reported'}
                        {status.url_base_active ? <span className="font-mono"> · path {status.url_base_active}</span> : <span className="text-gray-500 dark:text-gray-400"> · served at the root</span>}
                        {status.url_base !== status.url_base_active && <StatusChip tone="warning" className="ml-2">restart to use {status.url_base || 'the root'}</StatusChip>}
                    </Row>
                    <Row label="Supervisor" hint={unsupervised ? 'OmbiSub is not under a supervisor, so a restart stops it until someone starts it again.' : 'Restarts the server after a crash or an update.'}>
                        {unsupervised ? 'none' : status.supervisor}
                    </Row>
                    <Row label="Data folder">
                        <span className="font-mono">{status.data_dir}</span>
                        {disk.free_bytes != null && (
                            <div className="mt-2 max-w-sm">
                                <ProgressBar value={diskUsedPct ?? 0} tone={disk.free_bytes < 2 * 1024 ** 3 ? 'danger' : 'accent'} />
                                <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">{formatBytes(disk.free_bytes)} free of {formatBytes(disk.total_bytes)}</div>
                            </div>
                        )}
                    </Row>
                    <Row label="Python" hint={status.python?.executable}>{status.python?.version}{status.platform ? ` · ${status.platform}` : ''}</Row>
                    <Row label="ffmpeg">
                        {status.ffmpeg ? <span className="font-mono">{status.ffmpeg}</span> : <span className="text-gray-500 dark:text-gray-400">Not found. Subtitles inside videos cannot be extracted.</span>}
                    </Row>
                    <Row label="ffprobe">
                        {status.ffprobe ? <span className="font-mono">{status.ffprobe}</span> : <span className="text-gray-500 dark:text-gray-400">Not found.</span>}
                    </Row>
                    <Row label="Agent CLIs">
                        <div className="flex flex-wrap gap-2">
                            <StatusChip tone={clis.agy ? 'success' : 'neutral'} title={clis.agy || undefined}>agy {clis.agy ? 'found' : 'not found'}</StatusChip>
                            <StatusChip tone={clis.claude ? 'success' : 'neutral'} title={clis.claude || undefined}>claude {clis.claude ? 'found' : 'not found'}</StatusChip>
                        </div>
                    </Row>
                    <Row label="Translation runner" hint={runner.message || undefined}>
                        {runner.error ? <span className="text-rose-600 dark:text-rose-400">{runner.error}</span> : (
                            <>
                                {runner.running ? runner.status : 'stopped'}
                                <span className="text-gray-500 dark:text-gray-400"> · {runner.pending ?? 0} episode{runner.pending === 1 ? '' : 's'} waiting</span>
                            </>
                        )}
                    </Row>
                </dl>
            </Card>

            <Card
                title="Health checks"
                subtitle={health ? undefined : 'Running the checks...'}
                actions={(
                    <>
                      {health && <StatusChip tone={overall.tone}>{overall.label}</StatusChip>}
                      <Button size="sm" icon={RefreshCw} loading={checking} onClick={runChecks}>Run again</Button>
                    </>
                )}
            >
                {health ? (
                    <ul className="divide-y divide-gray-100 dark:divide-gray-750">
                        {rows.map(r => <HealthRow key={r.label} {...r} />)}
                    </ul>
                ) : <Spinner label="Checking Sonarr, Radarr, disks and paths..." />}
            </Card>

            <LibraryTranslationCheck />

            <ConfirmDialog
                open={confirmRestart}
                title="Restart OmbiSub?"
                message={unsupervised
                    ? 'OmbiSub is not running under a supervisor. It will stop, and you need to start it again yourself.'
                    : 'Translations in progress stop and resume after the restart. This takes about a minute.'}
                confirmLabel="Restart"
                danger
                onConfirm={restart}
                onCancel={() => setConfirmRestart(false)}
            />
        </div>
    );
}
