import React, { useState, useEffect } from 'react';
import { api } from '../api';
import { RefreshCw, CheckCircle2, XCircle, AlertTriangle, MinusCircle, Copy } from 'lucide-react';

const StatusIcon = ({ status }) => {
    if (status === 'pass') return <CheckCircle2 className="text-emerald-500" size={18} />;
    if (status === 'fail') return <XCircle className="text-rose-500" size={18} />;
    if (status === 'warn') return <AlertTriangle className="text-amber-500" size={18} />;
    return <MinusCircle className="text-gray-400" size={18} />;
};

const Row = ({ label, status, detail, hint }) => (
    <div className="flex items-start gap-3 py-3 border-b border-gray-100 dark:border-gray-800 last:border-0">
        <StatusIcon status={status} />
        <div className="flex-1 min-w-0">
            <div className="font-semibold text-gray-900 dark:text-white">{label}</div>
            {detail && <div className="text-sm text-gray-500 dark:text-gray-400 break-all">{detail}</div>}
            {hint && status !== 'pass' && <div className="text-xs text-amber-600 dark:text-amber-400 mt-0.5">{hint}</div>}
        </div>
    </div>
);

const HealthPage = () => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshCount, setRefreshCount] = useState(0);

    useEffect(() => {
        let active = true;
        const load = async () => {
            setLoading(true);
            try {
                const res = await api.getFullHealth();
                if (active) {
                    setData(res.data);
                }
            } catch (e) {
                console.error('health failed', e);
            } finally {
                if (active) {
                    setLoading(false);
                }
            }
        };
        load();
        return () => {
            active = false;
        };
    }, [refreshCount]);

    return (
        <div className="max-w-3xl mx-auto p-8 space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900 dark:text-white">System Health</h1>
                    {data && <p className={`text-sm font-semibold ${data.overall === 'pass' ? 'text-emerald-500' : data.overall === 'warn' ? 'text-amber-500' : 'text-rose-500'}`}>Overall: {data.overall.toUpperCase()}</p>}
                </div>
                <button onClick={() => setRefreshCount(c => c + 1)} className="flex items-center gap-1.5 text-sm font-semibold bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 px-3 py-1.5 rounded-lg shadow-sm">
                    <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Re-run checks
                </button>
            </div>

            {!data ? <div className="p-10 text-center text-gray-400"><RefreshCw className="w-6 h-6 animate-spin mx-auto" /></div> : (
                <div className="bg-white dark:bg-gray-850 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm p-5">
                    <Row label="Gemini API key" {...data.api_key} />
                    <Row label="Sonarr" status={data.sonarr.status} detail={data.sonarr.detail} hint={data.sonarr.hint} />
                    <Row label="Radarr" status={data.radarr.status} detail={data.radarr.detail} hint={data.radarr.hint} />
                    <Row label="Media write access" {...data.paths.write_test} />
                    {(data.paths.mappings || []).map((m, i) => (
                        <Row key={i} label={`Path mapping: ${m.remote || '(any)'} → ${m.local}`} status={m.status} hint={m.hint} />
                    ))}
                    <Row label="Background worker" status={data.worker.status}
                         detail={`${data.worker.running ? 'running' : 'stopped'}${data.worker.paused ? ' (paused)' : ''} · ${data.worker.queue?.pending || 0} pending, ${data.worker.queue?.running || 0} running`}
                         hint={data.worker.hint} />
                    <Row label="Disk space" {...data.disk} />
                    <div className="flex items-start gap-3 py-3">
                        <StatusIcon status={data.webhooks.status} />
                        <div className="flex-1">
                            <div className="font-semibold text-gray-900 dark:text-white">Webhooks {data.webhooks.secured ? '(secured)' : '(blocked — no secret set)'}</div>
                            {!data.webhooks.secured && <div className="text-xs text-amber-600 dark:text-amber-400">{data.webhooks.hint}</div>}
                            <div className="mt-2 space-y-1 font-mono text-xs text-gray-500 dark:text-gray-400">
                                {['sonarr_url', 'radarr_url'].map(k => {
                                    const getWebhookUrl = (path) => {
                                        const origin = window.location.origin;
                                        if (import.meta.env.DEV) {
                                            return origin.replace(/:\d+$/, ':8000') + path;
                                        }
                                        return origin + path;
                                    };
                                    return (
                                        <div key={k} className="flex items-center gap-2 min-w-0">
                                            <span className="truncate">{data.webhooks[k]}</span>
                                            <button onClick={() => navigator.clipboard.writeText(getWebhookUrl(data.webhooks[k]))} className="text-indigo-500 hover:text-indigo-600"><Copy size={12} /></button>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default HealthPage;
