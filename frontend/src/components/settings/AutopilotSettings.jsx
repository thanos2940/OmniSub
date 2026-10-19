import { Link } from 'react-router-dom';
import { Card, Field, INPUT, StatusChip, Toggle } from '../ui';
import { FieldGrid, Notice, NumberInput, SecretInput } from './parts';

const fmtEnabledAt = (iso) => {
    if (!iso) return '';
    const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
};

// The Autopilot: the only automation. It watches Sonarr and Radarr and translates what they import.
export default function AutopilotSettings({ settings, onChange }) {
    const get = (key, fallback) => (settings[key] === undefined || settings[key] === null ? fallback : settings[key]);
    const enabled = !!get('autopilot_enabled', false);
    const backlog = get('autopilot_backlog_mode', 'off');
    return (
        <div className="space-y-5">
            <Card
                title="Autopilot"
                subtitle="Watches Sonarr and Radarr and translates new imports that have no subtitle in your language."
                actions={<StatusChip tone={enabled ? 'success' : 'neutral'}>{enabled ? 'On' : 'Off'}</StatusChip>}
            >
                <div className="space-y-4">
                    <Toggle
                        label="Run the Autopilot"
                        description="Without it, titles are translated only when you ask or when a webhook refreshes them."
                        checked={enabled}
                        onChange={(v) => onChange('autopilot_enabled', v)}
                    />
                    {settings.autopilot_enabled_at && (
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                            Switched on {fmtEnabledAt(settings.autopilot_enabled_at)}. Files imported before that are handled only when the backlog option below is on.
                        </p>
                    )}
                    <Notice tone="info">
                        <p className="font-medium">What it does with a new file</p>
                        <ol className="list-decimal space-y-0.5 pl-5">
                            <li>Waits a few minutes, so another tool or a person can add a subtitle first.</li>
                            <li>Leaves the video alone if it already has Greek, as a file next to it or an embedded track.</li>
                            <li>Otherwise takes the embedded English subtitle (ASS first, then SRT) or an English file next to the video and queues it for translation.</li>
                            <li>Writes <code className="font-mono">.el.srt</code> next to the video, and <code className="font-mono">.el.ass</code> for an ASS source.</li>
                        </ol>
                        <p>Progress is on the <Link to="/activity" className="font-medium underline">Activity</Link> page.</p>
                    </Notice>
                </div>
            </Card>

            <Card title="Timing and backlog">
                <div className="space-y-4">
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                        <Field label="Wait before acting" htmlFor="ap-grace" hint="Time for another tool to supply a subtitle first.">
                            <div className="flex items-center gap-2"><NumberInput id="ap-grace" min={0} max={1440} value={get('autopilot_grace_minutes', 5)} onChange={(v) => onChange('autopilot_grace_minutes', v)} /><span className="text-sm text-gray-500 dark:text-gray-400">minutes</span></div>
                        </Field>
                        <Field label="Check Sonarr and Radarr every" htmlFor="ap-poll" hint="How often the import history is read.">
                            <div className="flex items-center gap-2"><NumberInput id="ap-poll" min={10} max={3600} value={get('autopilot_poll_seconds', 60)} onChange={(v) => onChange('autopilot_poll_seconds', v)} /><span className="text-sm text-gray-500 dark:text-gray-400">seconds</span></div>
                        </Field>
                        <Field label="Reconcile the library every" htmlFor="ap-reconcile" hint="Catches files missed while OmbiSub was down.">
                            <div className="flex items-center gap-2"><NumberInput id="ap-reconcile" min={1} max={168} value={get('autopilot_reconcile_hours', 6)} onChange={(v) => onChange('autopilot_reconcile_hours', v)} /><span className="text-sm text-gray-500 dark:text-gray-400">hours</span></div>
                        </Field>
                    </div>
                    <FieldGrid>
                        <Field label="Older files without a subtitle" htmlFor="ap-backlog">
                            <select id="ap-backlog" className={INPUT} value={backlog} onChange={(e) => onChange('autopilot_backlog_mode', e.target.value)}>
                                <option value="off">Leave them (new files only)</option>
                                <option value="newest">Add them, newest first</option>
                                <option value="oldest">Add them, oldest first</option>
                            </select>
                        </Field>
                        <Field label="Backlog files per day" htmlFor="ap-daily" hint={backlog === 'off' ? 'Used only when the backlog option is on.' : 'At most this many older files are added each day.'}>
                            <NumberInput id="ap-daily" min={0} max={1000} value={get('autopilot_backlog_daily_limit', 20)} onChange={(v) => onChange('autopilot_backlog_daily_limit', v)} />
                        </Field>
                    </FieldGrid>
                </div>
            </Card>

            <Card title="Rules">
                <div className="space-y-4">
                    <Toggle label="An embedded Greek track counts as Greek" description="Text and image tracks inside the video both count." checked={!!get('autopilot_count_embedded_greek', true)} onChange={(v) => onChange('autopilot_count_embedded_greek', v)} />
                    <Toggle label="Skip media whose original language is Greek" checked={!!get('autopilot_skip_original_greek', true)} onChange={(v) => onChange('autopilot_skip_original_greek', v)} />
                    <Toggle label="Only monitored series, episodes and movies" checked={!!get('autopilot_monitored_only', true)} onChange={(v) => onChange('autopilot_monitored_only', v)} />
                    <Toggle label="Never overwrite a Greek file OmbiSub did not write" description="Protects subtitles from other tools or a person. Recommended." checked={!!get('autopilot_protect_foreign_subs', true)} onChange={(v) => onChange('autopilot_protect_foreign_subs', v)} />
                    <Toggle label="Repair garbled encoding in other tools’ Greek files" description="Fixes the file in place after saving a backup copy." checked={!!get('autopilot_repair_foreign_encoding', true)} onChange={(v) => onChange('autopilot_repair_foreign_encoding', v)} />
                    <Field label="Skip tags" htmlFor="ap-tags" hint="Comma separated Sonarr or Radarr tags. A series or movie with one of them is left alone.">
                        <input id="ap-tags" className={INPUT} value={get('autopilot_skip_tags', 'ombisub-skip,no-greek')} onChange={(e) => onChange('autopilot_skip_tags', e.target.value)} placeholder="ombisub-skip,no-greek" />
                    </Field>
                </div>
            </Card>

            <Card title="Notifications" subtitle="Failures and the daily digest go to Discord when a webhook is set.">
                <div className="space-y-4">
                    <Field label="Discord webhook URL" htmlFor="discord-url">
                        <SecretInput id="discord-url" value={settings.discord_webhook_url} isSet={settings.secrets_set?.discord_webhook_url} onChange={(v) => onChange('discord_webhook_url', v)} placeholder="https://discord.com/api/webhooks/..." />
                    </Field>
                    <Toggle label="Also report every finished episode" checked={!!get('autopilot_notify_success', false)} onChange={(v) => onChange('autopilot_notify_success', v)} />
                    <Field label="Daily digest at" htmlFor="ap-digest" hint="Local time of the server.">
                        <select id="ap-digest" className={`${INPUT} sm:w-40`} value={get('autopilot_digest_hour', 9)} onChange={(e) => onChange('autopilot_digest_hour', parseInt(e.target.value, 10))}>
                            <option value={-1}>Off</option>
                            {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
                        </select>
                    </Field>
                </div>
            </Card>
        </div>
    );
}
