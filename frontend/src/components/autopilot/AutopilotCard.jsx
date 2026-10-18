import { Link } from 'react-router-dom';
import { Plane, ChevronRight } from 'lucide-react';
import useAutopilotStatus from './useAutopilotStatus';
import UpdateNotice from './UpdateNotice';

const Stat = ({ label, value, tone }) => (
    <div>
        <div className={`text-xl font-bold tabular-nums ${value > 0 ? tone : 'text-gray-400 dark:text-gray-500'}`}>{value}</div>
        <div className="text-[11px] text-gray-400">{label}</div>
    </div>
);

const arrText = (name, a) => {
    if (!a || !a.configured) return null;
    return (
        <span key={name} title={a.ok ? 'Connected' : (a.error || 'Unreachable')}
            className={`inline-flex items-center gap-1 text-[11px] font-semibold ${a.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}`}>
            <span className="w-1.5 h-1.5 rounded-full bg-current" />{name}
        </span>
    );
};

// Compact status card for the Dashboard.
const AutopilotCard = () => {
    const { status, unsupported, error } = useAutopilotStatus(10000);

    if (unsupported) return <UpdateNotice compact />;
    if (!status) {
        return error ? (
            <div className="text-xs text-gray-400 bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl">Autopilot status unavailable: {error}</div>
        ) : null;
    }

    const c = status.counts || {};
    const enabled = !!status.enabled;
    const arrs = [arrText('Sonarr', status.arr?.sonarr), arrText('Radarr', status.arr?.radarr)].filter(Boolean);

    return (
        <div className="bg-white dark:bg-gray-850 p-4 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm">
            <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
                <div className="flex items-center gap-2.5 mr-auto">
                    <Plane size={20} className={enabled ? 'text-emerald-500' : 'text-gray-400'} />
                    <div>
                        <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Autopilot</div>
                        <div className="flex items-center gap-3 mt-0.5">
                            <span className={`text-sm font-bold ${enabled ? 'text-emerald-600 dark:text-emerald-400' : 'text-gray-500'}`}>{enabled ? 'On' : 'Off'}</span>
                            {enabled && arrs}
                        </div>
                    </div>
                </div>
                {enabled ? (
                    <div className="flex items-center gap-6">
                        <Stat label="waiting" value={c.waiting || 0} tone="text-amber-500" />
                        <Stat label="translating" value={(c.translating || 0) + (c.extracting || 0)} tone="text-purple-500" />
                        <Stat label="failed" value={c.failed || 0} tone="text-rose-500" />
                    </div>
                ) : (
                    <span className="text-xs text-gray-500 dark:text-gray-400">Not watching Sonarr/Radarr. Switch it on to get Greek subtitles automatically.</span>
                )}
                <Link to="/autopilot" className="text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:underline flex items-center gap-1">
                    <span>{enabled ? 'Open Autopilot' : 'Set up Autopilot'}</span>
                    <ChevronRight size={12} />
                </Link>
            </div>
        </div>
    );
};

export default AutopilotCard;
