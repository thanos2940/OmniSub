import { useState } from 'react';
import { Film, Plus, Tv, Trash2, Play, RefreshCw, Webhook as WebhookIcon, PlayCircle } from 'lucide-react';
import { api } from '../../api';
import { useToast } from '../../context/ToastContext';
import { Button, Card, Field, IconButton, INPUT, StatusChip, Toggle } from '../ui';
import {
    CopyButton, Disclosure, Divider, errorText, FieldGrid, Notice, NumberInput, SECRET_SENTINEL, SecretInput, TestResult, cx,
} from './parts';

const secretArg = (value) => (value && value !== SECRET_SENTINEL ? value : undefined);

// ---------------------------------------------------------------- Sonarr / Radarr

const ARR = {
    sonarr: {
        label: 'Sonarr', subtitle: 'TV series', icon: Tv, placeholder: 'http://localhost:8989',
        test: (s) => api.testSonarr({ url: s.sonarr_url, api_key: secretArg(s.sonarr_api_key) }),
        sync: () => api.syncSonarr(),
        synced: (d) => `${d?.shows_synced || 0} shows updated`,
    },
    radarr: {
        label: 'Radarr', subtitle: 'Movies', icon: Film, placeholder: 'http://localhost:7878',
        test: (s) => api.testRadarr({ url: s.radarr_url, api_key: secretArg(s.radarr_api_key) }),
        sync: () => api.syncRadarr(),
        synced: (d) => `${d?.movies_synced || 0} movies updated`,
    },
};

function ArrCard({ kind, settings, onChange }) {
    const cfg = ARR[kind];
    const toast = useToast();
    const [testing, setTesting] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [result, setResult] = useState(null);
    const Icon = cfg.icon;
    const set = (suffix, value) => onChange(`${kind}_${suffix}`, value);

    const test = async () => {
        setTesting(true);
        setResult(null);
        try {
            const res = await cfg.test(settings);
            setResult(res.data?.connected
                ? { ok: true, text: `Connected to ${cfg.label}${res.data.version ? ` ${res.data.version}` : ''}.` }
                : { ok: false, text: res.data?.error || 'Could not connect.' });
        } catch (err) {
            setResult({ ok: false, text: errorText(err, 'The test failed') });
        } finally {
            setTesting(false);
        }
    };

    const sync = async () => {
        setSyncing(true);
        try {
            const res = await cfg.sync();
            toast.success(`${cfg.label} sync finished: ${cfg.synced(res.data)}.`);
        } catch (err) {
            toast.error(`${cfg.label} sync failed: ${errorText(err)}`);
        } finally {
            setSyncing(false);
        }
    };

    return (
        <Card
            title={<span className="inline-flex items-center gap-2"><Icon size={16} className="text-gray-400" />{cfg.label}</span>}
            subtitle={cfg.subtitle}
            actions={<StatusChip tone={settings[`${kind}_enabled`] ? 'success' : 'neutral'}>{settings[`${kind}_enabled`] ? 'Enabled' : 'Off'}</StatusChip>}
        >
            <div className="space-y-4">
                <Toggle label={`Use ${cfg.label}`} description="Reads the library from it and follows its imports." checked={!!settings[`${kind}_enabled`]} onChange={(v) => set('enabled', v)} />
                <FieldGrid>
                    <Field label="URL" htmlFor={`${kind}-url`}>
                        <input id={`${kind}-url`} className={INPUT} value={settings[`${kind}_url`] || ''} onChange={(e) => set('url', e.target.value)} placeholder={cfg.placeholder} />
                    </Field>
                    <Field label="API key" htmlFor={`${kind}-key`} hint={`Found in ${cfg.label} under Settings > General.`}>
                        <SecretInput id={`${kind}-key`} value={settings[`${kind}_api_key`]} isSet={settings.secrets_set?.[`${kind}_api_key`]} onChange={(v) => set('api_key', v)} placeholder="API key" />
                    </Field>
                </FieldGrid>
                <div className="flex flex-wrap gap-2">
                    <Button icon={PlayCircle} onClick={test} loading={testing}>Test</Button>
                    <Button icon={RefreshCw} onClick={sync} loading={syncing}>Sync now</Button>
                </div>
                <TestResult result={result} />
            </div>
        </Card>
    );
}

