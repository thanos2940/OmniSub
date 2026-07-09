import React, { useState, useEffect } from 'react';
import { api } from '../api';
import { useToast } from '../context/ToastContext';
import { RefreshCw, Clock, CheckCircle2, Layers, Zap } from 'lucide-react';

const Card = ({ title, children }) => (
    <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm">
        <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">{title}</div>
        {children}
    </div>
);

const Dashboard = () => {
    const { addToast } = useToast();
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    const load = async () => {
        try {
            const res = await api.getDashboard();
            setData(res.data);
        } catch (e) {
            console.error('dashboard load failed', e);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
        const t = setInterval(load, 10000);
        return () => clearInterval(t);
    }, []);

    const handleTranslateAll = async () => {
        try {
            const res = await api.enqueueAllMissing();
            addToast({ type: 'success', message: `Enqueued ${res.data.enqueued_count} episode(s) to the backlog.` });
            load();
        } catch (e) {
            addToast({ type: 'error', message: 'Failed to enqueue backlog' });
        }
    };

    if (loading) return <div className="p-10 text-center text-gray-400"><RefreshCw className="w-6 h-6 animate-spin mx-auto" /></div>;
    if (!data) return <div className="p-10 text-center text-gray-400">No data.</div>;

    const lib = data.library;
    const eta = data.eta;
    const maxDay = Math.max(1, ...data.throughput.map(d => d.count));

    return (
        <div className="max-w-6xl mx-auto p-8 space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Dashboard</h1>
                    <p className="text-sm text-gray-500 dark:text-gray-400">Library progress, throughput, and backlog ETA.</p>
                </div>
                {lib.missing_episodes > 0 && (
                    <button onClick={handleTranslateAll} className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-sm font-bold">
                        <Zap size={16} /> Translate all missing ({lib.missing_episodes})
                    </button>
                )}
            </div>

            {/* Progress + ETA */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <Card title="Library progress">
                    <div className="text-2xl font-bold text-emerald-500">{lib.progress_pct}%</div>
                    <div className="w-full h-2 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden mt-2">
                        <div className="h-full bg-gradient-to-r from-indigo-500 to-emerald-500 rounded-full" style={{ width: `${lib.progress_pct}%` }} />
                    </div>
                    <div className="text-xs text-gray-400 mt-1">{lib.translated_episodes}/{lib.total_episodes} episodes</div>
                </Card>
                <Card title="Missing"><div className="text-2xl font-bold text-blue-500">{lib.missing_episodes}</div><div className="text-xs text-gray-400 mt-1">{lib.shows} shows · {lib.movies} movies</div></Card>
                <Card title="Backlog ETA">
                    <div className="text-2xl font-bold text-amber-500 flex items-center gap-2"><Clock size={20} />{eta.days == null ? '—' : (eta.days === 0 ? 'Done' : `~${eta.days}d`)}</div>
                    <div className="text-xs text-gray-400 mt-1">{eta.episodes_per_day}/day ({eta.source === 'observed' ? 'observed' : 'estimate'})</div>
                </Card>
                <Card title="Completed today"><div className="text-2xl font-bold text-emerald-500 flex items-center gap-2"><CheckCircle2 size={20} />{data.queue.summary.completed_today || 0}</div><div className="text-xs text-gray-400 mt-1">{data.queue.summary.running || 0} running · {data.queue.summary.pending || 0} pending</div></Card>
            </div>

            {/* Cost & tokens (v2 telemetry) */}
            {data.usage && data.usage.calls > 0 && (
                <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <Card title="Spend today (est.)"><div className="text-2xl font-bold text-rose-500">${(data.usage.cost_today_usd ?? 0).toFixed(2)}</div><div className="text-xs text-gray-400 mt-1">${(data.usage.cost_usd ?? 0).toFixed(2)} over {data.usage.window_days}d</div></Card>
                    <Card title="Cost / episode (est.)"><div className="text-2xl font-bold text-indigo-500">${(data.usage.avg_cost_per_episode ?? 0).toFixed(4)}</div><div className="text-xs text-gray-400 mt-1">{data.usage.avg_translation_calls_per_episode ?? 0} req/ep</div></Card>
                    <Card title="Tokens (30d)"><div className="text-2xl font-bold text-blue-500">{(((data.usage.prompt_tokens ?? 0) + (data.usage.output_tokens ?? 0)) / 1e6).toFixed(1)}M</div><div className="text-xs text-gray-400 mt-1">{((data.usage.output_tokens ?? 0) / 1e6).toFixed(1)}M out · {((data.usage.cached_tokens ?? 0) / 1e6).toFixed(1)}M cached</div></Card>
                    <Card title="Thinking tokens"><div className={`text-2xl font-bold ${(data.usage.thoughts_tokens ?? 0) > 0 ? 'text-amber-500' : 'text-emerald-500'}`}>{((data.usage.thoughts_tokens ?? 0) / 1e6).toFixed(2)}M</div><div className="text-xs text-gray-400 mt-1">{(data.usage.thoughts_tokens ?? 0) > 0 ? 'thinking is billing' : 'thinking off ✓'}</div></Card>
                </div>
            )}

            {/* Throughput */}
            <Card title="Throughput (last 14 days)">
                <div className="flex items-end gap-1.5 h-28 mt-2">
                    {data.throughput.length === 0 ? <span className="text-xs text-gray-400">No completions yet.</span> :
                        data.throughput.map((d, i) => (
                            <div key={i} className="flex-1 flex flex-col items-center justify-end group">
                                <div className="w-full bg-indigo-500/80 rounded-t hover:bg-indigo-500" style={{ height: `${(d.count / maxDay) * 100}%` }} title={`${d.date}: ${d.count}`} />
                                <span className="text-[9px] text-gray-400 mt-1">{d.date.slice(5)}</span>
                            </div>
                        ))}
                </div>
            </Card>

            {/* Per-model quota */}
            <Card title="Per-model daily quota">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-2">
                    {Object.entries(data.quota.all_models).map(([model, s]) => {
                        const pct = Math.min(100, (s.daily_count / s.daily_limit) * 100);
                        return (
                            <div key={model} className="space-y-1">
                                <div className="flex justify-between text-xs font-semibold"><span className="truncate max-w-[180px] text-gray-700 dark:text-gray-300">{model}</span><span className="text-gray-500">{s.daily_count}/{s.daily_limit}</span></div>
                                <div className="w-full h-2 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden"><div className={`h-full rounded-full ${pct > 90 ? 'bg-rose-500' : pct > 70 ? 'bg-amber-500' : 'bg-indigo-600'}`} style={{ width: `${pct}%` }} /></div>
                            </div>
                        );
                    })}
                </div>
            </Card>
        </div>
    );
};

export default Dashboard;
