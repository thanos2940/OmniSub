import { Link } from 'react-router-dom';
import { Card, Field, INPUT } from '../ui';
import { cx, FieldGrid, Notice, NumberInput } from './parts';

const SEGMENT = /^[A-Za-z0-9._~-]+$/;

// Same rule as the server's normalize_url_base: "" or path segments of letters, digits and . _ ~ -
export function urlBaseError(value) {
    const segments = String(value || '').trim().split('/').filter(Boolean);
    const bad = segments.some(s => s === '.' || s === '..' || !SEGMENT.test(s));
    return bad ? 'Use letters, digits, ".", "_", "~" and "-" in each part, like /ombisub.' : null;
}

export function validateGeneral(settings) {
    return urlBaseError(settings.url_base);
}

export default function GeneralSection({ settings, onChange, status }) {
    const baseError = urlBaseError(settings.url_base);
    const port = status?.port || window.location.port || (window.location.protocol === 'https:' ? '443' : '80');
    const restartPending = status && status.url_base !== status.url_base_active;
    return (
        <div className="space-y-5">
            <Card title="Languages" subtitle="Defaults for new projects. Each project can override them.">
                <FieldGrid>
                    <Field label="Translate into" htmlFor="target-lang" hint="Used when projects are created from Sonarr and Radarr.">
                        <input id="target-lang" className={INPUT} value={settings.default_target_language || ''} onChange={(e) => onChange('default_target_language', e.target.value)} placeholder="Greek" />
                    </Field>
                    <Field label="Source language code" htmlFor="source-lang" hint="Language of the subtitles to translate from, as a code: en, ja.">
                        <input id="source-lang" className={cx(INPUT, 'font-mono sm:w-32')} value={settings.arr_source_language || ''} onChange={(e) => onChange('arr_source_language', e.target.value)} placeholder="en" />
                    </Field>
                </FieldGrid>
            </Card>

            <Card title="Requests from other tools" subtitle="MorpiceHub asks for subtitles of specific titles.">
                <Field label="Most episodes in one request" htmlFor="on-demand-max" hint="Each episode is a full translation, so this bounds what one click can queue.">
                    <NumberInput id="on-demand-max" min={1} max={500} value={settings.on_demand_max_episodes ?? 30} onChange={(v) => onChange('on_demand_max_episodes', v)} />
                </Field>
            </Card>

            <Card title="Network" subtitle="For running behind a reverse proxy such as Caddy.">
                <div className="space-y-4">
                    <FieldGrid>
                        <Field label="Port" htmlFor="net-port" hint="Set when OmbiSub is installed. Re-run the installer to change it.">
                            <input id="net-port" className={cx(INPUT, 'sm:w-32')} value={port} readOnly />
                        </Field>
                        <Field label="URL base" htmlFor="net-base" error={baseError} hint="The path the proxy serves OmbiSub under, like /ombisub. Empty for the root.">
                            <input id="net-base" className={cx(INPUT, 'font-mono')} value={settings.url_base || ''} onChange={(e) => onChange('url_base', e.target.value)} placeholder="/ombisub" aria-invalid={!!baseError} />
                        </Field>
                    </FieldGrid>
                    <Field label="Trusted proxies" htmlFor="net-proxies" hint="Addresses whose X-Forwarded-For and X-Forwarded-Proto headers are believed, comma separated. 127.0.0.1 suits Caddy on the same machine.">
                        <input id="net-proxies" className={cx(INPUT, 'font-mono')} value={settings.trusted_proxies ?? ''} onChange={(e) => onChange('trusted_proxies', e.target.value)} placeholder="127.0.0.1" />
                    </Field>
                    {restartPending ? (
                        <Notice tone="warning">
                            The URL base {status.url_base ? <code className="font-mono">{status.url_base}</code> : 'root'} is saved but the server still runs
                            with {status.url_base_active ? <code className="font-mono">{status.url_base_active}</code> : 'root'}. <Link to="/system" className="font-medium underline">Restart it on the System page</Link> to apply.
                        </Notice>
                    ) : (
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                            URL base and trusted proxies take effect after a restart. Save first, then restart on the <Link to="/system" className="font-medium text-indigo-600 hover:underline dark:text-indigo-400">System</Link> page.
                        </p>
                    )}
                </div>
            </Card>
        </div>
    );
}
