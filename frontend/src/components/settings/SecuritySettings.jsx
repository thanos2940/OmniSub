import { useState, useEffect, useCallback } from 'react';
import { ShieldAlert, ShieldCheck } from 'lucide-react';
import { api, AUTH_STORAGE_KEY } from '../../api';
import { useToast } from '../../context/ToastContext';
import { Button, Card, Field, INPUT, StatusChip, Toggle } from '../ui';
import { CopyButton, errorText, FieldGrid, Notice } from './parts';

function readStoredKey() {
    try { return localStorage.getItem(AUTH_STORAGE_KEY) || ''; } catch { return ''; }
}

// Login and the API key other tools (MorpiceHub, scripts) send as X-Api-Key. Credentials are changed
// with their own button: they are not part of the Save bar.
export default function SecuritySettings() {
    const toast = useToast();
    const [status, setStatus] = useState(null);
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [confirm, setConfirm] = useState('');
    const [regenerate, setRegenerate] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);
    const [apiKey, setApiKey] = useState(readStoredKey);
    const [shown, setShown] = useState(false);

    const load = useCallback(async () => {
        try {
            const res = await api.getAuthStatus();
            setStatus(res.data || { auth_enabled: false });
        } catch {
            setStatus({ auth_enabled: false });
        }
        setApiKey(readStoredKey());
    }, []);

    useEffect(() => { load(); }, [load]);

    const submit = async (e) => {
        e.preventDefault();
        setError(null);
        if (!username.trim()) return setError('Enter a username.');
        if (!password) return setError('Enter a password.');
        if (password !== confirm) return setError('The two passwords do not match.');
        setSaving(true);
        try {
            const res = await api.setCredentials(username.trim(), password, regenerate);
            if (res.data?.api_key) {
                try { localStorage.setItem(AUTH_STORAGE_KEY, res.data.api_key); } catch { /* private mode */ }
            }
            toast.success('Login updated');
            setPassword('');
            setConfirm('');
            setRegenerate(false);
            await load();
        } catch (err) {
            setError(errorText(err, 'Could not update the login'));
        } finally {
            setSaving(false);
        }
    };

    const secured = !!status?.auth_enabled;
    return (
        <div className="space-y-5">
            {status && (
                <Notice tone={secured ? 'success' : 'warning'}>
                    <p className="flex items-center gap-1.5 font-medium">
                        {secured ? <ShieldCheck size={14} /> : <ShieldAlert size={14} />}
                        {secured ? 'A login is required' : 'No login is set'}
                    </p>
                    <p>
                        {secured
                            ? 'Every page and API call needs the login or the API key.'
                            : 'Anyone who can reach this address can use OmbiSub. Set a login below, especially before exposing it through a reverse proxy.'}
                    </p>
                </Notice>
            )}

            <Card title="Login" subtitle="One administrator account." actions={status && <StatusChip tone={secured ? 'success' : 'warning'}>{secured ? 'Secured' : 'Open'}</StatusChip>}>
                <form onSubmit={submit} className="space-y-4" noValidate>
                    <Field label="Username" htmlFor="sec-user">
                        <input id="sec-user" className={INPUT} autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="admin" />
                    </Field>
                    <FieldGrid>
                        <Field label="New password" htmlFor="sec-pass">
                            <input id="sec-pass" type="password" className={INPUT} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
                        </Field>
                        <Field label="Confirm password" htmlFor="sec-confirm">
                            <input id="sec-confirm" type="password" className={INPUT} autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
                        </Field>
                    </FieldGrid>
                    <Toggle label="Generate a new API key" description="Tools using the old key, such as MorpiceHub, stop working until you give them the new one." checked={regenerate} onChange={setRegenerate} />
                    {error && <Notice tone="danger">{error}</Notice>}
                    <div className="flex justify-end">
                        <Button type="submit" variant="primary" loading={saving} disabled={!username.trim() || !password}>{secured ? 'Update login' : 'Set login'}</Button>
                    </div>
                </form>
            </Card>

            {apiKey && (
                <Card title="API key" subtitle="Send it as the X-Api-Key header. MorpiceHub and scripts use it to call OmbiSub.">
                    <div className="space-y-2">
                        <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-750 dark:bg-gray-900">
                            <code className="min-w-0 flex-1 truncate font-mono text-xs text-gray-800 dark:text-gray-200">{shown ? apiKey : '•'.repeat(32)}</code>
                            <button type="button" onClick={() => setShown(s => !s)} className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800">{shown ? 'Hide' : 'Show'}</button>
                            <CopyButton text={apiKey} />
                        </div>
                        <p className="text-xs text-gray-500 dark:text-gray-400">This is the key this browser signed in with.</p>
                    </div>
                </Card>
            )}
        </div>
    );
}
