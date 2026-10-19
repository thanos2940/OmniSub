import { Clock, HardDrive, Layers, Pause, Zap } from 'lucide-react';
import { Button, Card, ProgressBar, Stat, StatusChip } from '../ui';

// Queue counts, the side lanes (extraction, export retries) and, for the Gemini model engine only,
// the per-model daily quotas.
export default function QueueStats({ summary = {}, rateLimits = {}, engine = 'agent', onCheckDailyLimit, checkingLimit = false }) {
    const postponed = summary.postponed_count || summary.postponed || 0;
    const extraction = summary.extraction_count || 0;
    const exportRetry = summary.export_retry_count || 0;
    const ratePaused = summary.paused_daily_limit || 0;
    const showQuota = engine === 'model' && Object.keys(rateLimits).length > 0;
    const anyExhausted = Object.values(rateLimits).some(s => s.daily_exhausted) || ratePaused > 0;

    return (
        <div className="space-y-4">
            <Card>
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
                    <Stat label="Running" value={summary.running || 0} tone={summary.running ? 'accent' : 'neutral'} />
                    <Stat label="Waiting" value={summary.pending || 0} />
                    <Stat label="Postponed" value={postponed} tone={postponed ? 'warning' : 'neutral'} />
                    <Stat label="Done today" value={summary.completed_today || 0} tone="success" />
                    <Stat label="Failed" value={summary.failed || 0} tone={summary.failed ? 'danger' : 'neutral'} />
                    {engine === 'model' && <Stat label="Out of quota" value={ratePaused} tone={ratePaused ? 'warning' : 'neutral'} />}
                </div>
                {(extraction > 0 || exportRetry > 0) && (
                    <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-gray-100 pt-3 text-xs text-gray-500 dark:border-gray-750 dark:text-gray-400">
                        <span>Also running:</span>
                        {extraction > 0 && <StatusChip tone="info" icon={Layers}>{extraction} subtitle extraction{extraction > 1 ? 's' : ''}</StatusChip>}
                        {exportRetry > 0 && <StatusChip tone="warning" icon={HardDrive}>{exportRetry} export retr{exportRetry > 1 ? 'ies' : 'y'}</StatusChip>}
                    </div>
                )}
            </Card>

            {showQuota && (
                <Card
                    title="Gemini daily quota per model"
                    actions={anyExhausted && onCheckDailyLimit && (
                        <Button size="sm" icon={Zap} loading={checkingLimit} onClick={onCheckDailyLimit} title="Ask the API now whether the daily quota has reset">
                            Check the limit
                        </Button>
                    )}
                >
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                        {Object.entries(rateLimits).map(([model, stats]) => {
                            const pct = stats.daily_limit ? Math.min(100, (stats.daily_count / stats.daily_limit) * 100) : 0;
                            return (
                                <div key={model} className="space-y-1.5">
                                    <div className="flex justify-between gap-2 text-xs font-medium">
                                        <span className="truncate text-gray-700 dark:text-gray-300">{model}</span>
                                        <span className={stats.daily_exhausted ? 'font-semibold text-rose-500' : 'text-gray-500'}>{stats.daily_count} / {stats.daily_limit} per day</span>
                                    </div>
                                    <ProgressBar value={stats.daily_count} max={stats.daily_limit} tone={stats.daily_exhausted ? 'danger' : pct > 80 ? 'warning' : 'accent'} />
                                    {stats.is_backing_off && (
                                        <div className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400"><Clock size={11} /> Backing off for {stats.backoff_remaining}s</div>
                                    )}
                                    {stats.daily_exhausted && (
                                        <div className="flex items-center gap-1 text-xs font-medium text-rose-500">
                                            <Pause size={11} /> Daily limit reached.
                                            {stats.next_probe_in != null && <span className="font-normal text-gray-500">Checking again in {Math.ceil(stats.next_probe_in / 60)} min.</span>}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </Card>
            )}
        </div>
    );
}
