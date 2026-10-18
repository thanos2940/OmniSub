import { Plane, Info } from 'lucide-react';

const SECRET_SENTINEL = '__SECRET_UNCHANGED__';

const card = 'bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-4';
const input = 'px-3 py-1.5 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white text-sm';

const Header = ({ icon: Icon = Plane, title }) => (
    <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-4">
        <Icon className="text-indigo-600 dark:text-indigo-400" size={20} />
        <h3 className="text-base font-bold text-gray-900 dark:text-white">{title}</h3>
    </div>
);

const Check = ({ label, hint, checked, onChange }) => (
    <label className="flex items-start gap-3 cursor-pointer">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="w-4 h-4 mt-0.5 text-indigo-600 rounded" />
        <span>
            <span className="text-sm font-medium text-gray-800 dark:text-gray-200">{label}</span>
            {hint && <span className="block text-xs text-gray-500 dark:text-gray-400">{hint}</span>}
        </span>
    </label>
);

const NumberField = ({ label, hint, value, min, max, unit, onChange }) => (
    <div>
        <label className="block text-sm font-medium text-gray-800 dark:text-gray-200 mb-1">{label}</label>
        <div className="flex items-center gap-2">
            <input
                type="number" min={min} max={max} value={value}
                onChange={(e) => { const n = parseInt(e.target.value, 10); onChange(Number.isNaN(n) ? min : n); }}
                className={`${input} w-24`}
            />
            {unit && <span className="text-sm text-gray-500 dark:text-gray-400">{unit}</span>}
        </div>
        {hint && <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">{hint}</p>}
    </div>
);

const fmtEnabledAt = (iso) => {
    if (!iso) return '';
    const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
};

// Settings for the unattended Autopilot (docs/PLAN_autopilot.md 4.11).
const AutopilotSettings = ({ settings, onChange }) => {
    const get = (key, fallback) => (settings[key] === undefined || settings[key] === null ? fallback : settings[key]);
    const backlog = get('autopilot_backlog_mode', 'off');
    const digestHour = get('autopilot_digest_hour', 9);
    const plexEnabled = !!get('plex_refresh_enabled', false);
    const token = get('plex_token', '');

    return (
        <div className="space-y-6">
            <div className={card}>
                <Header title="Autopilot" />
                <Check
                    label="Autopilot enabled"
                    hint="Follow Sonarr and Radarr and make Greek subtitles for new files that have none."
                    checked={!!get('autopilot_enabled', false)}
                    onChange={(v) => onChange('autopilot_enabled', v)}
                />
                {settings.autopilot_enabled_at && (
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                        Switched on {fmtEnabledAt(settings.autopilot_enabled_at)}. Files imported before this are only handled when the backlog option below is on.
                    </p>
                )}

                <div className="rounded-xl bg-indigo-50/60 dark:bg-indigo-950/20 border border-indigo-100 dark:border-indigo-900/40 p-4 text-xs text-gray-700 dark:text-gray-300 space-y-1.5">
                    <div className="flex items-center gap-1.5 font-bold text-indigo-700 dark:text-indigo-300"><Info size={14} /> How it works</div>
                    <ol className="list-decimal ml-5 space-y-1">
                        <li>When Sonarr or Radarr imports a file, Autopilot waits (the 5-minute rule below) so Bazarr can find a Greek subtitle first.</li>
                        <li>If the video already has Greek (a sidecar file or an embedded track), Autopilot leaves it alone.</li>
                        <li>Otherwise it takes the embedded English subtitle (ASS first, then SRT) or an English file next to the video, and translates it.</li>
                        <li>An ASS source gives <code>.el.ass</code> and <code>.el.srt</code> next to the video; an SRT source gives <code>.el.srt</code>.</li>
                        <li>Greek files written by someone else are never overwritten.</li>
                    </ol>
                </div>
            </div>

            <div className={card}>
                <Header title="Timing and backlog" />
                <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
                    <NumberField label="Wait before acting" min={0} max={1440} unit="minutes" value={get('autopilot_grace_minutes', 5)}
                        hint="Bazarr's window to find a Greek subtitle (the 5-minute rule)." onChange={(v) => onChange('autopilot_grace_minutes', v)} />
                    <NumberField label="Check Sonarr/Radarr every" min={10} max={3600} unit="seconds" value={get('autopilot_poll_seconds', 60)}
                        hint="How often the import history is read." onChange={(v) => onChange('autopilot_poll_seconds', v)} />
                    <NumberField label="Reconcile the library every" min={1} max={168} unit="hours" value={get('autopilot_reconcile_hours', 6)}
                        hint="Catches files missed while OmbiSub was down." onChange={(v) => onChange('autopilot_reconcile_hours', v)} />
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
                    <div>
                        <label className="block text-sm font-medium text-gray-800 dark:text-gray-200 mb-1">Backlog (older files without Greek)</label>
                        <select value={backlog} onChange={(e) => onChange('autopilot_backlog_mode', e.target.value)} className={`${input} w-full`}>
                            <option value="off">Off (only new files)</option>
                            <option value="newest">Newest first</option>
                            <option value="oldest">Oldest first</option>
                        </select>
                    </div>
                    <NumberField label="Backlog items per day" min={0} max={1000} value={get('autopilot_backlog_daily_limit', 20)}
                        hint={backlog === 'off' ? 'Only used when the backlog option is on.' : 'At most this many older files are added each day.'}
                        onChange={(v) => onChange('autopilot_backlog_daily_limit', v)} />
                </div>
            </div>

            <div className={card}>
                <Header title="Rules" />
                <Check label="An embedded Greek track counts as Greek present" hint="Text and image tracks inside the video both count (Plex can show both)."
                    checked={!!get('autopilot_count_embedded_greek', true)} onChange={(v) => onChange('autopilot_count_embedded_greek', v)} />
                <Check label="Skip media whose original language is Greek" checked={!!get('autopilot_skip_original_greek', true)}
                    onChange={(v) => onChange('autopilot_skip_original_greek', v)} />
                <Check label="Only monitored series, episodes and movies" checked={!!get('autopilot_monitored_only', true)}
                    onChange={(v) => onChange('autopilot_monitored_only', v)} />
                <Check label="Never overwrite a Greek file OmbiSub did not write" hint="Protects subtitles from Bazarr or a person. Strongly recommended."
                    checked={!!get('autopilot_protect_foreign_subs', true)} onChange={(v) => onChange('autopilot_protect_foreign_subs', v)} />
                <Check label="Repair garbled encoding of other tools' Greek files" hint="Fixes the file in place, after saving a backup copy."
                    checked={!!get('autopilot_repair_foreign_encoding', true)} onChange={(v) => onChange('autopilot_repair_foreign_encoding', v)} />
                <div>
                    <label className="block text-sm font-medium text-gray-800 dark:text-gray-200 mb-1">Skip tags</label>
                    <input
                        type="text" value={get('autopilot_skip_tags', 'ombisub-skip,no-greek')}
                        onChange={(e) => onChange('autopilot_skip_tags', e.target.value)}
                        placeholder="ombisub-skip,no-greek" className={`${input} w-full md:w-96`}
                    />
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">Comma-separated Sonarr/Radarr tags. A series or movie with one of them is left alone.</p>
                </div>
            </div>

            <div className={card}>
                <Header title="Plex and notifications" />
                <Check label="Ask Plex to rescan after writing subtitles" checked={plexEnabled}
                    onChange={(v) => onChange('plex_refresh_enabled', v)} />
                {plexEnabled && (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                        <div>
                            <label className="block text-sm font-medium text-gray-800 dark:text-gray-200 mb-1">Plex URL</label>
                            <input type="text" value={get('plex_url', 'http://localhost:32400')} onChange={(e) => onChange('plex_url', e.target.value)}
                                placeholder="http://localhost:32400" className={`${input} w-full`} />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-800 dark:text-gray-200 mb-1">Plex token</label>
                            <input
                                type="password" autoComplete="off" value={token === SECRET_SENTINEL ? '' : token}
                                placeholder={token === SECRET_SENTINEL ? 'Saved (type to replace)' : 'X-Plex-Token'}
                                onChange={(e) => onChange('plex_token', e.target.value === '' && token === SECRET_SENTINEL ? SECRET_SENTINEL : e.target.value)}
                                className={`${input} w-full`}
                            />
                        </div>
                    </div>
                )}
                <Check label="Tell Discord about every finished episode" hint="Needs a Discord webhook. Failures and the daily digest are sent either way."
                    checked={!!get('autopilot_notify_success', false)} onChange={(v) => onChange('autopilot_notify_success', v)} />
                <div className="flex items-center gap-3 flex-wrap">
                    <label className="text-sm font-medium text-gray-800 dark:text-gray-200">Daily digest at</label>
                    <select
                        value={digestHour} onChange={(e) => onChange('autopilot_digest_hour', parseInt(e.target.value, 10))}
                        className={input}
                    >
                        <option value={-1}>Off</option>
                        {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
                    </select>
                    <span className="text-xs text-gray-500 dark:text-gray-400">local time of the server</span>
                </div>
            </div>
        </div>
    );
};

export default AutopilotSettings;
