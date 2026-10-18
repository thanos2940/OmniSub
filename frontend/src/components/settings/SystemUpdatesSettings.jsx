import React, { useState, useEffect, useCallback } from 'react';
import {
    RefreshCw, GitCommit, GitBranch, ArrowDownCircle, CheckCircle2,
    AlertCircle, Clock, Key, Copy, Check, Shield, Terminal, ExternalLink, Loader2,
    Download, Upload, HardDrive, Archive, FileText, CheckCircle, Server, Tag, Info
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

    // Backup & Migration State
    const [backupEstimate, setBackupEstimate] = useState(null);
    const [, setEstimateLoading] = useState(false);
    const [backupMode, setBackupMode] = useState('full');
    const [exporting, setExporting] = useState(false);
    const [importFile, setImportFile] = useState(null);
    const [inspecting, setInspecting] = useState(false);
    const [inspectResult, setInspectResult] = useState(null);
    const [convertUncPaths, setConvertUncPaths] = useState(true);
    const [importing, setImporting] = useState(false);
    const [importSuccess, setImportSuccess] = useState(null);

    const webhookUrl = `${window.location.origin}${window.__OMBISUB_BASE__ || ''}/api/webhooks/github`;

    const fetchStatus = useCallback(async (forceCheck = false) => {
        if (forceCheck) setChecking(true);
        try {
            const res = forceCheck ? await api.checkForUpdates() : await api.getUpdateStatus();
            setUpdateStatus(res.data);
            if (forceCheck) {
                if (res.data.update_available) {
                    toast.info(res.data.channel === 'main'
                        ? `${res.data.behind} new commit(s) on main`
                        : `New release available: ${res.data.latest_release?.tag_name || res.data.latest_commit}`);
                } else if (res.data.release_status === 'no_releases_published') {
                    toast.info('No official GitHub releases published yet.');
                } else {
                    toast.success('OmbiSub is up to date!');
                }
            }
        } catch (err) {
            console.error('Failed to fetch update status:', err);
            if (forceCheck) toast.error('Failed to check GitHub for releases.');
        } finally {
            setLoading(false);
            if (forceCheck) setChecking(false);
        }
    }, [toast]);

    const fetchEstimate = useCallback(async () => {
        setEstimateLoading(true);
        try {
            const res = await api.getBackupEstimate();
            setBackupEstimate(res.data);
        } catch (err) {
            console.error('Failed to fetch backup estimate:', err);
        } finally {
            setEstimateLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchStatus(false);
        fetchEstimate();
    }, [fetchStatus, fetchEstimate]);

    const handleApplyUpdate = async (force = false) => {
        setApplying(true);
        setConfirmModalOpen(false);
        try {
            const res = await api.applyUpdate(force);
            toast.success(res.data.message || 'Update applied. Server is restarting...');
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

    const handleExportBackup = async () => {
        setExporting(true);
        try {
            const href = await api.backupDownloadLink(backupMode);
            const link = document.createElement('a');
            link.href = href;
            link.setAttribute('download', '');
            document.body.appendChild(link);
            link.click();
            link.parentNode?.removeChild(link);
            toast.success('Building the bundle: your browser starts the download when it is ready (a few minutes for a large library).');
        } catch (err) {
            console.error('Export error:', err);
            toast.error('Failed to export migration bundle.');
        } finally {
            setExporting(false);
        }
    };

    const handleFileSelect = async (e) => {
        const file = e.target.files?.[0];
        if (!file) return;
        setImportFile(file);
        setInspecting(true);
        setInspectResult(null);
        setImportSuccess(null);
        try {
            const formData = new FormData();
            formData.append('file', file);
            const res = await api.inspectBackupBundle(formData);
            setInspectResult(res.data);
            toast.info('Migration bundle verified: ready to restore.');
        } catch (err) {
            console.error('Inspection failed:', err);
            toast.error(err.response?.data?.detail || 'Failed to inspect migration bundle.');
            setImportFile(null);
        } finally {
            setInspecting(false);
        }
    };

    const handleImportBackup = async () => {
        if (!importFile) return;
        setImporting(true);
        try {
            const formData = new FormData();
            formData.append('file', importFile);
            formData.append('remap_unc_to_local', convertUncPaths);
            const res = await api.importBackupBundle(formData);
            setImportSuccess(res.data);
            toast.success(res.data.message || 'Server migration and restore completed successfully!');
            fetchEstimate();
        } catch (err) {
            console.error('Restore failed:', err);
            toast.error(err.response?.data?.detail || 'Failed to import migration bundle.');
        } finally {
            setImporting(false);
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

    const installedTag = updateStatus?.installed_release?.tag_name || updateStatus?.tag_name || updateStatus?.current_commit;
    const latestRelease = updateStatus?.latest_release;
    const isNoReleases = updateStatus?.release_status === 'no_releases_published';
    const isMain = updateStatus?.channel === 'main';
    const notChecked = updateStatus?.checked === false;
    const commits = updateStatus?.commits || [];

    return (
        <div className="space-y-6">
            {/* Version & Status Banner */}
            <div className="bg-white dark:bg-gray-800/80 border border-gray-200 dark:border-gray-700/80 rounded-xl p-6 relative overflow-hidden shadow-sm">
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <div className="space-y-1">
                        <div className="flex items-center gap-2 flex-wrap">
                            <h3 className="text-lg font-semibold text-gray-900 dark:text-white">{isMain ? 'OmbiSub Updates (main branch)' : 'OmbiSub Official Releases'}</h3>
                            {updateStatus?.update_available ? (
                                <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-500/20 text-amber-700 dark:text-amber-300 border border-amber-500/30">
                                    <Tag className="w-3 h-3 mr-1" /> {isMain ? `${updateStatus.behind} new commit${updateStatus.behind === 1 ? '' : 's'} available` : `New Release Available: ${latestRelease?.tag_name || 'Latest'}`}
                                </span>
                            ) : notChecked ? (
                                <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-sky-500/20 text-sky-700 dark:text-sky-300 border border-sky-500/30">
                                    <Info className="w-3 h-3 mr-1" /> Not checked yet
                                </span>
                            ) : isNoReleases && !isMain ? (
                                <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-sky-500/20 text-sky-700 dark:text-sky-300 border border-sky-500/30">
                                    <Info className="w-3 h-3 mr-1" /> No GitHub Releases Published Yet
                                </span>
                            ) : (
                                <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30">
                                    <CheckCircle2 className="w-3 h-3 mr-1" /> Up to Date
                                </span>
                            )}
                        </div>
                        <p className="text-sm text-gray-500 dark:text-gray-400">
                            {isMain ? 'Following the main branch of ' : 'Tracking Official GitHub Releases from '}<span className="text-gray-700 dark:text-gray-300 font-mono">thanos2940/OmbiSub---Google-Seminar-Capstone-Project</span>
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
                            {checking ? 'Checking...' : 'Check for updates'}
                        </button>

                        {updateStatus?.update_available && (
                            <button
                                type="button"
                                onClick={() => setConfirmModalOpen(true)}
                                disabled={applying}
                                className="inline-flex items-center px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg shadow-sm transition-colors disabled:opacity-50"
                            >
                                <ArrowDownCircle className={`w-4 h-4 mr-1.5 ${applying ? 'animate-spin' : ''}`} />
                                {applying ? 'Installing...' : `Install ${isMain ? 'update' : (latestRelease?.tag_name || 'Update')} & Restart`}
                            </button>
                        )}
                    </div>
                </div>

                {/* Release Metadata Grid */}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-6 pt-5 border-t border-gray-200 dark:border-gray-700/60">
                    <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 border border-gray-200 dark:border-gray-800">
                        <div className="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1.5 mb-1">
                            <Tag className="w-3.5 h-3.5 text-indigo-500 dark:text-indigo-400" /> Installed Release
                        </div>
                        <div className="font-mono text-sm text-gray-800 dark:text-gray-200 font-semibold">{installedTag || 'untagged'}</div>
                        <div className="text-xs text-gray-500 dark:text-gray-400 truncate mt-0.5" title={updateStatus?.current_commit_full}>
                            Commit: {updateStatus?.current_commit || 'unknown'}
                        </div>
                    </div>

                    <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 border border-gray-200 dark:border-gray-800">
                        <div className="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1.5 mb-1">
                            <ExternalLink className="w-3.5 h-3.5 text-purple-500 dark:text-purple-400" /> Latest GitHub Release
                        </div>
                        <div className="font-mono text-sm text-gray-800 dark:text-gray-200 font-semibold">
                            {latestRelease?.tag_name || (isNoReleases ? 'None Published' : installedTag)}
                        </div>
                        <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                            {latestRelease?.published_at ? `Published: ${latestRelease.published_at.slice(0, 10)}` : 'Official Release Channel'}
                        </div>
                    </div>

                    <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 border border-gray-200 dark:border-gray-800">
                        <div className="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1.5 mb-1">
                            <Clock className="w-3.5 h-3.5 text-emerald-500 dark:text-emerald-400" /> Last Checked
                        </div>
                        <div className="text-sm text-gray-800 dark:text-gray-200">{updateStatus?.last_checked || (notChecked ? 'Never' : 'Just now')}</div>
                        <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Automated or manual check</div>
                    </div>

                    <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 border border-gray-200 dark:border-gray-800">
                        <div className="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1.5 mb-1">
                            <Terminal className="w-3.5 h-3.5 text-amber-500 dark:text-amber-400" /> Target Commit
                        </div>
                        <div className="font-mono text-sm text-gray-800 dark:text-gray-200">{updateStatus?.latest_commit || updateStatus?.current_commit}</div>
                        <div className="text-xs text-gray-500 dark:text-gray-400 truncate mt-0.5" title={updateStatus?.latest_message}>
                            {latestRelease?.name || updateStatus?.latest_message || 'Up to date'}
                        </div>
                    </div>
                </div>
            </div>

            {/* Formatted Release Notes Card (if release available) */}
            {latestRelease?.body && (
                <div className="bg-white dark:bg-gray-800/80 border border-indigo-500/30 rounded-xl p-5 shadow-sm space-y-3">
                    <div className="flex items-center justify-between">
                        <h4 className="text-sm font-semibold text-indigo-900 dark:text-indigo-200 flex items-center gap-2">
                            <Tag className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
                            Official Release Notes: {latestRelease.name || latestRelease.tag_name}
                        </h4>
                        {latestRelease.html_url && (
                            <a
                                href={latestRelease.html_url}
                                target="_blank"
                                rel="noreferrer"
                                className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline flex items-center gap-1"
                            >
                                View on GitHub <ExternalLink className="w-3 h-3" />
                            </a>
                        )}
                    </div>
                    <div className="bg-gray-50 dark:bg-gray-900/60 p-4 rounded-lg text-xs text-gray-700 dark:text-gray-300 font-mono whitespace-pre-wrap max-h-60 overflow-y-auto border border-gray-200 dark:border-gray-800 leading-relaxed">
                        {latestRelease.body}
                    </div>
                </div>
            )}

            {/* What the update brings: the commit subjects on the main channel */}
            {isMain && commits.length > 0 && (
                <div className="bg-white dark:bg-gray-800/80 border border-indigo-500/30 rounded-xl p-5 shadow-sm space-y-3">
                    <h4 className="text-sm font-semibold text-indigo-900 dark:text-indigo-200 flex items-center gap-2">
                        <GitCommit className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
                        What's new ({updateStatus.behind} commit{updateStatus.behind === 1 ? '' : 's'}{commits.length < updateStatus.behind ? `, newest ${commits.length} shown` : ''})
                    </h4>
                    <ul className="bg-gray-50 dark:bg-gray-900/60 p-4 rounded-lg text-xs text-gray-700 dark:text-gray-300 font-mono max-h-60 overflow-y-auto border border-gray-200 dark:border-gray-800 leading-relaxed space-y-1">
                        {commits.map((c) => (
                            <li key={c.hash}><span className="text-gray-500 dark:text-gray-500">{c.hash}</span> {c.message}</li>
                        ))}
                    </ul>
                </div>
            )}
            {isMain && updateStatus?.ahead > 0 && (
                <div className="flex items-start gap-2 p-3 rounded-lg border border-amber-500/30 bg-amber-500/10 text-xs text-amber-800 dark:text-amber-300">
                    <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                    This checkout has {updateStatus.ahead} commit(s) that are not on origin/main; updating is refused until they are pushed.
                </div>
            )}

            {/* Automated Release Installation Settings */}
            <div className="bg-white dark:bg-gray-800/80 border border-gray-200 dark:border-gray-700/80 rounded-xl p-6 space-y-4 shadow-sm">
                <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                    <Clock className="w-4 h-4 text-indigo-500 dark:text-indigo-400" />
                    Automated Background Updates
                </h3>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                    OmbiSub periodically checks GitHub in the background. When enabled, it installs new updates (commits on main, or official releases, depending on the channel) and restarts the server as soon as the translation queue is idle.
                </p>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
                    <label className="flex items-center gap-3 p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg border border-gray-200 dark:border-gray-700/60 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900/80 transition-colors">
                        <input
                            type="checkbox"
                            checked={settings.auto_update_enabled ?? false}
                            onChange={(e) => updateSetting('auto_update_enabled', e.target.checked)}
                            className="w-4 h-4 text-indigo-600 rounded bg-white dark:bg-gray-800 border-gray-300 dark:border-gray-700 focus:ring-indigo-500"
                        />
                        <div>
                            <div className="text-sm font-medium text-gray-800 dark:text-gray-200">Automatically install updates when idle</div>
                            <div className="text-xs text-gray-500 dark:text-gray-400">Installs when the translation queue is idle</div>
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

                    <div className="p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg border border-gray-200 dark:border-gray-700/60 flex flex-col justify-center">
                        <label className="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Update channel</label>
                        <select
                            value={settings.update_channel ?? 'auto'}
                            onChange={(e) => updateSetting('update_channel', e.target.value)}
                            className="bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded text-sm text-gray-800 dark:text-gray-200 p-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                        >
                            <option value="auto">Automatic (main until releases are published)</option>
                            <option value="main">main branch (every pushed commit)</option>
                            <option value="releases">GitHub Releases only</option>
                        </select>
                    </div>
                </div>
            </div>

            {/* Reverse proxy (Caddy) */}
            <div className="bg-white dark:bg-gray-800/80 border border-gray-200 dark:border-gray-700/80 rounded-xl p-6 space-y-4 shadow-sm">
                <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                    <Server className="w-4 h-4 text-indigo-500 dark:text-indigo-400" />
                    Reverse proxy
                </h3>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                    Serving OmbiSub under a path of another site (for example Caddy at example.com/ombisub)? Enter that path. Leave it empty for a (sub)domain or direct access.
                    Both settings take effect after a restart. Turn on the login (Security tab) before exposing OmbiSub to the internet.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <label className="p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg border border-gray-200 dark:border-gray-700/60 flex flex-col">
                        <span className="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">URL base (restart required)</span>
                        <input
                            type="text"
                            value={settings.url_base ?? ''}
                            onChange={(e) => updateSetting('url_base', e.target.value)}
                            placeholder="/ombisub"
                            className="bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded text-sm text-gray-800 dark:text-gray-200 p-1.5 font-mono focus:outline-none focus:ring-1 focus:ring-indigo-500"
                        />
                    </label>
                    <label className="p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg border border-gray-200 dark:border-gray-700/60 flex flex-col">
                        <span className="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Trusted proxies (restart required)</span>
                        <input
                            type="text"
                            value={settings.trusted_proxies ?? '127.0.0.1'}
                            onChange={(e) => updateSetting('trusted_proxies', e.target.value)}
                            placeholder="127.0.0.1"
                            className="bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded text-sm text-gray-800 dark:text-gray-200 p-1.5 font-mono focus:outline-none focus:ring-1 focus:ring-indigo-500"
                        />
                    </label>
                </div>
            </div>

            {/* Server Backup & Migration (.zip) Card */}
            <div className="bg-white dark:bg-gray-800/80 border border-gray-200 dark:border-gray-700/80 rounded-xl p-6 space-y-6 shadow-sm">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-gray-200 dark:border-gray-700/60 pb-4">
                    <div>
                        <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                            <Archive className="w-5 h-5 text-indigo-500 dark:text-indigo-400" />
                            Server Backup & Migration (.zip Bundle)
                        </h3>
                        <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                            Snapshot entire server state, databases, translation memories, and glossaries into an encrypted, atomic migration bundle.
                        </p>
                    </div>
                    {backupEstimate && (
                        <div className="text-xs text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/50 px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-800 self-start sm:self-auto">
                            Server: <span className="font-mono text-gray-700 dark:text-gray-300">{backupEstimate.hostname}</span> &bull; {backupEstimate.project_count} projects &bull; {backupEstimate.episode_count} episodes
                        </div>
                    )}
                </div>

                {/* Export Section */}
                <div className="space-y-4">
                    <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200 flex items-center gap-2">
                        <Download className="w-4 h-4 text-emerald-500" />
                        Export Server Migration Bundle
                    </h4>
                    
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div
                            onClick={() => setBackupMode('full')}
                            className={`p-4 rounded-xl border cursor-pointer transition-all ${
                                backupMode === 'full'
                                    ? 'bg-indigo-50/50 dark:bg-indigo-950/20 border-indigo-500 ring-1 ring-indigo-500'
                                    : 'bg-gray-50 dark:bg-gray-900/50 border-gray-200 dark:border-gray-700 hover:border-gray-300'
                            }`}
                        >
                            <div className="flex items-center justify-between mb-1">
                                <span className="text-sm font-semibold text-gray-900 dark:text-white">Full Migration Bundle</span>
                                <span className="text-xs font-mono text-purple-600 dark:text-purple-400 font-medium">
                                    ~{backupEstimate?.full_size_human || 'Calculating...'}
                                </span>
                            </div>
                            <p className="text-xs text-gray-600 dark:text-gray-400 leading-relaxed">
                                Recommended for moving the server. Everything in Compact plus the source subtitle each episode was translated from (needed to re-export ASS typesetting and to retranslate). No video files.
                            </p>
                        </div>
                        <div
                            onClick={() => setBackupMode('compact')}
                            className={`p-4 rounded-xl border cursor-pointer transition-all ${
                                backupMode === 'compact'
                                    ? 'bg-indigo-50/50 dark:bg-indigo-950/20 border-indigo-500 ring-1 ring-indigo-500'
                                    : 'bg-gray-50 dark:bg-gray-900/50 border-gray-200 dark:border-gray-700 hover:border-gray-300'
                            }`}
                        >
                            <div className="flex items-center justify-between mb-1">
                                <span className="text-sm font-semibold text-gray-900 dark:text-white">Compact Backup</span>
                                <span className="text-xs font-mono text-indigo-600 dark:text-indigo-400 font-medium">
                                    ~{backupEstimate?.compact_size_human || 'Calculating...'}
                                </span>
                            </div>
                            <p className="text-xs text-gray-600 dark:text-gray-400 leading-relaxed">
                                Settings, databases, glossaries, translation memory and every translation, without the source subtitle files. A backup of your work, not a server move: ASS episodes cannot be re-exported or retranslated from it.
                            </p>
                        </div>

                    </div>

                    <div className="flex justify-end">
                        <button
                            type="button"
                            onClick={handleExportBackup}
                            disabled={exporting}
                            className="inline-flex items-center px-4 py-2 text-sm font-medium text-white bg-emerald-600 hover:bg-emerald-500 rounded-lg shadow-sm transition-colors disabled:opacity-50"
                        >
                            <Download className={`w-4 h-4 mr-2 ${exporting ? 'animate-bounce' : ''}`} />
                            {exporting ? 'Exporting Bundle (.zip)...' : `Export Migration Bundle (${backupMode})`}
                        </button>
                    </div>
                </div>

                <div className="border-t border-gray-200 dark:border-gray-700/60 pt-4 space-y-4">
                    <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200 flex items-center gap-2">
                        <Upload className="w-4 h-4 text-indigo-500" />
                        Import & Restore Migration Bundle (.zip)
                    </h4>

                    {/* File Dropzone / Picker */}
                    <div className="border-2 border-dashed border-gray-300 dark:border-gray-700 rounded-xl p-6 text-center hover:border-indigo-500 dark:hover:border-indigo-400 transition-colors">
                        <input
                            type="file"
                            accept=".zip"
                            id="backup-file-input"
                            onChange={handleFileSelect}
                            className="hidden"
                        />
                        <label htmlFor="backup-file-input" className="cursor-pointer block space-y-2">
                            <Archive className="w-8 h-8 text-gray-400 mx-auto" />
                            <div className="text-sm font-medium text-gray-700 dark:text-gray-300">
                                {importFile ? importFile.name : 'Click to select or drop OmbiSub migration bundle (.zip)'}
                            </div>
                            <div className="text-xs text-gray-500 dark:text-gray-400">
                                Supports both Compact and Full migration archives
                            </div>
                        </label>
                    </div>

                    {/* Inspection Result Preview */}
                    {inspecting && (
                        <div className="flex items-center justify-center p-4 text-xs text-gray-500">
                            <Loader2 className="w-4 h-4 animate-spin mr-2 text-indigo-500" />
                            Verifying manifest, checksums, and database integrity...
                        </div>
                    )}

                    {inspectResult && (
                        <div className="bg-indigo-50/60 dark:bg-indigo-950/20 border border-indigo-500/30 rounded-xl p-4 space-y-3">
                            <div className="flex items-center justify-between">
                                <div className="text-xs font-semibold text-indigo-900 dark:text-indigo-200 flex items-center gap-1.5">
                                    <CheckCircle className="w-4 h-4 text-emerald-500" /> Validated Migration Bundle
                                </div>
                                <span className="text-[11px] font-mono bg-indigo-500/10 text-indigo-700 dark:text-indigo-300 px-2 py-0.5 rounded border border-indigo-500/20">
                                    SHA-256: {inspectResult.sha256} &bull; Verified
                                </span>
                            </div>

                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                                <div><span className="text-gray-500">Source Host:</span> <span className="font-mono text-gray-800 dark:text-gray-200">{inspectResult.hostname}</span></div>
                                <div><span className="text-gray-500">Projects:</span> <span className="font-semibold text-gray-800 dark:text-gray-200">{inspectResult.project_count}</span></div>
                                <div><span className="text-gray-500">Episodes:</span> <span className="font-semibold text-gray-800 dark:text-gray-200">{inspectResult.episode_count}</span></div>
                                <div><span className="text-gray-500">Mode:</span> <span className="font-semibold text-gray-800 dark:text-gray-200 capitalize">{inspectResult.mode}</span></div>
                            </div>

                            {/* UNC Path Converter Checkbox */}
                            <label className="flex items-center gap-2 pt-2 border-t border-indigo-500/20 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={convertUncPaths}
                                    onChange={(e) => setConvertUncPaths(e.target.checked)}
                                    className="w-4 h-4 text-indigo-600 rounded bg-white dark:bg-gray-800 border-gray-300 dark:border-gray-700 focus:ring-indigo-500"
                                />
                                <span className="text-xs text-gray-700 dark:text-gray-300 font-medium">
                                    Automatically convert UNC network paths (<code className="text-indigo-600 dark:text-indigo-400 font-mono">\\Morpiceserver\e\...</code>) to local server drive paths (<code className="text-indigo-600 dark:text-indigo-400 font-mono">E:\...</code>)
                                </span>
                            </label>

                            <div className="flex justify-end pt-2">
                                <button
                                    type="button"
                                    onClick={handleImportBackup}
                                    disabled={importing}
                                    className="inline-flex items-center px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg shadow-sm transition-colors disabled:opacity-50"
                                >
                                    <Server className={`w-4 h-4 mr-2 ${importing ? 'animate-spin' : ''}`} />
                                    {importing ? 'Restoring & Migrating Server...' : 'Restore & Migrate Server'}
                                </button>
                            </div>
                        </div>
                    )}

                    {importSuccess && (
                        <div className="p-4 bg-emerald-500/10 border border-emerald-500/30 rounded-xl text-xs text-emerald-800 dark:text-emerald-200 flex items-center gap-2">
                            <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400 flex-shrink-0" />
                            <span>
                                {importSuccess.message} ({importSuccess.restored_projects} projects, {importSuccess.restored_episodes} episodes migrated).
                            </span>
                        </div>
                    )}
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
                            Trigger automatic updates the exact moment you push a release or tag to GitHub.
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
                        <li>Choose <strong>"Let me select individual events"</strong> &rarr; Select <strong>Releases</strong> and <strong>Pushes</strong></li>
                    </ol>
                </div>
            </div>

            {/* Confirmation Modal */}
            {confirmModalOpen && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
                    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl max-w-md w-full p-6 space-y-4 shadow-2xl">
                        <div className="flex items-center gap-3 text-amber-500">
                            <AlertCircle className="w-6 h-6" />
                            <h3 className="text-lg font-semibold text-gray-900 dark:text-white">Confirm Official Release Install</h3>
                        </div>
                        <p className="text-sm text-gray-600 dark:text-gray-300">
                            OmbiSub will download {isMain ? 'the latest commits of main' : <>release <span className="font-mono text-indigo-600 dark:text-indigo-300">{latestRelease?.tag_name}</span></>}, update any modified dependencies, rebuild the UI static bundle, and gracefully restart the server.
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
                                Install & Restart Now
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
