import React, { useState } from 'react';
import { Tv, Film, CheckCircle2, XCircle, Loader2, Copy } from 'lucide-react';
import { api } from '../../api';

const ServiceBlock = ({ label, Icon, url, setUrl, apiKey, setApiKey, status, onTest, webhookUrl }) => (
    <div className="border border-gray-200 dark:border-gray-600 rounded-xl p-3 space-y-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-gray-800 dark:text-gray-200">
            <Icon size={16} /> {label}
        </div>
        <input type="text" value={url} onChange={(e) => setUrl(e.target.value)} placeholder={`http://localhost:${label === 'Sonarr' ? '8989' : '7878'}`} className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none" />
        <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="API key" className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none" />
        <button onClick={onTest} disabled={!url.trim() || !apiKey.trim() || status === 'testing'} className="w-full px-3 py-1.5 rounded-lg bg-gray-100 dark:bg-gray-700 text-xs font-medium flex items-center justify-center gap-1.5 disabled:opacity-50">
            {status === 'testing' && <Loader2 size={14} className="animate-spin" />}
            {status === 'ok' && <CheckCircle2 size={14} className="text-emerald-500" />}
            {status === 'error' && <XCircle size={14} className="text-red-500" />}
            {status === 'ok' ? 'Connected' : 'Test connection'}
        </button>
        {status === 'error' && <p className="text-[11px] text-red-500">Couldn't connect — check the URL and key.</p>}
        {status === 'ok' && webhookUrl && (
            <div className="pt-1">
                <p className="text-[11px] text-gray-500 mb-1">Webhook URL — add this in {label} → Settings → Connect:</p>
                <div className="flex items-center gap-1.5 bg-gray-50 dark:bg-gray-900/40 rounded px-2 py-1">
                    <code className="flex-1 text-[10px] font-mono truncate">{webhookUrl}</code>
                    <button onClick={() => navigator.clipboard.writeText(webhookUrl)} className="text-gray-400 hover:text-gray-600 flex-shrink-0"><Copy size={12} /></button>
                </div>
            </div>
        )}
    </div>
);

const StepArr = ({ onNext, onBack, onSkip, setArrConfiguredThisRun }) => {
    const [sonarrUrl, setSonarrUrl] = useState('http://localhost:8989');
    const [sonarrKey, setSonarrKey] = useState('');
    const [sonarrStatus, setSonarrStatus] = useState(null);

    const [radarrUrl, setRadarrUrl] = useState('http://localhost:7878');
    const [radarrKey, setRadarrKey] = useState('');
    const [radarrStatus, setRadarrStatus] = useState(null);

    const [webhooks, setWebhooks] = useState(null);
    const [saving, setSaving] = useState(false);

    const loadWebhookUrls = async () => {
        try {
            const res = await api.getFullHealth();
            setWebhooks(res.data?.webhooks || null);
        } catch {
            // non-critical
        }
    };

    const testSonarr = async () => {
        setSonarrStatus('testing');
        try {
            const res = await api.testSonarr({ url: sonarrUrl.trim(), api_key: sonarrKey.trim() });
            setSonarrStatus(res.data.connected ? 'ok' : 'error');
            if (res.data.connected) loadWebhookUrls();
        } catch {
            setSonarrStatus('error');
        }
    };

    const testRadarr = async () => {
        setRadarrStatus('testing');
        try {
            const res = await api.testRadarr({ url: radarrUrl.trim(), api_key: radarrKey.trim() });
            setRadarrStatus(res.data.connected ? 'ok' : 'error');
            if (res.data.connected) loadWebhookUrls();
        } catch {
            setRadarrStatus('error');
        }
    };

    const handleNext = async () => {
        setSaving(true);
        const payload = {};
        let configuredAny = false;
        if (sonarrStatus === 'ok') {
            payload.sonarr_url = sonarrUrl.trim();
            payload.sonarr_api_key = sonarrKey.trim();
            payload.sonarr_enabled = true;
            configuredAny = true;
        }
        if (radarrStatus === 'ok') {
            payload.radarr_url = radarrUrl.trim();
            payload.radarr_api_key = radarrKey.trim();
            payload.radarr_enabled = true;
            configuredAny = true;
        }
        if (configuredAny) {
            payload.arr_sync_interval = 60;
        }
        try {
            if (Object.keys(payload).length) await api.updateSettings(payload);
        } catch (e) {
            console.error('Failed to save Sonarr/Radarr settings', e);
        } finally {
            setSaving(false);
            setArrConfiguredThisRun(configuredAny);
            onNext();
        }
    };

    return (
        <div className="space-y-5">
            <div>
                <h2 className="text-lg font-bold text-gray-900 dark:text-white">Connect Sonarr &amp; Radarr</h2>
                <p className="text-sm text-gray-500 dark:text-gray-400">Optional — enables automatic discovery of new episodes/movies missing subtitles.</p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <ServiceBlock label="Sonarr" Icon={Tv} url={sonarrUrl} setUrl={setSonarrUrl} apiKey={sonarrKey} setApiKey={setSonarrKey} status={sonarrStatus} onTest={testSonarr} webhookUrl={webhooks?.sonarr_url} />
                <ServiceBlock label="Radarr" Icon={Film} url={radarrUrl} setUrl={setRadarrUrl} apiKey={radarrKey} setApiKey={setRadarrKey} status={radarrStatus} onTest={testRadarr} webhookUrl={webhooks?.radarr_url} />
            </div>

            <div className="flex items-center justify-between pt-2">
                <button onClick={onBack} disabled={!onBack} className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 disabled:opacity-0">Back</button>
                <div className="flex gap-3">
                    <button onClick={onSkip} className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">Skip</button>
                    <button onClick={handleNext} disabled={saving} className="px-5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-medium disabled:opacity-50">
                        {saving ? 'Saving...' : 'Continue'}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default StepArr;
