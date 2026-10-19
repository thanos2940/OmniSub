import { useState, useEffect, useCallback, useRef } from 'react';
import { ArrowDownCircle, CheckCircle2, ExternalLink, GitCommit, Info, RefreshCw } from 'lucide-react';
import { api } from '../../api';
import { useToast } from '../../context/ToastContext';
import { Button, Card, ConfirmDialog, Field, INPUT, Spinner, StatusChip, Toggle } from '../ui';
import { waitForServer } from './waitForServer';

const CHANNELS = [
    { id: 'auto', label: 'Automatic: main until releases are published' },
    { id: 'main', label: 'main branch: every pushed commit' },
    { id: 'releases', label: 'GitHub releases only' },
];
const INTERVALS = [1, 3, 6, 12, 24];

function Fact({ label, children, sub }) {
    return (
        <div className="min-w-0">
            <dt className="text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">{label}</dt>
            <dd className="mt-1 truncate font-mono text-sm text-gray-900 dark:text-white">{children}</dd>
            {sub && <div className="mt-0.5 truncate text-xs text-gray-500 dark:text-gray-400" title={sub}>{sub}</div>}
        </div>
    );
}

export default function UpdatesPanel() {
    const toast = useToast();
    const [update, setUpdate] = useState(null);
    const [loading, setLoading] = useState(true);
    const [checking, setChecking] = useState(false);
    const [applying, setApplying] = useState(false);
    const [confirmApply, setConfirmApply] = useState(false);
    const [saving, setSaving] = useState(false);
    const alive = useRef(true);

    const load = useCallback(async (force = false) => {
        if (force) setChecking(true);
        try {
            const res = force ? await api.checkForUpdates() : await api.getUpdateStatus();
            if (!alive.current) return;
            const data = res.data;
            setUpdate(data);
            if (force) {
                if (data.update_available) {
                    toast.info(data.channel === 'main'
                        ? `${data.behind} new commit${data.behind === 1 ? '' : 's'} on main.`
                        : `New release: ${data.latest_release?.tag_name || data.latest_commit}.`);
                } else if (data.release_status === 'no_releases_published') {
                    toast.info('No GitHub releases have been published yet.');
                } else {
                    toast.success('OmbiSub is up to date.');
                }
            }
        } catch (err) {
            if (force) toast.error(`Could not check for updates: ${err.response?.data?.detail || err.message}`);
        } finally {
            if (alive.current) {
                setLoading(false);
                setChecking(false);
            }
        }
    }, [toast]);

    useEffect(() => {
        alive.current = true;
        load(false);
        return () => { alive.current = false; };
    }, [load]);

    const saveAuto = async (patch) => {
        const next = {
            enabled: update.auto_update_enabled ?? false,
            channel: update.update_channel ?? 'auto',
            intervalHours: update.auto_update_interval_hours ?? 6,
            ...patch,
        };
        const before = update;
        setUpdate({ ...update, auto_update_enabled: next.enabled, update_channel: next.channel, auto_update_interval_hours: next.intervalHours });
        setSaving(true);
        try {
            await api.setAutoUpdateConfig(next);
            if (patch.channel) load(true); // another channel has another answer
        } catch (err) {
            setUpdate(before);
            toast.error(`Could not save: ${err.response?.data?.detail || err.message}`);
        } finally {
            setSaving(false);
        }
    };

    const apply = async () => {
        setApplying(true);
        setConfirmApply(false);
        const before = (await api.getSystemStatus().catch(() => null))?.data?.started_at;
        try {
            const res = await api.applyUpdate(false);
            toast.success(res.data?.message || 'Update installed. Restarting...');
        } catch (err) {
            const detail = err.response?.data?.detail || err.message;
            if (err.response?.status === 409) toast.warning(detail);
            else toast.error(`The update failed: ${detail}`);
            setApplying(false);
            return;
        }
        const fresh = await waitForServer({ startedBefore: before, cancelled: () => !alive.current });
        if (!alive.current) return;
        setApplying(false);
        if (fresh) {
            toast.success('The server is back on the new version.');
            load(false);
        } else {
            toast.error('The server did not come back within two minutes. Check the service or start OmbiSub again.');
        }
    };

    if (loading) return <Spinner label="Checking the installed version..." />;
    if (!update) return <Card><p className="text-sm text-gray-600 dark:text-gray-300">Could not read the update status. Try again in a moment.</p></Card>;

    const isMain = update.channel === 'main';
    const latestRelease = update.latest_release;
    const commits = update.commits || [];
    const installed = update.installed_release?.tag_name || update.tag_name || update.current_commit;
    const notChecked = update.checked === false;
    const noReleases = update.release_status === 'no_releases_published' && !isMain;

    let chip;
    if (update.update_available) {
        chip = <StatusChip tone="warning" icon={ArrowDownCircle}>{isMain ? `${update.behind} new commit${update.behind === 1 ? '' : 's'}` : `Release ${latestRelease?.tag_name || 'available'}`}</StatusChip>;
    } else if (notChecked) {
        chip = <StatusChip tone="info" icon={Info}>Not checked yet</StatusChip>;
    } else if (noReleases) {
        chip = <StatusChip tone="info" icon={Info}>No releases published</StatusChip>;
    } else {
        chip = <StatusChip tone="success" icon={CheckCircle2}>Up to date</StatusChip>;
    }

    return (
        <div className="space-y-5">
            <Card
                title={isMain ? 'Following the main branch' : 'Following GitHub releases'}
                subtitle="thanos2940/OmbiSub---Google-Seminar-Capstone-Project"
                actions={(
                    <>
                        {chip}
                        <Button size="sm" icon={RefreshCw} loading={checking} disabled={applying} onClick={() => load(true)}>Check now</Button>
                        {update.update_available && (
                            <Button size="sm" variant="primary" icon={ArrowDownCircle} loading={applying} onClick={() => setConfirmApply(true)}>Install and restart</Button>
                        )}
                    </>
                )}
            >
                <dl className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                    <Fact label="Installed" sub={update.current_commit ? `commit ${update.current_commit}` : undefined}>{installed || 'untagged'}</Fact>
                    <Fact label={isMain ? 'Newest on main' : 'Newest release'} sub={latestRelease?.published_at ? `published ${latestRelease.published_at.slice(0, 10)}` : (update.latest_message || undefined)}>
                        {isMain ? (update.latest_commit || update.current_commit) : (latestRelease?.tag_name || (noReleases ? 'none' : installed))}
                    </Fact>
                    <Fact label="Last checked">{update.last_checked || (notChecked ? 'never' : 'just now')}</Fact>
                </dl>
                {isMain && update.ahead > 0 && (
                    <p className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
                        This copy has {update.ahead} commit{update.ahead === 1 ? '' : 's'} that are not on origin/main. Updating is refused until they are pushed.
                    </p>
                )}
            </Card>

            {isMain && commits.length > 0 && (
                <Card
                    title={`What is new (${update.behind} commit${update.behind === 1 ? '' : 's'}${commits.length < update.behind ? `, newest ${commits.length} shown` : ''})`}
                >
                    <ul className="max-h-72 space-y-1.5 overflow-y-auto text-sm">
                        {commits.map(c => (
                            <li key={c.hash} className="flex items-start gap-2">
                                <GitCommit size={14} className="mt-1 shrink-0 text-gray-400" />
                                <span className="font-mono text-xs text-gray-500 dark:text-gray-400">{c.hash}</span>
                                <span className="min-w-0 break-words text-gray-800 dark:text-gray-200">{c.message}</span>
                            </li>
                        ))}
                    </ul>
                </Card>
            )}

            {latestRelease?.body && (
                <Card
                    title={`Release notes: ${latestRelease.name || latestRelease.tag_name}`}
                    actions={latestRelease.html_url && (
                        <a href={latestRelease.html_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs font-medium text-indigo-600 hover:underline dark:text-indigo-400">
                            View on GitHub <ExternalLink size={12} />
                        </a>
                    )}
                >
                    <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap rounded-lg bg-gray-50 p-3 font-mono text-xs leading-relaxed text-gray-700 dark:bg-gray-900 dark:text-gray-300">{latestRelease.body}</pre>
                </Card>
            )}

            <Card title="Automatic updates" subtitle="OmbiSub checks GitHub in the background. With this on, it installs an update and restarts as soon as the translation queue is idle.">
                <div className="space-y-4">
                    <Toggle
                        checked={!!update.auto_update_enabled}
                        onChange={(v) => saveAuto({ enabled: v })}
                        disabled={saving}
                        label="Install updates when idle"
                    />
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <Field label="Update channel" htmlFor="upd-channel">
                            <select id="upd-channel" className={INPUT} value={update.update_channel ?? 'auto'} disabled={saving} onChange={e => saveAuto({ channel: e.target.value })}>
                                {CHANNELS.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                            </select>
                        </Field>
                        <Field label="Check every" htmlFor="upd-interval">
                            <select id="upd-interval" className={INPUT} value={update.auto_update_interval_hours ?? 6} disabled={saving} onChange={e => saveAuto({ intervalHours: parseInt(e.target.value, 10) })}>
                                {INTERVALS.map(h => <option key={h} value={h}>{h === 1 ? 'Every hour' : `Every ${h} hours`}</option>)}
                            </select>
                        </Field>
                    </div>
                </div>
            </Card>

            <ConfirmDialog
                open={confirmApply}
                title="Install the update?"
                message={`OmbiSub downloads ${isMain ? 'the newest commits of main' : `release ${latestRelease?.tag_name || ''}`}, updates changed dependencies, rebuilds the web interface and restarts. Projects, glossaries and settings in the data folder are not touched.`}
                confirmLabel="Install and restart"
                busy={applying}
                onConfirm={apply}
                onCancel={() => setConfirmApply(false)}
            />
        </div>
    );
}
