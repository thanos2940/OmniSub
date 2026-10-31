import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ChevronDown } from 'lucide-react';
import LiveWorkList from './LiveWorkList';
import QueueLogsModal from '../queue/QueueLogsModal';
import useRunnerStatus, { describeRunner } from './useRunnerStatus';
import { useLiveWork } from './useLiveWork';

const DOT = {
    neutral: 'bg-gray-400',
    success: 'bg-emerald-500 animate-pulse',
    warning: 'bg-amber-500',
    danger: 'bg-rose-500',
    info: 'bg-sky-500',
    accent: 'bg-indigo-500',
};

// The top bar's view of live work: what the translation runner (or the Gemini worker) is doing, how many
// background jobs run beside it, and on click a panel listing all of it with Cancel, Put back and Log, and a
// way into Activity. It reads the activity snapshot JobContext already polls: no request of its own.
export default function ActivityIndicator() {
    const { info, engine, queue, loading } = useRunnerStatus();
    const live = useLiveWork();
    const [open, setOpen] = useState(false);
    const box = useRef(null);
    const location = useLocation();
    const [lastPath, setLastPath] = useState(location.pathname);
    if (lastPath !== location.pathname) {   // a page change closes the panel
        setLastPath(location.pathname);
        setOpen(false);
    }

    useEffect(() => {
        if (!open) return undefined;
        const onDown = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
        const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onDown);
            document.removeEventListener('keydown', onKey);
        };
    }, [open]);

    if (loading && !info && !queue) return null;
    const { label, tone } = describeRunner(info, engine, queue);
    const background = live.jobs.length;
    const failed = live.finished.some(j => j.status === 'failed');
    const summary = live.snapshot?.summary || {};
    const waiting = summary.pending || 0;
    const message = info?.message || (engine === 'model' ? 'Translating through the Gemini API.' : '');
    const close = () => setOpen(false);
    const pillTitle = [label, background ? `${background} background job${background === 1 ? '' : 's'}` : '', failed ? 'a job failed' : '']
        .filter(Boolean).join(' · ');

    return (
        <div ref={box} className="relative">
            <button
                type="button"
                onClick={() => setOpen(o => !o)}
                aria-expanded={open}
                aria-haspopup="dialog"
                aria-controls="live-work-panel"
                aria-label={`${pillTitle}. Show what is running`}
                title={`${pillTitle}. Show what is running`}
                className="inline-flex max-w-[13rem] items-center gap-2 rounded-full border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-750 sm:max-w-none"
            >
                <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[tone] || DOT.neutral}`} />
                <span className="truncate">{label}</span>
                {background > 0 && (
                    <span className="rounded-full bg-indigo-100 px-1.5 text-[10px] font-semibold tabular-nums text-indigo-700 dark:bg-indigo-900/60 dark:text-indigo-200">
                        +{background}
                    </span>
                )}
                {failed && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500" aria-hidden="true" />}
                <ChevronDown size={12} className={`shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
            </button>

            {open && (
                <div
                    id="live-work-panel"
                    role="dialog"
                    aria-label="What is running"
                    className="fixed inset-x-2 top-14 z-50 max-h-[75vh] overflow-y-auto rounded-xl border border-gray-200 bg-white p-4 shadow-xl dark:border-gray-700 dark:bg-gray-850 sm:absolute sm:inset-x-auto sm:right-0 sm:top-full sm:mt-2 sm:w-[24rem]"
                >
                    <div className="flex items-start justify-between gap-3 border-b border-gray-100 pb-2.5 dark:border-gray-800">
                        <div className="min-w-0">
                            <p className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
                                <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[tone] || DOT.neutral}`} />
                                <span className="truncate">{label}</span>
                            </p>
                            {message && <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">{message}</p>}
                        </div>
                        <Link to="/activity" onClick={close} className="shrink-0 text-xs font-medium text-indigo-600 hover:underline dark:text-indigo-400">
                            Open Activity
                        </Link>
                    </div>
                    <LiveWorkList
                        compact
                        rows={live.rows}
                        jobs={live.jobs}
                        finished={live.finished}
                        onCancelRow={live.cancelRow}
                        onReleaseRow={live.releaseRow}
                        onCancelJob={live.cancelJob}
                        onDismissJob={live.dismissJob}
                        onLogs={live.showLogs}
                        onNavigate={close}
                    />
                    <p className="mt-2 border-t border-gray-100 pt-2.5 text-xs text-gray-500 dark:border-gray-800 dark:text-gray-400">
                        {waiting ? `${waiting} waiting in the queue` : 'Nothing waiting in the queue'}
                        {summary.completed_today ? ` · ${summary.completed_today} done today` : ''}
                        {summary.failed ? ` · ${summary.failed} failed` : ''}
                    </p>
                </div>
            )}
            <QueueLogsModal logs={live.logs} onClose={live.closeLogs} />
        </div>
    );
}
