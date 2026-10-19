import { Link } from 'react-router-dom';
import useRunnerStatus, { describeRunner } from './useRunnerStatus';

const DOT = {
    neutral: 'bg-gray-400',
    success: 'bg-emerald-500 animate-pulse',
    warning: 'bg-amber-500',
    danger: 'bg-rose-500',
    info: 'bg-sky-500',
    accent: 'bg-indigo-500',
};

// The top-bar pill: what is translating right now. Click opens Activity.
export default function RunnerPill() {
    const { info, engine, queue, loading } = useRunnerStatus({ intervalMs: 15000 });
    if (loading && !info && !queue) return null;
    const { label, tone } = describeRunner(info, engine, queue);
    const hint = info?.message || (engine === 'model' ? 'Gemini worker' : 'Translation runner');
    return (
        <Link
            to="/activity"
            title={`${hint} — open Activity`}
            className="inline-flex max-w-[11rem] items-center gap-2 rounded-full border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-750 sm:max-w-none"
        >
            <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[tone] || DOT.neutral}`} />
            <span className="truncate">{label}</span>
        </Link>
    );
}