function LibrarySyncCard({ settings, onChange }) {
    const toast = useToast();
    const [syncing, setSyncing] = useState(false);
    const syncAll = async () => {
        setSyncing(true);
        try {
            const res = await api.syncArrAll();
            toast.success(`Library sync finished: ${res.data?.total_synced || 0} items checked.`);
        } catch (err) {
            toast.error(`Library sync failed: ${errorText(err)}`);
        } finally {
            setSyncing(false);
        }
    };
    return (
        <Card title="Library sync" subtitle="Keeps the library in step with Sonarr and Radarr. It never queues translations by itself.">
            <div className="flex flex-wrap items-end justify-between gap-4">
                <Field label="Sync every" htmlFor="arr-interval" hint="Minutes. 0 syncs only when you ask, and on webhooks.">
                    <div className="flex items-center gap-2">
                        <NumberInput id="arr-interval" min={0} max={10080} value={settings.arr_sync_interval ?? 0} onChange={(v) => onChange('arr_sync_interval', v)} />
                        <span className="text-sm text-gray-500 dark:text-gray-400">minutes</span>
                    </div>
                </Field>
                <Button icon={RefreshCw} onClick={syncAll} loading={syncing}>Sync everything now</Button>
            </div>
        </Card>
    );
}

// ---------------------------------------------------------------- Plex

function PlexCard({ settings, onChange }) {
    const [testing, setTesting] = useState(false);
    const [result, setResult] = useState(null);
    const [libraries, setLibraries] = useState([]);

    const test = async () => {
        setTesting(true);
        setResult(null);
        setLibraries([]);
        try {
            const res = await api.testPlexConnection({ url: settings.plex_url, token: secretArg(settings.plex_token) });
            const d = res.data || {};
            if (d.connected) {
                setResult({ ok: true, text: `Connected to ${d.name || 'Plex'}${d.version ? `, version ${d.version}` : ''}.` });
                setLibraries(d.sections || []);
            } else {
                setResult({ ok: false, text: d.error || 'Could not connect.' });
            }
        } catch (err) {
            setResult({ ok: false, text: errorText(err, 'The test failed') });
        } finally {
            setTesting(false);
        }
    };

    return (
        <Card title="Plex" subtitle="Asked to rescan a library after every subtitle OmbiSub writes, so it shows up without a manual refresh.">
            <div className="space-y-4">
                <Toggle label="Refresh Plex after a subtitle is written" checked={!!settings.plex_refresh_enabled} onChange={(v) => onChange('plex_refresh_enabled', v)} />
                <FieldGrid>
                    <Field label="URL" htmlFor="plex-url">
                        <input id="plex-url" className={INPUT} value={settings.plex_url || ''} onChange={(e) => onChange('plex_url', e.target.value)} placeholder="http://localhost:32400" />
                    </Field>
                    <Field label="Token" htmlFor="plex-token" hint="The X-Plex-Token of your account.">
                        <SecretInput id="plex-token" value={settings.plex_token} isSet={settings.secrets_set?.plex_token} onChange={(v) => onChange('plex_token', v)} placeholder="X-Plex-Token" />
                    </Field>
                </FieldGrid>
                <Button icon={PlayCircle} onClick={test} loading={testing}>Test</Button>
                <TestResult result={result} />
                {libraries.length > 0 && (
                    <div>
                        <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">Libraries</div>
                        <ul className="divide-y divide-gray-100 rounded-lg border border-gray-200 text-sm dark:divide-gray-750 dark:border-gray-750">
                            {libraries.map(s => (
                                <li key={s.key} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 px-3 py-2">
                                    <span className="font-medium text-gray-900 dark:text-white">{s.title} <span className="font-normal text-gray-500 dark:text-gray-400">({s.type})</span></span>
                                    <span className="min-w-0 break-all font-mono text-xs text-gray-500 dark:text-gray-400">{(s.locations || []).join(', ')}</span>
                                </li>
                            ))}
                        </ul>
                    </div>
                )}
            </div>
        </Card>
    );
}

// ---------------------------------------------------------------- path mappings

