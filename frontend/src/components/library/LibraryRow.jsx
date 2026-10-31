import { memo } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Film, Tv, Folder, Check, Clock, Loader2, Eye, EyeOff, Languages, Search } from 'lucide-react';
import { Button, OverflowMenu, ProgressBar, StatusChip } from '../ui';
import { PRIORITY_OPTIONS, isMovieItem, itemPriority } from './libraryUtils';

// Status chip for a title from its counts and what the queue holds for it.
function statusFor(item, queued, running) {
    const total = item.episode_count;
    const done = item.translated_count || 0;
    if (itemPriority(item) === 'ignored') return { tone: 'neutral', icon: EyeOff, label: 'Ignored' };
    if (running > 0) return { tone: 'accent', icon: Loader2, label: 'Translating', spin: true };
    if (queued > 0) return { tone: 'info', icon: Clock, label: `${queued} queued` };
    if (total == null) return null;
    if (total === 0) return { tone: 'neutral', icon: Clock, label: 'No subtitles yet' };
    if (done >= total) return { tone: 'success', icon: Check, label: 'Complete' };
    if (done > 0) return { tone: 'warning', icon: Languages, label: 'Partly translated' };
    return { tone: 'neutral', icon: Clock, label: 'Not started' };
}

// Memoized: the page re-renders with every activity snapshot, a row only when its own title, counts or
// selection change (the handlers passed in are stable).
function LibraryRow({
    item, queued = 0, running = 0, selected = false, onToggle, onPriorityChange,
    onTranslateMissing, translating = false, onInspectTracks, onExtract,
}) {
    const navigate = useNavigate();
    const movie = isMovieItem(item);
    const manual = !item.arr_source;
    const parent = item.type === 'parent';
    const Icon = parent ? Folder : movie ? Film : Tv;
    const priority = itemPriority(item);
    const ignored = priority === 'ignored';
    const status = statusFor(item, queued, running);
    const total = item.episode_count;
    const done = item.translated_count || 0;
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    const title = item.show_name || item.name;
    const open = () => navigate(`/project/${encodeURIComponent(item.name)}`);
    const menuItems = [
        { label: 'Stop ignoring', icon: Eye, hidden: !ignored, onClick: () => onPriorityChange(item.name, 'normal') },
        { label: 'Inspect tracks in the video', icon: Search, hidden: !movie || manual || ignored || !onInspectTracks, onClick: () => onInspectTracks(item) },
        { label: 'Extract subtitles from videos', icon: Film, hidden: manual || ignored || !onExtract, onClick: () => onExtract(item) },
    ];

    return (
        <li className={`flex flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3 sm:px-5 ${ignored ? 'opacity-70' : ''} ${selected ? 'bg-indigo-50/60 dark:bg-indigo-950/20' : ''}`}>
            <input
                type="checkbox"
                checked={selected}
                onChange={() => onToggle(item.name)}
                className="h-4 w-4 shrink-0 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 dark:border-gray-600"
                aria-label={`Select ${title}`}
            />
            <Icon size={20} className="shrink-0 text-gray-400" aria-hidden="true" />

            <div className="min-w-0 flex-1 basis-48">
                <Link to={`/project/${encodeURIComponent(item.name)}`} className="block truncate text-sm font-semibold text-gray-900 hover:text-indigo-600 dark:text-white dark:hover:text-indigo-400">
                    {title}
                </Link>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-3 text-xs text-gray-500 dark:text-gray-400">
                    {item.target_language && <span>to {item.target_language}</span>}
                    {!movie && total != null && !parent && <span>{total} episode{total === 1 ? '' : 's'}</span>}
                    {parent && <span>Groups other projects</span>}
                    {item.parent_project && <span>in {item.parent_project}</span>}
                    {item.arr_last_sync && <span>synced {new Date(item.arr_last_sync).toLocaleDateString()}</span>}
                </div>
            </div>

            <div className="flex shrink-0 items-center gap-2">
                {status && (
                    <StatusChip tone={status.tone} icon={status.icon} className={status.spin ? '[&>svg]:animate-spin' : ''}>{status.label}</StatusChip>
                )}
                <label className="sr-only" htmlFor={`prio-${item.name}`}>Priority of {title}</label>
                <select
                    id={`prio-${item.name}`}
                    value={priority}
                    onChange={e => onPriorityChange(item.name, e.target.value)}
                    className="h-8 rounded-lg border border-gray-250 bg-white px-2 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/30 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200"
                    title="Which titles OmbiSub translates first"
                >
                    {PRIORITY_OPTIONS.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
                </select>
            </div>

            {total > 0 && !ignored && (
                <div className="w-full shrink-0 sm:w-40" title={`${done} of ${total} translated`}>
                    <ProgressBar value={done} max={total} tone={done >= total ? 'success' : 'accent'} />
                    <div className="mt-1 text-xs tabular-nums text-gray-500 dark:text-gray-400">{done}/{total} translated · {pct}%</div>
                </div>
            )}

            <div className="ml-auto flex shrink-0 items-center gap-1.5">
                {!manual && !ignored && (
                    <Button size="sm" variant="secondary" icon={Languages} loading={translating} onClick={() => onTranslateMissing(item)}
                        title="Queue everything this title still lacks">
                        Translate missing
                    </Button>
                )}
                <Button size="sm" variant="ghost" onClick={open}>Open</Button>
                <OverflowMenu items={menuItems} label={`More actions for ${title}`} />
            </div>
        </li>
    );
}

export default memo(LibraryRow);
