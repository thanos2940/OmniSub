import React, { useState, useEffect, useCallback } from 'react';
import {
    RefreshCw, GitCommit, GitBranch, ArrowDownCircle, CheckCircle2,
    AlertCircle, Clock, Key, Copy, Check, Shield, Terminal, ExternalLink, Loader2
} from 'lucide-react';
import { api } from '../../api';
import { useToast } from '../../context/ToastContext';

export default function SystemUpdatesSettings({ settings, updateSetting }) {
    const toast = useToast();
    const [updateStatus, setUpdateStatus] = useState(null);
    const [loading, setLoading] = useState(true);
    const [checking, setChecking] = useState(false);
    const [applying, setApplying] = useState(false);
    const [copiedUrl, setCopiedUrl] = useState(false);
    const [copiedSecret, setCopiedSecret] = useState(false);
    const [confirmModalOpen, setConfirmModalOpen] = useState(false);

    const webhookUrl = `${window.location.origin}/api/webhooks/github`;

    const fetchStatus = useCallback(async (forceCheck = false) => {
        if (forceCheck) setChecking(true);
        try {
            const res = forceCheck ? await api.checkForUpdates() : await api.getUpdateStatus();
            setUpdateStatus(res.data);
            if (forceCheck) {
                if (res.data.update_available) {
                    toast.info(`New update available: ${res.data.commits_behind} commit(s) behind.`);
                } else {
                    toast.success('OmbiSub is up to date!');
                }
            }
        } catch (err) {
            console.error('Failed to fetch update status:', err);
            if (forceCheck) toast.error('Failed to check GitHub for updates.');
        } finally {
            setLoading(false);
            if (forceCheck) setChecking(false);
        }
    }, [toast]);

    useEffect(() => {
        fetchStatus(false);
    }, [fetchStatus]);

    const handleApplyUpdate = async (force = false) => {
        setApplying(true);
        setConfirmModalOpen(false);
        try {
            const res = await api.applyUpdate(force);
            toast.success(res.data.message || 'Update applied. Server is restarting...');
            // Wait 5 seconds and check connection
            setTimeout(() => {
                window.location.reload();
            }, 5000);
        } catch (err) {
            console.error('Failed to apply update:', err);
            const detail = err.response?.data?.detail || err.message;
            if (err.response?.status === 409) {
                toast.warning(detail);
            } else {
                toast.error(`Update failed: ${detail}`);
            }
            setApplying(false);
        }
    };

    const copyToClipboard = (text, type) => {
        navigator.clipboard.writeText(text);
        if (type === 'url') {
            setCopiedUrl(true);
            setTimeout(() => setCopiedUrl(false), 2000);
        } else {
            setCopiedSecret(true);
            setTimeout(() => setCopiedSecret(false), 2000);
        }
        toast.info('Copied to clipboard');
    };

    const generateSecret = () => {
        const randomBytes = new Uint8Array(16);
        window.crypto.getRandomValues(randomBytes);
        const secret = Array.from(randomBytes).map(b => b.toString(16).padStart(2, '0')).join('');
        updateSetting('github_webhook_secret', secret);
        toast.success('Generated new Webhook Secret. Remember to save settings!');
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center p-12 text-gray-500 dark:text-gray-400">
                <Loader2 className="w-6 h-6 animate-spin mr-2" />
                <span>Checking system version...</span>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            {/* Version & Status Banner */}
            <div className="bg-white dark:bg-gray-800/80 border border-gray-200 dark:border-gray-700/80 rounded-xl p-6 relative overflow-hidden shadow-sm">
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <div className="space-y-1">
                        <div className="flex items-center gap-2">
                            <h3 className="text-lg font-semibold text-gray-900 dark:text-white">OmbiSub System Version</h3>
                            {updateStatus?.update_available ? (
                                <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-500/20 text-amber-700 dark:text-amber-300 border border-amber-500/30">
                                    Update Available ({updateStatus.commits_behind} commits behind)
                                </span>
                            ) : (
                                <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30">
                                    <CheckCircle2 className="w-3 h-3 mr-1" /> Up to Date
                                </span>
                            )}
                        </div>
                        <p className="text-sm text-gray-500 dark:text-gray-400">
                            Connected to <span className="text-gray-700 dark:text-gray-300 font-mono">thanos2940/OmbiSub---Google-Seminar-Capstone-Project</span>
                        </p>
                    </div>

                    <div className="flex items-center gap-2">
                        <button
                            type="button"
                            onClick={() => fetchStatus(true)}
                            disabled={checking || applying}
                            className="inline-flex items-center px-3.5 py-2 text-sm font-medium text-gray-700 dark:text-gray-200 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700/60 dark:hover:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600/50 transition-colors disabled:opacity-50"
                        >
                            <RefreshCw className={`w-4 h-4 mr-1.5 ${checking ? 'animate-spin' : ''}`} />
                            {checking ? 'Checking...' : 'Check for Updates'}
                        </button>

                        {updateStatus?.update_available && (
                            <button
                                type="button"
                                onClick={() => setConfirmModalOpen(true)}
                                disabled={applying}
                                className="inline-flex items-center px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg shadow-sm transition-colors disabled:opacity-50"
                            >
                                <ArrowDownCircle className={`w-4 h-4 mr-1.5 ${applying ? 'animate-spin' : ''}`} />
                                {applying ? 'Installing...' : 'Update & Restart'}
                            </button>
                        )}
                    </div>
                </div>

                {/* Git Metadata Grid */}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-6 pt-5 border-t border-gray-200 dark:border-gray-700/60">
                    <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 border border-gray-200 dark:border-gray-800">
                        <div className="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1.5 mb-1">
                            <GitCommit className="w-3.5 h-3.5 text-indigo-500 dark:text-indigo-400" /> Current Commit
                        </div>
                        <div className="font-mono text-sm text-gray-800 dark:text-gray-200">{updateStatus?.current_commit || 'unknown'}</div>
                        <div className="text-xs text-gray-500 dark:text-gray-400 truncate mt-0.5" title={updateStatus?.current_message}>
                            {updateStatus?.current_message || 'Local working copy'}
                        </div>
                    </div>

                    <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 border border-gray-200 dark:border-gray-800">
                        <div className="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1.5 mb-1">
                            <GitBranch className="w-3.5 h-3.5 text-purple-500 dark:text-purple-400" /> Branch
                        </div>
                        <div className="font-mono text-sm text-gray-800 dark:text-gray-200">{updateStatus?.branch || 'main'}</div>
                        <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Tracking origin/{updateStatus?.branch || 'main'}</div>
                    </div>

                    <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 border border-gray-200 dark:border-gray-800">
                        <div className="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1.5 mb-1">
                            <Clock className="w-3.5 h-3.5 text-emerald-500 dark:text-emerald-400" /> Last Checked
                        </div>
                        <div className="text-sm text-gray-800 dark:text-gray-200">{updateStatus?.last_checked || 'Just now'}</div>
                        <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Automated or manual check</div>
                    </div>

                    <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 border border-gray-200 dark:border-gray-800">
                        <div className="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1.5 mb-1">
                            <Terminal className="w-3.5 h-3.5 text-amber-500 dark:text-amber-400" /> Latest Remote
                        </div>
                        <div className="font-mono text-sm text-gray-800 dark:text-gray-200">{updateStatus?.latest_commit || updateStatus?.current_commit}</div>
                        <div className="text-xs text-gray-500 dark:text-gray-400 truncate mt-0.5" title={updateStatus?.latest_message}>
                            {updateStatus?.latest_message || 'Up to date'}
                        </div>
                    </div>
                </div>
            </div>

            {/* Incoming Changelog (if update available) */}
            {updateStatus?.update_available && updateStatus?.changelog?.length > 0 && (
                <div className="bg-white dark:bg-gray-800/80 border border-amber-500/30 rounded-xl p-5 shadow-sm">
                    <h4 className="text-sm font-semibold text-amber-800 dark:text-amber-200 flex items-center gap-2 mb-3">
                        <GitCommit className="w-4 h-4 text-amber-600 dark:text-amber-400" />
                        Incoming Commits ({updateStatus.changelog.length})
                    </h4>
                    <div className="divide-y divide-gray-200 dark:divide-gray-700/50 max-h-56 overflow-y-auto space-y-1">
                        {updateStatus.changelog.map((c, i) => (
                            <div key={i} className="py-2 flex items-start justify-between text-xs gap-4">
                                <div>
                                    <span className="font-mono text-indigo-600 dark:text-indigo-300 font-medium mr-2">{c.hash}</span>
                                    <span className="text-gray-800 dark:text-gray-200">{c.message}</span>
                                </div>
                                <div className="text-gray-500 dark:text-gray-400 whitespace-nowrap text-[11px]">
                                    {c.author} &bull; {c.date}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Automated Polling Settings */}
            <div className="bg-white dark:bg-gray-800/80 border border-gray-200 dark:border-gray-700/80 rounded-xl p-6 space-y-4 shadow-sm">
                <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                    <Clock className="w-4 h-4 text-indigo-500 dark:text-indigo-400" />
                    Automated Background Updates
                </h3>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                    OmbiSub periodically checks GitHub in the background. When enabled, it pulls updates and restarts the server as soon as the translation queue is idle.
                </p>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
                    <label className="flex items-center gap-3 p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg border border-gray-200 dark:border-gray-700/60 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900/80 transition-colors">
                        <input
                            type="checkbox"
                            checked={settings.auto_update_enabled ?? true}
                            onChange={(e) => updateSetting('auto_update_enabled', e.target.checked)}
                            className="w-4 h-4 text-indigo-600 rounded bg-white dark:bg-gray-800 border-gray-300 dark:border-gray-700 focus:ring-indigo-500"
                        />
                        <div>
                            <div className="text-sm font-medium text-gray-800 dark:text-gray-200">Enable Automatic Updates</div>
                            <div className="text-xs text-gray-500 dark:text-gray-400">Automatically install updates and restart when idle</div>
                        </div>
                    </label>

                    <div className="p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg border border-gray-200 dark:border-gray-700/60 flex flex-col justify-center">
                        <label className="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Check Frequency</label>
                        <select
                            value={settings.auto_update_interval_hours ?? 6}
                            onChange={(e) => updateSetting('auto_update_interval_hours', parseInt(e.target.value, 10))}
                            className="bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded text-sm text-gray-800 dark:text-gray-200 p-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                        >
                            <option value={1}>Every 1 hour</option>
                            <option value={3}>Every 3 hours</option>
                            <option value={6}>Every 6 hours (Recommended)</option>
                            <option value={12}>Every 12 hours</option>
                            <option value={24}>Every 24 hours</option>
                        </select>
                    </div>
                </div>
            </div>

            {/* GitHub Push Webhook (Instant Updates) */}
            <div className="bg-white dark:bg-gray-800/80 border border-gray-200 dark:border-gray-700/80 rounded-xl p-6 space-y-4 shadow-sm">
                <div className="flex items-center justify-between">
                    <div>
                        <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                            <Shield className="w-4 h-4 text-indigo-500 dark:text-indigo-400" />
                            GitHub Push Webhook (Instant Zero-Delay Updates)
                        </h3>
                        <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                            Trigger automatic updates the exact moment you push from your local PC to GitHub.
                        </p>
                    </div>
                    <label className="flex items-center gap-2 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={settings.github_webhook_enabled ?? true}
                            onChange={(e) => updateSetting('github_webhook_enabled', e.target.checked)}
                            className="w-4 h-4 text-indigo-600 rounded bg-white dark:bg-gray-800 border-gray-300 dark:border-gray-700 focus:ring-indigo-500"
                        />
                        <span className="text-xs font-medium text-gray-700 dark:text-gray-300">Enabled</span>
                    </label>
                </div>

                <div className="space-y-3 pt-2">
                    <div>
                        <label className="text-xs font-medium text-gray-700 dark:text-gray-300 block mb-1">Payload URL</label>
                        <div className="flex items-center gap-2">
                            <input
                                type="text"
                                readOnly
                                value={webhookUrl}
                                className="w-full bg-gray-50 dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg px-3 py-2 text-xs font-mono text-gray-800 dark:text-gray-300 focus:outline-none"
                            />
                            <button
                                type="button"
                                onClick={() => copyToClipboard(webhookUrl, 'url')}
                                className="px-3 py-2 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700/80 dark:hover:bg-gray-700 rounded-lg text-gray-700 dark:text-gray-200 text-xs font-medium flex items-center gap-1.5 transition-colors border border-gray-200 dark:border-transparent"
                            >
                                {copiedUrl ? <Check className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                                {copiedUrl ? 'Copied' : 'Copy'}
                            </button>
                        </div>
                    </div>

                    <div>
                        <div className="flex items-center justify-between mb-1">
                            <label className="text-xs font-medium text-gray-700 dark:text-gray-300">Secret (HMAC SHA-256)</label>
                            <button
                                type="button"
                                onClick={generateSecret}
                                className="text-xs text-indigo-600 dark:text-indigo-400 hover:text-indigo-500 dark:hover:text-indigo-300 font-medium"
                            >
                                Generate Secret
                            </button>
                        </div>
                        <div className="flex items-center gap-2">
                            <input
                                type="password"
                                value={settings.github_webhook_secret ?? ''}
                                onChange={(e) => updateSetting('github_webhook_secret', e.target.value)}
                                placeholder="Enter secret or generate one"
                                className="w-full bg-gray-50 dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg px-3 py-2 text-xs font-mono text-gray-800 dark:text-gray-300 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                            />
                            {settings.github_webhook_secret && (
                                <button
                                    type="button"
                                    onClick={() => copyToClipboard(settings.github_webhook_secret, 'secret')}
                                    className="px-3 py-2 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700/80 dark:hover:bg-gray-700 rounded-lg text-gray-700 dark:text-gray-200 text-xs font-medium flex items-center gap-1.5 transition-colors border border-gray-200 dark:border-transparent"
                                >
                                    {copiedSecret ? <Check className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                                    {copiedSecret ? 'Copied' : 'Copy'}
                                </button>
                            )}
                        </div>
                    </div>
                </div>

                <div className="bg-gray-50 dark:bg-gray-900/60 border border-gray-200 dark:border-gray-800 rounded-lg p-3 text-xs text-gray-600 dark:text-gray-400 space-y-1">
                    <div className="font-semibold text-gray-800 dark:text-gray-300">How to configure in GitHub:</div>
                    <ol className="list-decimal list-inside space-y-0.5 text-gray-600 dark:text-gray-400">
                        <li>Go to your repository on GitHub &rarr; <strong>Settings &rarr; Webhooks &rarr; Add webhook</strong></li>
                        <li>Paste the <strong>Payload URL</strong> above</li>
                        <li>Set Content type to <strong>application/json</strong></li>
                        <li>Paste the <strong>Secret</strong> (if generated)</li>
                        <li>Choose <strong>"Just the push event"</strong> and click <strong>Add webhook</strong></li>
                    </ol>
                </div>
            </div>

            {/* Confirmation Modal */}
            {confirmModalOpen && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
                    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl max-w-md w-full p-6 space-y-4 shadow-2xl">
                        <div className="flex items-center gap-3 text-amber-500">
                            <AlertCircle className="w-6 h-6" />
                            <h3 className="text-lg font-semibold text-gray-900 dark:text-white">Confirm Server Update</h3>
                        </div>
                        <p className="text-sm text-gray-600 dark:text-gray-300">
                            OmbiSub will download the latest commits from <span className="font-mono text-indigo-600 dark:text-indigo-300">origin/main</span>, update any modified dependencies, and gracefully restart the server.
                        </p>
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                            Your projects, glossaries, and configuration in <code className="text-gray-700 dark:text-gray-300">data/</code> are safely protected and will remain untouched.
                        </p>
                        <div className="flex justify-end gap-3 pt-2">
                            <button
                                type="button"
                                onClick={() => setConfirmModalOpen(false)}
                                className="px-4 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors"
                            >
                                Cancel
                            </button>
                            <button
                                type="button"
                                onClick={() => handleApplyUpdate(false)}
                                className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors"
                            >
                                Update & Restart Now
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
