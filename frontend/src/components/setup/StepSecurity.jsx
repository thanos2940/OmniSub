import React, { useState } from 'react';
import { Lock, ShieldAlert, Copy } from 'lucide-react';
import { api, AUTH_STORAGE_KEY } from '../../api';

const StepSecurity = ({ onNext, onBack, onSkip }) => {
    const [username, setUsername] = useState('admin');
    const [password, setPassword] = useState('');
    const [confirm, setConfirm] = useState('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [done, setDone] = useState(false);
    const [webhookSecret, setWebhookSecret] = useState('');

    const handleSecure = async () => {
        setError('');
        if (!username.trim()) { setError('Enter a username.'); return; }
        if (password.length < 8) { setError('Password must be at least 8 characters.'); return; }
        if (password !== confirm) { setError('Passwords do not match.'); return; }

        setSaving(true);
        try {
            const res = await api.setCredentials(username.trim(), password, false);
            localStorage.setItem(AUTH_STORAGE_KEY, res.data.api_key);
            try {
                const health = await api.getFullHealth();
                setWebhookSecret(health.data?.webhooks?.sonarr_url?.split('token=')[1] || '');
            } catch {
                // non-critical — the Health page shows this later regardless
            }
            setDone(true);
        } catch (err) {
            setError(err.response?.data?.detail || 'Failed to set credentials.');
        } finally {
            setSaving(false);
        }
    };

    if (done) {
        return (
            <div className="space-y-5 text-center">
                <div className="mx-auto w-14 h-14 rounded-2xl bg-emerald-500 flex items-center justify-center">
                    <Lock className="text-white" size={26} />
                </div>
                <div>
                    <h2 className="text-lg font-bold text-gray-900 dark:text-white">Server secured</h2>
                    <p className="text-sm text-gray-500 dark:text-gray-400">Sign in with these credentials next time you open Omnisub.</p>
                </div>
                {webhookSecret && (
                    <div className="text-left bg-gray-50 dark:bg-gray-900/40 rounded-lg p-3">
                        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Webhook token</p>
                        <div className="flex items-center gap-2">
                            <code className="flex-1 text-xs font-mono break-all">{webhookSecret}</code>
                            <button onClick={() => navigator.clipboard.writeText(webhookSecret)} className="text-gray-400 hover:text-gray-600"><Copy size={14} /></button>
                        </div>
                        <p className="text-[11px] text-gray-500 mt-1">Included automatically in the webhook URLs on the Health page.</p>
                    </div>
                )}
                <button onClick={onNext} className="w-full px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-medium">Continue</button>
            </div>
        );
    }

    return (
        <div className="space-y-5">
            <div>
                <h2 className="text-lg font-bold text-gray-900 dark:text-white">Secure this server</h2>
                <p className="text-sm text-gray-500 dark:text-gray-400">Set a username and password if anything other than this machine can reach Omnisub.</p>
            </div>

            {!onSkip && (
                <div className="flex items-start gap-2 text-sm text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg p-3">
                    <ShieldAlert className="w-4 h-4 flex-shrink-0 mt-0.5" />
                    <span>Without credentials, anyone who can reach this server can read your settings and control translations.</span>
                </div>
            )}

            <div className="space-y-3">
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">Username</label>
                    <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none text-sm" autoComplete="username" />
                </div>
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">Password</label>
                    <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none text-sm" autoComplete="new-password" />
                </div>
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">Confirm password</label>
                    <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none text-sm" autoComplete="new-password" />
                </div>
            </div>

            {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

            <div className="flex items-center justify-between pt-2">
                <button onClick={onBack} disabled={!onBack} className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 disabled:opacity-0">Back</button>
                <div className="flex gap-3">
                    {onSkip && <button onClick={onSkip} className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">Skip — leave unsecured</button>}
                    <button onClick={handleSecure} disabled={saving} className="px-5 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-sm font-medium disabled:opacity-50">
                        {saving ? 'Securing...' : 'Secure server'}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default StepSecurity;
