import { useMemo, useRef, useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, ChevronDown, ChevronRight, ExternalLink, Film, FileText, Languages, Layers, Loader2, RotateCcw, Search, ShieldAlert, Trash2, Upload, Wrench, Clock, ListChecks } from 'lucide-react';
import { Button, ConfirmDialog, Card, EmptyState, INPUT, OverflowMenu, ProgressBar, StatusChip } from '../../ui';
import { canTranslate, episodeCode, episodeTitle, formatDate, gapInfo, hasSourceSubtitle, lastTranslated, lineProgress, matchesFilter, seasonLabel, seasonNumber } from '../episodeUtils';

const FILTERS = [
    { id: 'all', label: 'All' },
    { id: 'translated', label: 'Translated' },
    { id: 'missing', label: 'Missing' },
    { id: 'progress', label: 'In progress' },
    { id: 'nosub', label: 'No subtitle' },
];

const GRID = 'grid items-center gap-x-3 gap-y-1.5 grid-cols-[auto_minmax(0,1fr)_auto] md:grid-cols-[1rem_minmax(0,1fr)_10rem_9rem_8rem_13rem]';

// What the translation itself looks like (independent of anything running right now).
function StatusCell({ ep, projectName }) {
    if (!hasSourceSubtitle(ep)) {
        return ep.has_media || ep.metadata?.arr_media_path
            ? <StatusChip tone="neutral" icon={Film} title="No subtitle file yet; it is taken from the video when you translate">In the video</StatusChip>
            : <StatusChip tone="warning" icon={AlertTriangle} title="No subtitle file and no video linked">No subtitle</StatusChip>;
    }
    if (!ep.translated) return <StatusChip tone="neutral">Not translated</StatusChip>;
    const gaps = gapInfo(ep);
    if (gaps.missing > 0) {
        return <StatusChip tone="warning" icon={AlertTriangle} title={`${gaps.missing} line(s) are still in the source language`}>Incomplete · {gaps.missing}</StatusChip>;
    }
    if (gaps.misplaced > 0) {
        return <StatusChip tone="danger" icon={AlertTriangle} title="Some lines carry the translation of a neighbouring line">Misaligned · {gaps.misplaced}</StatusChip>;
    }
    if (gaps.broken > 0) {
        return <StatusChip tone="danger" icon={AlertTriangle} title="Garbled text, leftover prompt text or mixed alphabets. Repair them from Sync.">Garbled · {gaps.broken}</StatusChip>;
    }
    const review = ep.needs_review_count ?? ep.metadata?.needs_review_count ?? 0;
    if (review > 0) {
        return (
            <Link to={`/review?project=${encodeURIComponent(projectName)}&episode=${encodeURIComponent(ep.name)}`} title="Open the flagged lines in Review">
                <StatusChip tone="warning" icon={AlertTriangle} className="hover:brightness-95">Review · {review}</StatusChip>
            </Link>
        );
    }
    return <StatusChip tone="success">Translated</StatusChip>;
}

function ProgressCell({ ep, entry }) {
    if (entry?.phase === 'running' && entry.progress != null) {
        return (
            <div className="min-w-[6rem] space-y-1">
                <ProgressBar value={entry.progress} />
                <div className="text-xs tabular-nums text-gray-500 dark:text-gray-400">{Math.round(entry.progress)}%</div>
            </div>
        );
    }
    const p = lineProgress(ep);
    if (p) {
        return (
            <div className="min-w-[6rem] space-y-1">
                <ProgressBar value={p.done} max={p.total} tone={p.done === p.total ? 'success' : 'warning'} />
                <div className="text-xs tabular-nums text-gray-500 dark:text-gray-400">{p.done} / {p.total} lines</div>
            </div>
        );
    }
    return <div className="text-xs tabular-nums text-gray-500 dark:text-gray-400">{ep.line_count > 0 ? `${ep.line_count} lines` : '—'}</div>;
}

