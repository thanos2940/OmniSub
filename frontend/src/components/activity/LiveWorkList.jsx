import { Link } from 'react-router-dom';
import { AlertTriangle, CheckCircle2, CornerUpLeft, Loader2, Terminal, X, XCircle } from 'lucide-react';
import { IconButton, ProgressBar } from '../ui';
import { jobTitle } from './liveWork';

const muted = 'text-xs text-gray-500 dark:text-gray-400';

function Row({ icon: Icon, iconClass, title, subtitle, link, onNavigate, progress, actions }) {
    return (
        <li className="flex items-start gap-2.5 py-2.5">
            <Icon size={16} className={`mt-0.5 shrink-0 ${iconClass}`} aria-hidden="true" />
            <div className="min-w-0 flex-1">
                {link
                    ? <Link to={link} onClick={onNavigate} className="block truncate text-sm font-medium text-gray-900 hover:text-indigo-600 dark:text-white dark:hover:text-indigo-400">{title}</Link>
                    : <span className="block truncate text-sm font-medium text-gray-900 dark:text-white">{title}</span>}
                {subtitle && <p className={`truncate ${muted}`} title={subtitle}>{subtitle}</p>}
                {progress != null && (
                    <div className="mt-1.5 flex items-center gap-2">
                        <ProgressBar value={Math.max(2, progress)} />
                        <span className={`w-9 shrink-0 text-right tabular-nums ${muted}`}>{Math.round(progress)}%</span>
                    </div>
                )}
            </div>
            {actions && <div className="-mr-1 flex shrink-0 items-center">{actions}</div>}
        </li>
    );
}

// The live work: episodes being translated or extracted (queue rows), background jobs, and jobs that finished a
// moment ago. Used by the top-bar indicator (``compact``) and the Activity page.
export default function LiveWorkList({
    rows = [], jobs = [], finished = [], onCancelRow, onReleaseRow, onCancelJob, onDismissJob, onLogs, onNavigate,
    compact = false, emptyText = 'Nothing is running right now.',
}) {
    if (!rows.length && !jobs.length && !finished.length) {
        return <p className={`py-3 ${compact ? 'text-sm' : ''} text-gray-500 dark:text-gray-400`}>{emptyText}</p>;
    }
    const heading = 'pt-2 text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400';
    return (
        <div>
            {rows.length > 0 && (
                <section aria-label="Episodes in progress">
                    {compact && <h3 className={heading}>Episodes</h3>}
                    <ul className="divide-y divide-gray-100 dark:divide-gray-800">
                        {rows.map(r => {
                            const title = r.episode || r.project || 'Queue item';
                            const where = r.episode && r.project ? r.project : '';
                            const units = r.unitsTotal ? `${r.unitsDone ?? 0}/${r.unitsTotal} lines` : '';
                            const subtitle = [where, r.orphaned ? 'Nothing is working on it' : r.message, r.orphaned ? '' : units]
                                .filter(Boolean).join(' · ');
                            return (
                                <Row
                                    key={r.id}
                                    icon={r.orphaned ? AlertTriangle : Loader2}
                                    iconClass={r.orphaned ? 'text-amber-500' : 'animate-spin text-indigo-500'}
                                    title={title}
                                    subtitle={subtitle}
                                    link={r.link}
                                    onNavigate={onNavigate}
                                    progress={r.orphaned ? null : r.progress}
                                    actions={(
                                        <>
                                            {r.orphaned && onReleaseRow && (
                                                <IconButton icon={CornerUpLeft} label="Put back in the queue" onClick={() => onReleaseRow(r.id)} />
                                            )}
                                            {onCancelRow && (
                                                <IconButton icon={X} label={`Cancel ${title}`} className="hover:text-rose-600 dark:hover:text-rose-400" onClick={() => onCancelRow(r.id)} />
                                            )}
                                        </>
                                    )}
                                />
                            );
                        })}
                    </ul>
                </section>
            )}
            {jobs.length > 0 && (
                <section aria-label="Background jobs">
                    {(compact || rows.length > 0) && <h3 className={heading}>Background jobs</h3>}
                    <ul className="divide-y divide-gray-100 dark:divide-gray-800">
                        {jobs.map(job => (
                            <Row
                                key={job.id}
                                icon={Loader2}
                                iconClass="animate-spin text-indigo-500"
                                title={jobTitle(job)}
                                subtitle={job.message}
                                link={job.metadata?.link}
                                onNavigate={onNavigate}
                                progress={job.status === 'pending' ? null : job.progress}
                                actions={(
                                    <>
                                        {onLogs && <IconButton icon={Terminal} label="Log" onClick={() => onLogs(job)} />}
                                        {onCancelJob && (
                                            <IconButton icon={X} label={`Cancel ${jobTitle(job)}`} className="hover:text-rose-600 dark:hover:text-rose-400" onClick={() => onCancelJob(job.id)} />
                                        )}
                                    </>
                                )}
                            />
                        ))}
                    </ul>
                </section>
            )}
            {finished.length > 0 && (
                <section aria-label="Finished just now">
                    <h3 className={heading}>Finished</h3>
                    <ul className="divide-y divide-gray-100 dark:divide-gray-800">
                        {finished.map(job => {
                            const ok = job.status === 'completed';
                            return (
                                <Row
                                    key={job.id}
                                    icon={ok ? CheckCircle2 : XCircle}
                                    iconClass={ok ? 'text-emerald-500' : job.status === 'failed' ? 'text-rose-500' : 'text-gray-400'}
                                    title={jobTitle(job)}
                                    subtitle={job.message || (ok ? 'Done' : job.status === 'failed' ? 'Failed' : 'Cancelled')}
                                    link={job.metadata?.link}
                                    onNavigate={onNavigate}
                                    actions={(
                                        <>
                                            {onLogs && <IconButton icon={Terminal} label="Log" onClick={() => onLogs(job)} />}
                                            {onDismissJob && <IconButton icon={X} label="Dismiss" onClick={() => onDismissJob(job.id)} />}
                                        </>
                                    )}
                                />
                            );
                        })}
                    </ul>
                </section>
            )}
        </div>
    );
}