function PathMappingsCard({ settings, onChange }) {
    const mappings = settings.arr_path_mappings || [];
    const [remote, setRemote] = useState('');
    const [testing, setTesting] = useState(false);
    const [result, setResult] = useState(null);
    const set = (next) => onChange('arr_path_mappings', next);

    const test = async () => {
        setTesting(true);
        setResult(null);
        try {
            const res = await api.testPath(remote.trim(), mappings);
            const d = res.data || {};
            setResult(d.exists
                ? { ok: true, text: `${d.local_path || remote} is reachable.` }
                : { ok: false, text: `${d.local_path || remote} was not found on this machine.` });
        } catch (err) {
            setResult({ ok: false, text: errorText(err, 'Could not resolve the path') });
        } finally {
            setTesting(false);
        }
    };

    return (
        <Card
            title="Path mappings"
            subtitle="For when Sonarr or Radarr see the media under another path than OmbiSub does (Docker, a network share)."
            actions={<Button size="sm" icon={Plus} onClick={() => set([...mappings, { remote: '', local: '' }])}>Add mapping</Button>}
        >
            <div className="space-y-4">
                {mappings.length === 0 ? (
                    <p className="text-sm text-gray-500 dark:text-gray-400">No mappings. Paths are used as Sonarr and Radarr report them.</p>
                ) : (
                    <div className="space-y-3">
                        {mappings.map((m, i) => (
                            <div key={i} className="grid grid-cols-[1fr_auto] items-center gap-2 md:grid-cols-[1fr_1fr_auto]">
                                <input
                                    aria-label="Path as Sonarr or Radarr see it"
                                    className={cx(INPUT, 'font-mono text-xs')}
                                    value={m.remote || ''}
                                    placeholder="As the arr sees it: /media/tv"
                                    onChange={(e) => set(mappings.map((x, j) => (j === i ? { ...x, remote: e.target.value } : x)))}
                                />
                                <IconButton icon={Trash2} label="Remove mapping" className="md:order-3" onClick={() => set(mappings.filter((_, j) => j !== i))} />
                                <input
                                    aria-label="Path on this machine"
                                    className={cx(INPUT, 'col-span-2 font-mono text-xs md:col-span-1')}
                                    value={m.local || ''}
                                    placeholder="On this machine: D:\Media\TV"
                                    onChange={(e) => set(mappings.map((x, j) => (j === i ? { ...x, local: e.target.value } : x)))}
                                />
                            </div>
                        ))}
                    </div>
                )}
                <Divider />
                <Field label="Try a path" htmlFor="path-test" hint="Uses the mappings above, including ones you have not saved yet.">
                    <div className="flex flex-col gap-2 sm:flex-row">
                        <input id="path-test" className={cx(INPUT, 'font-mono text-xs sm:flex-1')} value={remote} onChange={(e) => setRemote(e.target.value)} placeholder="A path as Sonarr or Radarr report it" />
                        <Button icon={Play} onClick={test} loading={testing} disabled={!remote.trim()}>Test</Button>
                    </div>
                </Field>
                <TestResult result={result} />
            </div>
        </Card>
    );
}

// ---------------------------------------------------------------- webhooks

const randomToken = () => {
    const bytes = new Uint8Array(24);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
};

// Where Sonarr and Radarr can reach this server: this page's own origin plus the URL base, as the
// browser sees it (in `npm run dev` the API is on :8000, not the dev server).
const webhookOrigin = () => {
    const origin = import.meta.env.DEV ? window.location.origin.replace(/:\d+$/, ':8000') : window.location.origin;
    return origin + (window.__OMBISUB_BASE__ || '');
};