function ActionCell({ ep, entry, busy, onTranslate, onRetranslate }) {
    if (entry) {
        const label = entry.phase === 'running' ? 'Translating…' : entry.phase === 'extracting' ? 'Extracting…' : 'Queued';
        return (
            <StatusChip tone={entry.phase === 'queued' ? 'info' : 'accent'} icon={entry.phase === 'queued' ? Clock : Loader2} className={entry.phase === 'queued' ? '' : '[&>svg]:animate-spin'}>
                {label}
            </StatusChip>
        );
    }
    const gaps = gapInfo(ep);
    const complete = ep.translated && gaps.missing === 0;
    const allowed = canTranslate(ep);
    if (complete) {
        return <Button size="sm" icon={RotateCcw} onClick={onRetranslate} disabled={busy}>Retranslate</Button>;
    }
    return (
        <Button size="sm" variant="primary" icon={Languages} onClick={onTranslate} disabled={busy || !allowed}
            title={allowed ? (ep.translated ? 'Translate the lines that are still missing' : undefined) : 'No subtitle and no video file linked to this episode'}>
            Translate
        </Button>
    );
}

function Row({ ep, projectName, selected, entry, busy, onToggle, onTranslate, onRetranslate, onAction }) {
    const hasSource = hasSourceSubtitle(ep);
    const hasMedia = !!(ep.has_media || ep.metadata?.arr_media_path);
    const title = episodeTitle(ep);
    const date = lastTranslated(ep);
    return (
        <div className={`${GRID} px-4 py-2.5 ${selected ? 'bg-indigo-50/60 dark:bg-indigo-950/20' : ''}`}>
            <input type="checkbox" checked={selected} onChange={(e) => onToggle(ep.name, e.nativeEvent.shiftKey)}
                aria-label={`Select ${ep.name}`} className="h-4 w-4 rounded border-gray-300 text-indigo-600" />
            <div className="min-w-0">
                <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-gray-900 dark:text-white" title={ep.name}>{episodeCode(ep)}</span>
                    {ep.has_ass && <span className="rounded bg-purple-100 px-1.5 text-[10px] font-semibold uppercase text-purple-700 dark:bg-purple-950 dark:text-purple-300">ASS</span>}
                </div>
                {title && <div className="truncate text-xs text-gray-500 dark:text-gray-400" title={title}>{title}</div>}
            </div>
            <div className="col-span-2 col-start-2 flex flex-wrap items-center gap-x-4 gap-y-1 md:contents">
                <div><StatusCell ep={ep} projectName={projectName} /></div>
                <ProgressCell ep={ep} entry={entry} />
                <div className="text-xs text-gray-500 dark:text-gray-400">{date ? formatDate(date) : ''}</div>
            </div>
            <div className="col-start-3 row-start-1 flex items-center justify-end gap-1 md:col-start-auto md:row-start-auto">
                <ActionCell ep={ep} entry={entry} busy={busy} onTranslate={onTranslate} onRetranslate={onRetranslate} />
                {hasSource && (
                    <Link to={`/project/${encodeURIComponent(projectName)}/episode/${encodeURIComponent(ep.name)}`} aria-label={`Open ${ep.name} in the editor`} title="Open in editor"
                        className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-white">
                        <FileText size={16} />
                    </Link>
                )}
                <OverflowMenu items={[
                    { label: 'Extract subtitle from video', icon: Film, hidden: !hasMedia, onClick: () => onAction('extract', ep) },
                    { label: 'Inspect embedded tracks', icon: Layers, hidden: !hasMedia, onClick: () => onAction('tracks', ep) },
                    { label: 'Apply SubtitleEdit fixes', icon: Wrench, hidden: !hasSource, onClick: () => onAction('fixes', ep) },
                    { label: 'Check missing lines', icon: Search, hidden: !hasSource || !ep.translated, onClick: () => onAction('missing', ep) },
                    { label: 'ScriptGuard check', icon: ShieldAlert, hidden: !ep.translated, onClick: () => onAction('script', ep) },
                    { label: 'Delete translation', icon: Trash2, danger: true, hidden: !ep.translated, onClick: () => onAction('delete', ep) },
                ]} />
            </div>
        </div>
    );
}

function SeasonCheckbox({ names, selected, onToggle }) {
    const ref = useRef(null);
    const count = names.filter(n => selected.has(n)).length;
    useEffect(() => {
        if (ref.current) ref.current.indeterminate = count > 0 && count < names.length;
    }, [count, names.length]);
    return <input ref={ref} type="checkbox" checked={names.length > 0 && count === names.length} onChange={onToggle}
        aria-label="Select the whole season" className="h-4 w-4 rounded border-gray-300 text-indigo-600" />;
}

