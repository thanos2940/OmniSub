import React, { useState, useEffect } from 'react';
import { Lock, ShieldAlert, ShieldCheck, Copy, Eye, EyeOff, Key, Loader2, Save } from 'lucide-react';
import { api, AUTH_STORAGE_KEY } from '../../api';

const SecuritySettings = ({ toast }) => {
    const [authStatus, setAuthStatus] = useState({ auth_enabled: true, authenticated: true });
    const [authUsername, setAuthUsername] = useState('');
    const [authPassword, setAuthPassword] = useState('');
    const [authPasswordConfirm, setAuthPasswordConfirm] = useState('');
    const [savingAuth, setSavingAuth] = useState(false);
    const [authMessage, setAuthMessage] = useState(null);
    const [showApiKey, setShowApiKey] = useState(false);
    const [regenerateKeyOnSave, setRegenerateKeyOnSave] = useState(false);
    const [currentApiKey, setCurrentApiKey] = useState('');

    const loadAuthStatus = async () => {
        try {
            const res = await api.getAuthStatus();
            setAuthStatus(res.data || { auth_enabled: false });
        } catch {
            setAuthStatus({ auth_enabled: false });
        }
        const storedKey = localStorage.getItem(AUTH_STORAGE_KEY) || '';
        setCurrentApiKey(storedKey);
    };

    useEffect(() => {
        loadAuthStatus();
    }, []);

    const handleSaveAuth = async (e) => {
        e.preventDefault();
        if (!authUsername.trim()) {
            setAuthMessage({ type: 'error', text: 'Username is required' });
            return;
        }
        if (!authPassword) {
            setAuthMessage({ type: 'error', text: 'Password is required' });
            return;
        }
        if (authPassword !== authPasswordConfirm) {
            setAuthMessage({ type: 'error', text: 'Passwords do not match' });
            return;
        }

        setSavingAuth(true);
        setAuthMessage(null);
        try {
            const res = await api.setCredentials(authUsername.trim(), authPassword, regenerateKeyOnSave);
            if (res.data?.api_key) {
                localStorage.setItem(AUTH_STORAGE_KEY, res.data.api_key);
                setCurrentApiKey(res.data.api_key);
            }
            setAuthMessage({ type: 'success', text: 'Security credentials updated successfully.' });
            if (toast?.success) toast.success('Security credentials updated!');
            setAuthPassword('');
            setAuthPasswordConfirm('');
            setRegenerateKeyOnSave(false);
            loadAuthStatus();
        } catch (err) {
            const msg = err.response?.data?.detail || err.message || 'Failed to update credentials';
            setAuthMessage({ type: 'error', text: msg });
            if (toast?.error) toast.error(msg);
        } finally {
            setSavingAuth(false);
        }
    };

    const handleCopyApiKey = () => {
        if (!currentApiKey) return;
        navigator.clipboard.writeText(currentApiKey);
        if (toast?.success) toast.success('API Key copied to clipboard');
    };

    return (
        <div className="space-y-6">
            {/* Server Status Banner */}
            <div className={`p-6 rounded-2xl border shadow-sm flex items-start gap-4 ${
                authStatus.auth_enabled
                    ? 'bg-emerald-50/50 dark:bg-emerald-950/20 border-emerald-200 dark:border-emerald-900/50'
                    : 'bg-amber-50/50 dark:bg-amber-950/20 border-amber-200 dark:border-amber-900/50'
            }`}>
                <div className={`p-3 rounded-xl ${
                    authStatus.auth_enabled
                        ? 'bg-emerald-100 dark:bg-emerald-900/50 text-emerald-600 dark:text-emerald-400'
                        : 'bg-amber-100 dark:bg-amber-900/50 text-amber-600 dark:text-amber-400'
                }`}>
                    {authStatus.auth_enabled ? <ShieldCheck size={24} /> : <ShieldAlert size={24} />}
                </div>
                <div>
                    <h3 className="text-base font-bold text-gray-900 dark:text-white">
                        {authStatus.auth_enabled ? 'Server Access Secured' : 'Server is Unsecured'}
                    </h3>
                    <p className="text-xs text-gray-600 dark:text-gray-300 mt-1">
                        {authStatus.auth_enabled
                            ? 'Admin challenge authentication and API key headers are active on all endpoints.'
                            : 'Anyone on your local network or reverse proxy can access OmbiSub without logging in. Set credentials below.'}
                    </p>
                </div>
            </div>

            {/* Credentials Management Form */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-5">
                <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-4">
                    <Lock className="text-indigo-600 dark:text-indigo-400" size={20} />
                    <div>
                        <h3 className="text-base font-bold text-gray-900 dark:text-white">Admin Credentials &amp; API Key</h3>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Manage master administrator login credentials and programmatic API access.</p>
                    </div>
                </div>

                <form onSubmit={handleSaveAuth} className="space-y-4">
                    <div>
                        <label className="block text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1">
                            Administrator Username
                        </label>
                        <input
                            type="text"
                            value={authUsername}
                            onChange={(e) => setAuthUsername(e.target.value)}
                            placeholder="admin"
                            className="w-full px-3.5 py-2 text-sm rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none focus:ring-2 focus:ring-indigo-500"
                        />
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <div>
                            <label className="block text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1">
                                New Password
                            </label>
                            <input
                                type="password"
                                value={authPassword}
                                onChange={(e) => setAuthPassword(e.target.value)}
                                placeholder="••••••••••••"
                                className="w-full px-3.5 py-2 text-sm rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none focus:ring-2 focus:ring-indigo-500"
                            />
                        </div>

                        <div>
                            <label className="block text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1">
                                Confirm Password
                            </label>
                            <input
                                type="password"
                                value={authPasswordConfirm}
                                onChange={(e) => setAuthPasswordConfirm(e.target.value)}
                                placeholder="••••••••••••"
                                className="w-full px-3.5 py-2 text-sm rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none focus:ring-2 focus:ring-indigo-500"
                            />
                        </div>
                    </div>

                    <label className="flex items-center gap-2 cursor-pointer pt-1">
                        <input
                            type="checkbox"
                            checked={regenerateKeyOnSave}
                            onChange={(e) => setRegenerateKeyOnSave(e.target.checked)}
                            className="w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                        />
                        <span className="text-xs text-gray-700 dark:text-gray-300 font-medium">
                            Regenerate server API Key on save (invalidates existing client sessions)
                        </span>
                    </label>

                    {authMessage && (
                        <div className={`p-3 rounded-xl text-xs font-semibold ${
                            authMessage.type === 'success'
                                ? 'bg-emerald-50 dark:bg-emerald-950/30 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-900'
                                : 'bg-rose-50 dark:bg-rose-950/30 text-rose-700 dark:text-rose-300 border border-rose-200 dark:border-rose-900'
                        }`}>
                            {authMessage.text}
                        </div>
                    )}

                    <div className="flex justify-end pt-2">
                        <button
                            type="submit"
                            disabled={savingAuth || !authUsername.trim() || !authPassword}
                            className="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold flex items-center gap-2 transition-colors disabled:opacity-50 shadow-sm"
                        >
                            {savingAuth ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                            <span>Update Credentials</span>
                        </button>
                    </div>
                </form>

                {/* API Key Inspector */}
                {currentApiKey && (
                    <div className="pt-4 border-t border-gray-100 dark:border-gray-700 space-y-2">
                        <div className="flex items-center justify-between">
                            <label className="text-xs font-semibold text-gray-600 dark:text-gray-400 flex items-center gap-1.5">
                                <Key size={14} className="text-indigo-500" />
                                <span>Active Programmatic API Key</span>
                            </label>
                            <div className="flex items-center gap-2">
                                <button
                                    type="button"
                                    onClick={() => setShowApiKey(!showApiKey)}
                                    className="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 flex items-center gap-1"
                                >
                                    {showApiKey ? <EyeOff size={14} /> : <Eye size={14} />}
                                    <span>{showApiKey ? 'Hide' : 'Reveal'}</span>
                                </button>
                                <button
                                    type="button"
                                    onClick={handleCopyApiKey}
                                    className="text-xs text-indigo-600 hover:text-indigo-700 dark:text-indigo-400 font-semibold flex items-center gap-1"
                                >
                                    <Copy size={14} />
                                    <span>Copy</span>
                                </button>
                            </div>
                        </div>

                        <div className="p-3 bg-gray-50 dark:bg-gray-900 rounded-xl font-mono text-xs text-gray-800 dark:text-gray-200 border border-gray-200 dark:border-gray-700 truncate">
                            {showApiKey ? currentApiKey : '••••••••••••••••••••••••••••••••••••••••'}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};

export default SecuritySettings;
