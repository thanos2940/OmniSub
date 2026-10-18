import React, { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useToast } from '../context/ToastContext';
import { useJobs } from '../context/JobContext';
import { formatEta } from '../utils/formatters';
import AutopilotCard from './autopilot/AutopilotCard';
import { RefreshCw, Clock, CheckCircle2, Layers, Zap, Activity, ChevronRight, ExternalLink } from 'lucide-react';

const Card = ({ title, children }) => (
    <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm">
        <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">{title}</div>
        {children}
    </div>
);

const Dashboard = () => {
    const toast = useToast();
    const { activeJobs, cancelJob } = useJobs();
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    const inFlightJobs = useMemo(() => {
        if (!activeJobs) return [];
        return Object.values(activeJobs).filter(job =>
            job &&
            !job.parentId &&
            !job.parent_job_id &&
            ['running', 'pending'].includes(job.status) &&
            (job.project_name || job.episode_name || job.type === 'translation' || job.type === 'auto_translate' || job.type === 'simple_pipeline' || job.type === 'translate_episode' || (job.id && job.id.startsWith('bg_')))
        );
    }, [activeJobs]);

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
        const t = setInterval(() => { if (!document.hidden) load(); }, 10000);
        return () => clearInterval(t);
    }, []);

    const handleTranslateAll = async () => {
        try {
            const res = await api.enqueueAllMissing();
            toast.success(`Enqueued ${res.data.enqueued_count} episode(s) to the backlog.`);
            load();
        } catch (e) {
            toast.error('Failed to enqueue backlog');
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

            <AutopilotCard />

            {/* In-Flight Translations Widget */}
            {inFlightJobs.length > 0 ? (
                <div className="bg-white dark:bg-gray-850 rounded-2xl p-6 border border-indigo-100 dark:border-indigo-900/40 shadow-sm space-y-4">
                    {/* Header Banner */}
                    <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-800 pb-3">
                        <div className="flex items-center gap-2.5">
                            <div className="relative flex items-center justify-center">
                                <Activity className="text-indigo-600 dark:text-indigo-400 animate-pulse" size={20} />
                                <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-indigo-500 animate-ping" />
                            </div>
                            <h3 className="text-base font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                <span>In-Flight Translations</span>
                                <span className="px-2 py-0.5 rounded-full text-xs font-bold bg-indigo-50 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300">
                                    {inFlightJobs.length} Active
                                </span>
                            </h3>
                        </div>
                        <Link
                            to="/queue"
                            className="text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 flex items-center gap-1 group"
                        >
                            <span>View Full Queue</span>
                            <ChevronRight size={14} className="group-hover:translate-x-0.5 transition-transform" />
                        </Link>
                    </div>

                    {/* Active Cards Grid */}
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {inFlightJobs.map(job => {
                            const projName = job.project_name || job.metadata?.projectId || 'Unknown Project';
                            const epName = job.episode_name || job.metadata?.episodeName || job.description || job.id;
                            const isRunning = job.status === 'running';

                            return (
                                <div
                                    key={job.id}
                                    className="p-4 rounded-xl border border-gray-100 dark:border-gray-750 bg-gray-50/60 dark:bg-gray-800/40 space-y-3 flex flex-col justify-between"
                                >
                                    {/* Title & Status Row */}
                                    <div>
                                        <div className="flex items-start justify-between gap-2">
                                            <Link
                                                to={`/project/${encodeURIComponent(projName)}`}
                                                className="font-bold text-sm text-gray-900 dark:text-white hover:text-indigo-600 dark:hover:text-indigo-400 line-clamp-1 flex items-center gap-1"
                                                title={projName}
                                            >
                                                <span>{projName}</span>
                                                <ExternalLink size={12} className="opacity-50" />
                                            </Link>

                                            <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold flex items-center gap-1 ${
                                                isRunning
                                                    ? 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400 animate-pulse'
                                                    : 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400'
                                            }`}>
                                                {isRunning ? <Activity size={10} /> : <Clock size={10} />}
                                                {isRunning ? 'Running' : 'Queued'}
                                            </span>
                                        </div>

                                        <div className="text-xs text-gray-500 dark:text-gray-400 truncate mt-0.5" title={epName}>
                                            {epName}
                                        </div>
                                    </div>

                                    {/* Dual-Line Progress Bar */}
                                    <div className="space-y-1.5">
                                        {/* Line 1: Track + Percentage */}
                                        <div className="flex items-center justify-between text-xs font-semibold">
                                            <span className="text-gray-500 dark:text-gray-400">Progress</span>
                                            <span className="text-gray-800 dark:text-gray-200 tabular-nums">
                                                {Math.round(job.progress || 0)}%
                                            </span>
                                        </div>
                                        <div className="w-full h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden relative">
                                            <div
                                                className="h-full bg-gradient-to-r from-amber-500 via-indigo-500 to-indigo-600 rounded-full transition-all duration-500 relative"
                                                style={{ width: `${Math.max(3, Math.min(100, job.progress || 0))}%` }}
                                            >
                                                {isRunning && <div className="absolute inset-0 bg-white/25 animate-pulse" />}
                                            </div>
                                        </div>

                                        {/* Line 2: Telemetry Metrics */}
                                        <div className="flex items-center justify-between text-[11px] text-gray-500 dark:text-gray-400">
                                            <span className="truncate max-w-[130px]">
                                                {job.scenes_total > 0
                                                    ? `Scene ${job.scenes_done || 0}/${job.scenes_total}`
                                                    : ''}
                                                {job.scenes_total > 0 && job.cues_total > 0 ? ' · ' : ''}
                                                {job.cues_total > 0 ? `${job.cues_translated || 0}/${job.cues_total} cues` : ''}
                                            </span>
                                            {job.eta_seconds != null && (
                                                <span className="text-indigo-600 dark:text-indigo-400 font-semibold flex items-center gap-0.5 shrink-0">
                                                    <Clock size={10} />
                                                    ETA: {formatEta(job.eta_seconds)}
                                                </span>
                                            )}
                                        </div>
                                    </div>

                                    {/* Card Footer Actions */}
                                    <div className="pt-2 border-t border-gray-200 dark:border-gray-750 flex items-center justify-between text-xs">
                                        <Link
                                            to={`/project/${encodeURIComponent(projName)}`}
                                            className="text-indigo-600 dark:text-indigo-400 hover:underline font-medium"
                                        >
                                            Open Project
                                        </Link>

                                        <button
                                            type="button"
                                            onClick={() => cancelJob(job.id)}
                                            className="text-rose-500 hover:text-rose-700 dark:hover:text-rose-400 font-medium transition-colors"
                                        >
                                            Cancel
                                        </button>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            ) : (
                /* Sleek Idle Banner when no in-flight translations */
                <div className="bg-white dark:bg-gray-850 rounded-2xl p-4 border border-gray-200 dark:border-gray-800 shadow-sm flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
                    <div className="flex items-center gap-2">
                        <CheckCircle2 size={16} className="text-emerald-500 shrink-0" />
                        <span>Translation queue is idle. All queued tasks are complete.</span>
                    </div>
                    <Link to="/queue" className="text-indigo-600 dark:text-indigo-400 font-semibold hover:underline flex items-center gap-1">
                        <span>View Queue History</span>
                        <ChevronRight size={12} />
                    </Link>
                </div>
            )}

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
