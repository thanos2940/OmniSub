import React, { useState } from 'react';
import {
    Tv, Film, RefreshCw, CheckCircle2, XCircle, Loader2, Plus, Trash2,
    Eye, EyeOff, FolderOpen, Play, Activity, AlertTriangle, Zap, Clock
} from 'lucide-react';
import { api } from '../../api';

const SECRET_SENTINEL = '__SECRET_UNCHANGED__';

const MediaServerSettings = ({ settings, onChange, toast }) => {
    const [showSonarrKey, setShowSonarrKey] = useState(false);
    const [showRadarrKey, setShowRadarrKey] = useState(false);
    const [testingSonarr, setTestingSonarr] = useState(false);
    const [testingRadarr, setTestingRadarr] = useState(false);
    const [syncingSonarr, setSyncingSonarr] = useState(false);
    const [syncingRadarr, setSyncingRadarr] = useState(false);
    const [syncingAll, setSyncingAll] = useState(false);

    // Path test state
    const [testRemotePath, setTestRemotePath] = useState('');
    const [testingPath, setTestingPath] = useState(false);
    const [pathTestResult, setPathTestResult] = useState(null);

    // Media Probe test state
    const [probePath, setProbePath] = useState('');
    const [probingMedia, setProbingMedia] = useState(false);
    const [probeResult, setProbeResult] = useState(null);
    const [probeError, setProbeError] = useState(null);

    const handleTestSonarr = async () => {
        setTestingSonarr(true);
        try {
            const payload = {
                url: settings.sonarr_url,
                api_key: settings.sonarr_api_key === SECRET_SENTINEL ? undefined : settings.sonarr_api_key,
            };
            const res = await api.testSonarr(payload);
            if (res.data?.connected) {
                if (toast?.success) toast.success(`Sonarr connected: v${res.data?.version || 'OK'}`);
            } else {
                if (toast?.error) toast.error(`Sonarr connection failed: ${res.data?.error || 'Unknown error'}`);
            }
        } catch (e) {
            if (toast?.error) toast.error(`Sonarr test failed: ${e.response?.data?.detail || e.message}`);
        } finally {
            setTestingSonarr(false);
        }
    };

    const handleTestRadarr = async () => {
        setTestingRadarr(true);
        try {
            const payload = {
                url: settings.radarr_url,
                api_key: settings.radarr_api_key === SECRET_SENTINEL ? undefined : settings.radarr_api_key,
            };
            const res = await api.testRadarr(payload);
            if (res.data?.connected) {
                if (toast?.success) toast.success(`Radarr connected: v${res.data?.version || 'OK'}`);
            } else {
                if (toast?.error) toast.error(`Radarr connection failed: ${res.data?.error || 'Unknown error'}`);
            }
        } catch (e) {
            if (toast?.error) toast.error(`Radarr test failed: ${e.response?.data?.detail || e.message}`);
        } finally {
            setTestingRadarr(false);
        }
    };

    const handleSyncSonarr = async () => {
        setSyncingSonarr(true);
        try {
            const res = await api.syncSonarr();
            if (toast?.success) toast.success(`Sonarr sync complete: ${res.data?.shows_synced || 0} shows updated.`);
        } catch (e) {
            if (toast?.error) toast.error(`Sonarr sync failed: ${e.response?.data?.detail || e.message}`);
        } finally {
            setSyncingSonarr(false);
        }
    };

    const handleSyncRadarr = async () => {
        setSyncingRadarr(true);
        try {
            const res = await api.syncRadarr();
            if (toast?.success) toast.success(`Radarr sync complete: ${res.data?.movies_synced || 0} movies updated.`);
        } catch (e) {
            if (toast?.error) toast.error(`Radarr sync failed: ${e.response?.data?.detail || e.message}`);
        } finally {
            setSyncingRadarr(false);
        }
    };

    const handleSyncAll = async () => {
        setSyncingAll(true);
        try {
            const res = await api.syncArrAll();
            if (toast?.success) toast.success(`Full library sync finished: ${res.data?.total_synced || 0} items checked.`);
        } catch (e) {
            if (toast?.error) toast.error(`Full sync failed: ${e.response?.data?.detail || e.message}`);
        } finally {
            setSyncingAll(false);
        }
    };

    // Path Mappings helper
    const pathMappings = settings.arr_path_mappings || settings.path_mappings || [];
    const handleAddMapping = () => {
        const next = [...pathMappings, { remote: '', local: '' }];
        onChange('arr_path_mappings', next);
        onChange('path_mappings', next);
    };

    const handleUpdateMapping = (idx, field, value) => {
        const next = [...pathMappings];
        next[idx] = { ...next[idx], [field]: value };
        onChange('arr_path_mappings', next);
        onChange('path_mappings', next);
    };

    const handleRemoveMapping = (idx) => {
        const next = pathMappings.filter((_, i) => i !== idx);
        onChange('arr_path_mappings', next);
        onChange('path_mappings', next);
    };

    const handleTestPath = async () => {
        if (!testRemotePath.trim()) return;
        setTestingPath(true);
        setPathTestResult(null);
        try {
            const res = await api.testPath(testRemotePath.trim(), settings.arr_path_mappings || settings.path_mappings || []);
            setPathTestResult(res.data);
        } catch (e) {
            setPathTestResult({ error: e.response?.data?.detail || e.message || 'Path resolution failed' });
        } finally {
            setTestingPath(false);
        }
    };

    const handleRunMediaProbe = async () => {
        if (!probePath.trim()) return;
        setProbingMedia(true);
        setProbeError(null);
        setProbeResult(null);
        try {
            const res = await api.testMediaProbe(probePath.trim());
            setProbeResult(res.data);
        } catch (err) {
            setProbeError(err.response?.data?.detail || err.message || 'Media probe failed');
        } finally {
            setProbingMedia(false);
        }
    };

    return (
        <div className="space-y-6">
            {/* Top Sync All Banner */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                <div>
                    <h3 className="text-base font-bold text-gray-900 dark:text-white">Media Library Synchronization</h3>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                        Import shows, seasons, and movies directly from Sonarr &amp; Radarr.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={handleSyncAll}
                    disabled={syncingAll}
                    className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold flex items-center gap-2 shadow-sm transition-all disabled:opacity-50"
                >
                    <RefreshCw size={14} className={syncingAll ? 'animate-spin' : ''} />
                    <span>{syncingAll ? 'Syncing All...' : 'Sync All Libraries'}</span>
                </button>
            </div>

            {/* Sonarr Integration */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-4">
                <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-700 pb-4">
                    <div className="flex items-center gap-2">
                        <Tv className="text-sky-500" size={20} />
                        <div>
                            <h3 className="text-base font-bold text-gray-900 dark:text-white">Sonarr (TV Shows)</h3>
                            <p className="text-xs text-gray-500 dark:text-gray-400">Automatic series monitoring &amp; episode synchronization</p>
                        </div>
                    </div>

                    <label className="flex items-center gap-2 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={settings.sonarr_enabled || false}
                            onChange={(e) => onChange('sonarr_enabled', e.target.checked)}
                            className="w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                        />
                        <span className="text-xs font-semibold text-gray-700 dark:text-gray-300">Enabled</span>
                    </label>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label className="block text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1">Sonarr Host URL</label>
                        <input
                            type="text"
                            value={settings.sonarr_url || ''}
                            onChange={(e) => onChange('sonarr_url', e.target.value)}
                            placeholder="http://localhost:8989"
                            className="w-full px-3.5 py-2 text-sm rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none focus:ring-2 focus:ring-indigo-500"
                        />
                    </div>

                    <div>
                        <label className="block text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1">Sonarr API Key</label>
                        <div className="relative">
                            <input
                                type={showSonarrKey ? 'text' : 'password'}
                                value={settings.sonarr_api_key || ''}
                                onChange={(e) => onChange('sonarr_api_key', e.target.value)}
                                placeholder="Enter Sonarr API Key"
                                className="w-full px-3.5 py-2 pr-10 text-sm rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white font-mono outline-none focus:ring-2 focus:ring-indigo-500"
                            />
                            <button
                                type="button"
                                onClick={() => setShowSonarrKey(!showSonarrKey)}
                                className="absolute right-3 top-2.5 text-gray-400 hover:text-gray-600"
                            >
                                {showSonarrKey ? <EyeOff size={16} /> : <Eye size={16} />}
                            </button>
                        </div>
                    </div>
                </div>

                <div className="flex justify-end gap-2 pt-2">
                    <button
                        type="button"
                        onClick={handleTestSonarr}
                        disabled={testingSonarr}
                        className="px-3.5 py-1.5 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors disabled:opacity-50"
                    >
                        {testingSonarr ? <Loader2 size={14} className="animate-spin" /> : <Activity size={14} />}
                        <span>Test Sonarr</span>
                    </button>
                    <button
                        type="button"
                        onClick={handleSyncSonarr}
                        disabled={syncingSonarr}
                        className="px-3.5 py-1.5 bg-sky-600 hover:bg-sky-700 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors disabled:opacity-50"
                    >
                        <RefreshCw size={14} className={syncingSonarr ? 'animate-spin' : ''} />
                        <span>Sync Sonarr</span>
                    </button>
                </div>
            </div>

            {/* Radarr Integration */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-4">
                <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-700 pb-4">
                    <div className="flex items-center gap-2">
                        <Film className="text-amber-500" size={20} />
                        <div>
                            <h3 className="text-base font-bold text-gray-900 dark:text-white">Radarr (Movies)</h3>
                            <p className="text-xs text-gray-500 dark:text-gray-400">Automatic movie library monitoring &amp; subtitle synchronization</p>
                        </div>
                    </div>

                    <label className="flex items-center gap-2 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={settings.radarr_enabled || false}
                            onChange={(e) => onChange('radarr_enabled', e.target.checked)}
                            className="w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                        />
                        <span className="text-xs font-semibold text-gray-700 dark:text-gray-300">Enabled</span>
                    </label>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label className="block text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1">Radarr Host URL</label>
                        <input
                            type="text"
                            value={settings.radarr_url || ''}
                            onChange={(e) => onChange('radarr_url', e.target.value)}
                            placeholder="http://localhost:7878"
                            className="w-full px-3.5 py-2 text-sm rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none focus:ring-2 focus:ring-indigo-500"
                        />
                    </div>

                    <div>
                        <label className="block text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1">Radarr API Key</label>
                        <div className="relative">
                            <input
                                type={showRadarrKey ? 'text' : 'password'}
                                value={settings.radarr_api_key || ''}
                                onChange={(e) => onChange('radarr_api_key', e.target.value)}
                                placeholder="Enter Radarr API Key"
                                className="w-full px-3.5 py-2 pr-10 text-sm rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white font-mono outline-none focus:ring-2 focus:ring-indigo-500"
                            />
                            <button
                                type="button"
                                onClick={() => setShowRadarrKey(!showRadarrKey)}
                                className="absolute right-3 top-2.5 text-gray-400 hover:text-gray-600"
                            >
                                {showRadarrKey ? <EyeOff size={16} /> : <Eye size={16} />}
                            </button>
                        </div>
                    </div>
                </div>

                <div className="flex justify-end gap-2 pt-2">
                    <button
                        type="button"
                        onClick={handleTestRadarr}
                        disabled={testingRadarr}
                        className="px-3.5 py-1.5 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors disabled:opacity-50"
                    >
                        {testingRadarr ? <Loader2 size={14} className="animate-spin" /> : <Activity size={14} />}
                        <span>Test Radarr</span>
                    </button>
                    <button
                        type="button"
                        onClick={handleSyncRadarr}
                        disabled={syncingRadarr}
                        className="px-3.5 py-1.5 bg-amber-600 hover:bg-amber-700 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors disabled:opacity-50"
                    >
                        <RefreshCw size={14} className={syncingRadarr ? 'animate-spin' : ''} />
                        <span>Sync Radarr</span>
                    </button>
                </div>
            </div>

            {/* Automation & Webhook Policy */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-4">
                <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-700 pb-4">
                    <div className="flex items-center gap-2">
                        <Zap className="text-amber-500" size={20} />
                        <div>
                            <h3 className="text-base font-bold text-gray-900 dark:text-white">Auto-Translation &amp; Webhook Policy</h3>
                            <p className="text-xs text-gray-500 dark:text-gray-400">Control how Sonarr &amp; Radarr download events trigger translations</p>
                        </div>
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 items-start">
                    <div>
                        <label className="block text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1.5">Auto-Translate Mode</label>
                        <select
                            value={
                                settings.arr_auto_translate === 'smart' || settings.arr_auto_translate === true
                                    ? (settings.arr_auto_translate === true ? 'true' : 'smart')
                                    : 'false'
                            }
                            onChange={(e) => {
                                const val = e.target.value;
                                onChange('arr_auto_translate', val === 'true' ? true : (val === 'smart' ? 'smart' : false));
                            }}
                            className="w-full px-3.5 py-2 text-sm rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none focus:ring-2 focus:ring-indigo-500"
                        >
                            <option value="smart">Smart Policy (Anime Immediate, Live-Action Delayed for Bazarr)</option>
                            <option value="true">Immediate All (Auto-translate all incoming media immediately)</option>
                            <option value="false">Disabled (Manual queue &amp; translation only)</option>
                        </select>
                        <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-relaxed">
                            Smart policy extracts &amp; translates anime immediately. Live-action series and movies are queued with a delay to give Bazarr time to search external providers.
                        </p>
                    </div>

                    {(settings.arr_auto_translate === 'smart' || settings.arr_auto_translate === undefined) && (
                        <div>
                            <label className="block text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1.5 flex items-center gap-1.5">
                                <Clock size={13} className="text-indigo-500" />
                                <span>Bazarr Wait Delay (Minutes)</span>
                            </label>
                            <input
                                type="number"
                                min="0"
                                max="1440"
                                value={settings.arr_non_anime_delay_minutes ?? 30}
                                onChange={(e) => onChange('arr_non_anime_delay_minutes', Math.max(0, parseInt(e.target.value) || 0))}
                                className="w-full px-3.5 py-2 text-sm rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none focus:ring-2 focus:ring-indigo-500"
                            />
                            <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-relaxed">
                                If Bazarr drops a Greek subtitle during this window, OmbiSub automatically detects it on disk, marks the item complete, and skips translation with <strong>0 tokens used</strong>.
                            </p>
                        </div>
                    )}
                </div>
            </div>

            {/* Path Mappings Table */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-4">
                <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-700 pb-4">
                    <div className="flex items-center gap-2">
                        <FolderOpen className="text-purple-500" size={20} />
                        <div>
                            <h3 className="text-base font-bold text-gray-900 dark:text-white">Path Mappings (Docker / Remote Mounts)</h3>
                            <p className="text-xs text-gray-500 dark:text-gray-400">Map paths reported by remote Sonarr/Radarr instances to local filesystem locations.</p>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={handleAddMapping}
                        className="px-3 py-1.5 bg-indigo-50 dark:bg-indigo-950/50 hover:bg-indigo-100 text-indigo-700 dark:text-indigo-300 rounded-xl text-xs font-bold flex items-center gap-1 transition-colors"
                    >
                        <Plus size={14} /> Add Mapping
                    </button>
                </div>

                {pathMappings.length === 0 ? (
                    <div className="text-center py-6 text-xs text-gray-400">
                        No custom path mappings defined. Direct paths will be accessed.
                    </div>
                ) : (
                    <div className="space-y-3">
                        {pathMappings.map((mapping, idx) => (
                            <div key={idx} className="flex items-center gap-2">
                                <input
                                    type="text"
                                    value={mapping.remote || ''}
                                    onChange={(e) => handleUpdateMapping(idx, 'remote', e.target.value)}
                                    placeholder="Remote Path (e.g. /media/tv)"
                                    className="flex-1 px-3 py-2 text-xs font-mono rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none"
                                />
                                <span className="text-xs font-bold text-gray-400">&rarr;</span>
                                <input
                                    type="text"
                                    value={mapping.local || ''}
                                    onChange={(e) => handleUpdateMapping(idx, 'local', e.target.value)}
                                    placeholder="Local Path (e.g. D:\Media\TV)"
                                    className="flex-1 px-3 py-2 text-xs font-mono rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none"
                                />
                                <button
                                    type="button"
                                    onClick={() => handleRemoveMapping(idx)}
                                    className="p-2 text-gray-400 hover:text-rose-600 rounded-lg transition-colors"
                                >
                                    <Trash2 size={16} />
                                </button>
                            </div>
                        ))}
                    </div>
                )}

                {/* Path Diagnostic Tester */}
                <div className="pt-4 border-t border-gray-100 dark:border-gray-700">
                    <label className="block text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1">Test Path Mapping</label>
                    <div className="flex gap-2">
                        <input
                            type="text"
                            value={testRemotePath}
                            onChange={(e) => setTestRemotePath(e.target.value)}
                            placeholder="Enter a remote path to test resolution..."
                            className="flex-1 px-3 py-2 text-xs font-mono rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none"
                        />
                        <button
                            type="button"
                            onClick={handleTestPath}
                            disabled={testingPath || !testRemotePath.trim()}
                            className="px-4 py-2 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 text-xs font-semibold rounded-lg flex items-center gap-1.5 transition-colors disabled:opacity-50"
                        >
                            {testingPath ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                            <span>Test Resolution</span>
                        </button>
                    </div>

                    {pathTestResult && (
                        <div className="mt-2 p-3 rounded-lg bg-gray-50 dark:bg-gray-750 text-xs font-mono border border-gray-200 dark:border-gray-700">
                            {pathTestResult.error ? (
                                <span className="text-rose-500 font-bold">{pathTestResult.error}</span>
                            ) : (
                                <div>
                                    <span className="text-gray-400">Resolved to: </span>
                                    <span className="text-emerald-600 dark:text-emerald-400 font-bold">{pathTestResult.local_path}</span>
                                    <span className={`ml-2 px-2 py-0.5 rounded text-[10px] ${pathTestResult.exists ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'}`}>
                                        {pathTestResult.exists ? 'Accessible on disk' : 'Path not found'}
                                    </span>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>

            {/* Media Probe Diagnostic Tool */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-4">
                <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-4">
                    <Activity className="text-indigo-600 dark:text-indigo-400" size={20} />
                    <div>
                        <h3 className="text-base font-bold text-gray-900 dark:text-white">Media Probe Diagnostic Tool (Embedded ASS Inspection)</h3>
                        <p className="text-xs text-gray-500 dark:text-gray-400">Test FFmpeg/MKVToolNix probing on a local video file to inspect embedded subtitle tracks.</p>
                    </div>
                </div>

                <div className="flex gap-2">
                    <input
                        type="text"
                        value={probePath}
                        onChange={(e) => setProbePath(e.target.value)}
                        placeholder="Path to .mkv/.mp4 file (e.g. D:\Anime\Show\Episode01.mkv)..."
                        className="flex-1 px-3.5 py-2 text-xs font-mono rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none focus:ring-2 focus:ring-indigo-500"
                    />
                    <button
                        type="button"
                        onClick={handleRunMediaProbe}
                        disabled={probingMedia || !probePath.trim()}
                        className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold flex items-center gap-2 transition-colors disabled:opacity-50 shadow-sm"
                    >
                        {probingMedia ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                        <span>Probe Media</span>
                    </button>
                </div>

                {probeError && (
                    <div className="p-3 bg-rose-50 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-900 text-rose-700 dark:text-rose-400 text-xs rounded-xl flex items-center gap-2">
                        <AlertTriangle size={16} className="shrink-0" />
                        <span>{probeError}</span>
                    </div>
                )}

                {probeResult && (
                    <div className="p-4 bg-gray-50 dark:bg-gray-750 border border-gray-200 dark:border-gray-700 rounded-xl space-y-2 text-xs font-mono">
                        <div className="font-bold text-gray-900 dark:text-white">
                            Found {probeResult.tracks?.length || 0} subtitle track(s):
                        </div>
                        <div className="space-y-1.5">
                            {(probeResult.tracks || []).map((t, idx) => (
                                <div key={idx} className="p-2 rounded bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 flex justify-between items-center">
                                    <div>
                                        <span className="font-bold text-indigo-600 dark:text-indigo-400">Track #{t.index || idx}: </span>
                                        <span className="text-gray-800 dark:text-gray-200">{t.codec_name || t.format} ({t.language || 'und'})</span>
                                        {t.title && <span className="text-gray-400 ml-2">"{t.title}"</span>}
                                    </div>
                                    <span className={`px-1.5 py-0.5 text-[10px] rounded font-bold uppercase ${t.is_ass ? 'bg-purple-100 text-purple-800' : 'bg-gray-100 text-gray-800'}`}>
                                        {t.is_ass ? 'ASS Stream' : 'Standard'}
                                    </span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};

export default MediaServerSettings;