function WebhooksCard({ settings, onChange }) {
    const toast = useToast();
    const secret = settings.webhook_secret;
    const known = secret && secret !== SECRET_SENTINEL;
    const stored = !!settings.secrets_set?.webhook_secret;
    const token = known ? secret : 'YOUR_TOKEN';
    const hooks = [
        { name: 'Sonarr', path: '/api/webhook/sonarr' },
        { name: 'Radarr', path: '/api/webhook/radarr' },
    ];
    return (
        <Card title="Webhooks" subtitle="Lets Sonarr and Radarr tell OmbiSub about an import at once, instead of waiting for the next check.">
            <div className="space-y-4">
                <ol className="list-decimal space-y-1 pl-5 text-sm text-gray-600 dark:text-gray-300">
                    <li>In Sonarr or Radarr open Settings &gt; Connect &gt; Add &gt; Webhook.</li>
                    <li>Paste the URL below, keep the method POST, and tick On Import, On Upgrade and On Rename.</li>
                    <li>Use the Test button there. A reply of success means the token matches.</li>
                </ol>
                <div className="space-y-2">
                    {hooks.map(h => {
                        const url = `${webhookOrigin()}${h.path}?token=${token}`;
                        return (
                            <div key={h.name} className="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-750 dark:bg-gray-900">
                                <span className="w-14 shrink-0 text-xs font-semibold text-gray-600 dark:text-gray-300">{h.name}</span>
                                <code className="min-w-0 flex-1 break-all font-mono text-xs text-gray-800 dark:text-gray-200">{url}</code>
                                <CopyButton text={url} onCopied={() => !known && toast.info('Replace YOUR_TOKEN with the webhook token before you paste it.')} />
                            </div>
                        );
                    })}
                </div>
                <Field
                    label="Webhook token"
                    htmlFor="webhook-secret"
                    hint={known
                        ? 'New token, not saved yet. Save, then update the URL in Sonarr and Radarr.'
                        : stored
                            ? 'A token is stored on the server and is not shown here. Put it in place of YOUR_TOKEN, or generate a new one and update Sonarr and Radarr.'
                            : 'No token is set, so webhooks are refused. Generate one.'}
                >
                    <div className="flex flex-col gap-2 sm:flex-row">
                        <div className="sm:flex-1">
                            <SecretInput id="webhook-secret" value={secret} isSet={stored} onChange={(v) => onChange('webhook_secret', v)} placeholder="Token" />
                        </div>
                        <Button icon={WebhookIcon} onClick={() => onChange('webhook_secret', randomToken())}>Generate new</Button>
                    </div>
                </Field>
            </div>
        </Card>
    );
}

// ---------------------------------------------------------------- media probe

function MediaProbe() {
    const [path, setPath] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const [result, setResult] = useState(null);
    const run = async () => {
        setBusy(true);
        setError(null);
        setResult(null);
        try {
            const res = await api.testMediaProbe(path.trim());
            setResult(res.data);
        } catch (err) {
            setError(errorText(err, 'The probe failed'));
        } finally {
            setBusy(false);
        }
    };
    return (
        <Disclosure title="Check a video file" subtitle="Lists the subtitle tracks ffprobe finds inside a video, to see what extraction would work with.">
            <div className="flex flex-col gap-2 sm:flex-row">
                <input aria-label="Path to a video file" className={cx(INPUT, 'font-mono text-xs sm:flex-1')} value={path} onChange={(e) => setPath(e.target.value)} placeholder="D:\Anime\Show\Episode01.mkv" />
                <Button icon={Play} onClick={run} loading={busy} disabled={!path.trim()}>Probe</Button>
            </div>
            {error && <Notice tone="danger">{error}</Notice>}
            {result && (
                <div className="space-y-2">
                    <p className="text-sm font-medium text-gray-900 dark:text-white">{(result.tracks || []).length} subtitle track(s)</p>
                    <ul className="divide-y divide-gray-100 rounded-lg border border-gray-200 text-sm dark:divide-gray-750 dark:border-gray-750">
                        {(result.tracks || []).map((t, i) => (
                            <li key={i} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                                <span className="min-w-0">
                                    <span className="font-medium text-gray-900 dark:text-white">#{t.index ?? i}</span>{' '}
                                    <span className="text-gray-600 dark:text-gray-300">{t.codec_name || t.format} · {t.language || 'und'}</span>
                                    {t.title && <span className="text-gray-500 dark:text-gray-400"> · {t.title}</span>}
                                </span>
                                <StatusChip tone={t.is_ass ? 'accent' : 'neutral'}>{t.is_ass ? 'ASS' : 'Text'}</StatusChip>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </Disclosure>
    );
}

export default function ConnectionsSection({ settings, onChange }) {
    return (
        <div className="space-y-5">
            <ArrCard kind="sonarr" settings={settings} onChange={onChange} />
            <ArrCard kind="radarr" settings={settings} onChange={onChange} />
            <LibrarySyncCard settings={settings} onChange={onChange} />
            <PlexCard settings={settings} onChange={onChange} />
            <PathMappingsCard settings={settings} onChange={onChange} />
            <WebhooksCard settings={settings} onChange={onChange} />
            <MediaProbe />
        </div>
    );
}
