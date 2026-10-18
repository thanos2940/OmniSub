import { STATE_ORDER, STATE_LABEL, STATE_STYLE } from './autopilotMeta';

const Counters = ({ counts = {}, active, onPick }) => (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        {STATE_ORDER.map(s => {
            const n = counts[s] || 0;
            return (
                <button
                    key={s} type="button" onClick={() => onPick(active === s ? '' : s)}
                    title={`Show ${STATE_LABEL[s].toLowerCase()} items`}
                    className={`text-left p-2.5 rounded-xl border bg-white dark:bg-gray-850 transition-colors hover:bg-gray-50 dark:hover:bg-gray-800 ${active === s ? 'border-indigo-400 dark:border-indigo-500' : 'border-gray-200 dark:border-gray-800'} ${n === 0 ? 'opacity-60' : ''}`}
                >
                    <div className="text-xl font-bold tabular-nums text-gray-900 dark:text-white">{n}</div>
                    <span className={`inline-block mt-0.5 px-1.5 py-0.5 rounded-full text-[10px] font-bold border ${STATE_STYLE[s]}`}>{STATE_LABEL[s]}</span>
                </button>
            );
        })}
    </div>
);

export default Counters;
