import { STATE_ORDER, STATE_LABEL } from './autopilotMeta';

// One tile per state; picking a tile filters the items below to that state.
const Counters = ({ counts = {}, active, onPick }) => (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {STATE_ORDER.map(s => {
            const n = counts[s] || 0;
            return (
                <button
                    key={s}
                    type="button"
                    onClick={() => onPick(active === s ? '' : s)}
                    aria-pressed={active === s}
                    title={`Show ${STATE_LABEL[s].toLowerCase()} items`}
                    className={`rounded-xl border bg-white p-3 text-left transition-colors hover:bg-gray-50 dark:bg-gray-850 dark:hover:bg-gray-800 ${active === s ? 'border-indigo-400 dark:border-indigo-500' : 'border-gray-200 dark:border-gray-750'} ${n === 0 ? 'opacity-60' : ''}`}
                >
                    <div className="text-xl font-semibold tabular-nums text-gray-900 dark:text-white">{n}</div>
                    <div className="mt-0.5 truncate text-xs text-gray-500 dark:text-gray-400">{STATE_LABEL[s]}</div>
                </button>
            );
        })}
    </div>
);

export default Counters;