const EpisodesTab = ({
    project, projectName, episodes, subprojects, selection, queue, busy,
    onTranslate, onAction, onUpload, onBulk,
}) => {
    const [search, setSearch] = useState('');
    const [filter, setFilter] = useState('all');
    const [collapsed, setCollapsed] = useState({});
    const [confirmRetranslate, setConfirmRetranslate] = useState(null);
    const { selected, toggleEpisode, toggleMultiple, clearSelection } = selection;

    const counts = useMemo(() => {
        const c = {};
        FILTERS.forEach(f => { c[f.id] = episodes.filter(ep => matchesFilter(ep, f.id, queue.get(ep.name))).length; });
        return c;
    }, [episodes, queue]);

    const visible = useMemo(() => {
        const q = search.trim().toLowerCase();
        return episodes.filter(ep => matchesFilter(ep, filter, queue.get(ep.name))
            && (!q || ep.name.toLowerCase().includes(q) || episodeTitle(ep).toLowerCase().includes(q)));
    }, [episodes, search, filter, queue]);

    const seasons = useMemo(() => {
        const bySeason = new Map();
        visible.forEach(ep => {
            const n = seasonNumber(ep);
            if (!bySeason.has(n)) bySeason.set(n, []);
            bySeason.get(n).push(ep);
        });
        return [...bySeason.entries()].sort((a, b) => a[0] - b[0]);
    }, [visible]);

    const totalsBySeason = useMemo(() => {
        const t = new Map();
        episodes.forEach(ep => {
            const n = seasonNumber(ep);
            const cur = t.get(n) || { done: 0, total: 0 };
            cur.total += 1;
            if (ep.translated) cur.done += 1;
            t.set(n, cur);
        });
        return t;
    }, [episodes]);

    const toggleSeason = (n) => setCollapsed(prev => ({ ...prev, [n]: !prev[n] }));
    const showSeasonHeaders = seasons.length > 1 || (seasons.length === 1 && seasons[0][0] > 0);

    if (project?.type === 'parent') {
        return (
            <Card title={`Shows in this universe (${subprojects.length})`} subtitle="A universe shares its glossary, context and character profiles with the shows linked to it.">
                {subprojects.length === 0 ? (
                    <EmptyState icon={Layers} title="No shows linked yet" description="Link a show to this universe from the show's project settings." />
                ) : (
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                        {subprojects.map(sp => (
                            <Link key={sp.name} to={`/project/${encodeURIComponent(sp.name)}`}
                                className="flex items-center justify-between rounded-lg border border-gray-200 p-3 hover:border-indigo-400 dark:border-gray-700">
                                <div className="min-w-0">
                                    <div className="truncate text-sm font-medium text-gray-900 dark:text-white">{sp.name}</div>
                                    <div className="text-xs text-gray-500">{sp.target_language || 'Default language'} · {sp.episodes_count || 0} episodes</div>
                                </div>
                                <ExternalLink size={16} className="shrink-0 text-gray-400" />
                            </Link>
                        ))}
                    </div>
                )}
            </Card>
        );
    }

    return (
        <div className="space-y-4">
            <Card padded={false}>
                <div className="flex flex-wrap items-center gap-3 p-3 sm:px-4">
                    <div className="relative w-full sm:w-64">
                        <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                        <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search episodes" aria-label="Search episodes" className={`${INPUT} pl-9`} />
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                        {FILTERS.map(f => (
                            <button key={f.id} type="button" onClick={() => setFilter(f.id)} aria-pressed={filter === f.id}
                                className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors ${filter === f.id
                                    ? 'border-indigo-600 bg-indigo-600 text-white'
                                    : 'border-gray-200 text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800'}`}>
                                {f.label}
                                <span className={`tabular-nums ${filter === f.id ? 'text-indigo-100' : 'text-gray-400'}`}>{counts[f.id]}</span>
                            </button>
                        ))}
                    </div>
                    <label className="ml-auto inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-lg border border-gray-250 bg-white px-2.5 text-xs font-medium text-gray-800 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-750">
                        <input type="file" multiple accept=".srt,.ass,.ssa,.vtt" onChange={onUpload} className="hidden" />
                        <Upload size={14} /> Upload subtitles
                    </label>
                </div>
            </Card>

            <Card padded={false}>
                {visible.length === 0 ? (
                    <EmptyState icon={Film}
                        title={episodes.length === 0 ? 'No episodes yet' : 'No episodes match'}
                        description={episodes.length === 0 ? 'Sync the title with Sonarr/Radarr, or upload subtitle files.' : 'Try another filter or clear the search.'} />
                ) : (
                    <div role="table" aria-label="Episodes">
                        <div role="row" className={`${GRID} hidden border-b border-gray-100 px-4 py-2 text-xs font-medium uppercase tracking-wide text-gray-500 dark:border-gray-750 dark:text-gray-400 md:grid`}>
                            <span className="w-4" /><span>Episode</span><span>Status</span><span>Lines</span><span>Last translated</span><span className="text-right">Actions</span>
                        </div>
                        {seasons.map(([n, eps]) => {
                            const names = eps.map(e => e.name);
                            const total = totalsBySeason.get(n) || { done: 0, total: eps.length };
                            const isCollapsed = !!collapsed[n] && showSeasonHeaders;
                            return (
                                <div key={n}>
                                    {showSeasonHeaders && (
                                        <div className="flex items-center gap-3 border-y border-gray-100 bg-gray-50 px-4 py-2 first:border-t-0 dark:border-gray-750 dark:bg-gray-900/40">
                                            <SeasonCheckbox names={names} selected={selected} onToggle={() => toggleMultiple(names)} />
                                            <button type="button" onClick={() => toggleSeason(n)} aria-expanded={!isCollapsed}
                                                className="flex flex-1 items-center gap-2 text-left text-sm font-semibold text-gray-900 dark:text-white">
                                                {isCollapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
                                                {seasonLabel(n)}
                                            </button>
                                            <span className="text-xs tabular-nums text-gray-500 dark:text-gray-400">{total.done} / {total.total} translated</span>
                                        </div>
                                    )}
                                    {!isCollapsed && (
                                        <div className="divide-y divide-gray-100 dark:divide-gray-750">
                                            {eps.map(ep => (
                                                <Row key={ep.name} ep={ep} projectName={projectName} busy={busy}
                                                    selected={selected.has(ep.name)} entry={queue.get(ep.name)}
                                                    onToggle={(name, shift) => toggleEpisode(name, shift, visible)}
                                                    onTranslate={() => onTranslate([ep.name])}
                                                    onRetranslate={() => setConfirmRetranslate(ep)}
                                                    onAction={onAction} />
                                            ))}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                )}
            </Card>

            {selected.size > 0 && (
                <div className="fixed inset-x-4 bottom-4 z-40 mx-auto flex max-w-xl flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white p-3 shadow-xl dark:border-gray-700 dark:bg-gray-850">
                    <div className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200">
                        <ListChecks size={16} className="text-indigo-600" />
                        <span className="font-medium">{selected.size} selected</span>
                        <button type="button" onClick={clearSelection} className="text-xs text-gray-500 underline hover:text-gray-900 dark:hover:text-white">Clear</button>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        <Button size="sm" variant="primary" icon={Languages} disabled={busy} onClick={() => onBulk('translate')}>Translate</Button>
                        <Button size="sm" icon={Film} onClick={() => onBulk('extract')}>Extract</Button>
                        <Button size="sm" variant="danger" icon={Trash2} onClick={() => onBulk('delete')}>Delete translation</Button>
                    </div>
                </div>
            )}

            <ConfirmDialog open={!!confirmRetranslate} title={`Retranslate ${confirmRetranslate?.name || ''}?`} confirmLabel="Retranslate"
                onCancel={() => setConfirmRetranslate(null)}
                onConfirm={() => { const name = confirmRetranslate.name; setConfirmRetranslate(null); onTranslate([name], { retranslate: true }); }}>
                <p>The existing translation is replaced by a new one. Lines you edited by hand are translated again too.</p>
            </ConfirmDialog>
        </div>
    );
};

export default EpisodesTab;
