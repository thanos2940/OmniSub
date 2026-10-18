import React from 'react';
import { Activity, Pause } from 'lucide-react';

const QueueStats = ({ summary = {}, rateLimits = {} }) => {
    return (
        <div className="space-y-4">
            {/* Summary Cards */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm">
                    <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Running</div>
                    <div className="text-2xl font-bold text-amber-500 mt-1 flex items-center gap-2">
                        {summary.running || 0}
                        {summary.running > 0 && <Activity className="text-amber-500 animate-pulse" size={20} />}
                    </div>
                </div>
                <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm">
                    <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Pending</div>
                    <div className="text-2xl font-bold text-blue-500 mt-1">{summary.pending || 0}</div>
                </div>
                <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm">
                    <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Completed Today</div>
                    <div className="text-2xl font-bold text-emerald-500 mt-1">{summary.completed_today || 0}</div>
                </div>
                <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm">
                    <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Failed</div>
                    <div className="text-2xl font-bold text-rose-500 mt-1">{summary.failed || 0}</div>
                </div>
                <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm col-span-2 md:col-span-1">
                    <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Rate Paused</div>
                    <div className="text-2xl font-bold text-amber-600 mt-1">{summary.paused_daily_limit || 0}</div>
                </div>
            </div>

            {/* Rate Limits Gauges */}
            {Object.keys(rateLimits).length > 0 && (
                <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm space-y-4">
                    <h3 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">Per-Model Daily Quotas (RPD)</h3>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {Object.entries(rateLimits).map(([model, stats]) => {
                            const pct = Math.min(100, (stats.daily_count / stats.daily_limit) * 100);
                            const isExhausted = stats.daily_exhausted;
                            return (
                                <div key={model} className="space-y-1.5">
                                    <div className="flex justify-between text-xs font-semibold">
                                        <span className="text-gray-700 dark:text-gray-300 truncate max-w-[180px]">{model}</span>
                                        <span className={isExhausted ? "text-rose-500 font-bold" : "text-gray-500"}>
                                            {stats.daily_count} / {stats.daily_limit} RPD
                                        </span>
                                    </div>
                                    <div className="w-full bg-gray-100 dark:bg-gray-850 border border-gray-200 dark:border-gray-750 h-2.5 rounded-full overflow-hidden">
                                        <div 
                                            className={`h-full rounded-full transition-all duration-500 ${
                                                isExhausted 
                                                    ? 'bg-rose-500' 
                                                    : pct > 80 
                                                        ? 'bg-amber-500' 
                                                        : 'bg-indigo-600'
                                            }`}
                                            style={{ width: `${pct}%` }}
                                        ></div>
                                    </div>
                                    <div className="flex justify-between text-[10px] text-gray-400">
                                        <span>RPM Limit: {stats.tokens_available} tokens left</span>
                                        {stats.is_backing_off && (
                                            <span className="text-amber-500 animate-pulse">Backing off: {stats.backoff_remaining}s</span>
                                        )}
                                    </div>
                                    {isExhausted && (
                                        <div className="flex items-center gap-1.5 text-[10px] font-semibold text-rose-500">
                                            <Pause size={11} />
                                            Daily limit reached (detected from API).
                                            {stats.next_probe_in != null && (
                                                <span className="text-gray-400 font-normal">
                                                    Auto-recheck in {Math.ceil(stats.next_probe_in / 60)}m.
                                                </span>
                                            )}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}
        </div>
    );
};

export default QueueStats;
