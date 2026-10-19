import { useState, useEffect, useCallback, useRef } from 'react';
import { Search, Play, RotateCcw, SkipForward, ChevronLeft, ChevronRight, Film, Tv, Clock } from 'lucide-react';
import { api } from '../../api';
import { Button, Card, EmptyState, INPUT, Spinner } from '../ui';
import {
    STATE_ORDER, STATE_LABEL, SOURCE_KIND_LABEL, baseName, canCheckNow, canRetry,
    canSkip, errorText, fmtAgo, fmtCountdown, isMissingEndpoint, itemTitle, lastEvent, reasonText,
} from './autopilotMeta';
import ItemDrawer from './ItemDrawer';
import StateChip from './StateChip';

const PAGE = 25;

const ItemsTable = ({ state, onStateChange, toast, onChanged, refreshKey }) => {
    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [offset, setOffset] = useState(0);
    const [query, setQuery] = useState('');
    const [debounced, setDebounced] = useState('');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [busyId, setBusyId] = useState('');
    const [openId, setOpenId] = useState(null);
    const [now, setNow] = useState(Date.now());
    const alive = useRef(true);

    useEffect(() => {
        const t = setTimeout(() => { setDebounced(query.trim()); setOffset(0); }, 300);
        return () => clearTimeout(t);
    }, [query]);

    useEffect(() => { setOffset(0); }, [state]);

    const load = useCallback(async () => {
        try {
            const res = await api.getAutopilotItems({ state, q: debounced, limit: PAGE, offset });
            if (!alive.current) return;
            const d = res.data || {};
            setItems(Array.isArray(d.items) ? d.items : []);
            setTotal(d.total ?? (d.items || []).length);
            setError('');
        } catch (e) {
            if (!alive.current) return;
            setError(isMissingEndpoint(e) ? 'This server does not have Autopilot yet. Update OmbiSub.' : errorText(e));
        } finally {
            if (alive.current) setLoading(false);
        }
    }, [state, debounced, offset]);

    useEffect(() => {
        alive.current = true;
        setLoading(true);
        load();
        const t = setInterval(() => { if (!document.hidden) load(); }, 5000);
        const onVis = () => { if (!document.hidden) load(); };
        document.addEventListener('visibilitychange', onVis);
        return () => {
            alive.current = false;
            clearInterval(t);
            document.removeEventListener('visibilitychange', onVis);
        };
    }, [load, refreshKey]);

    const hasWaiting = items.some(i => i.state === 'waiting');
    useEffect(() => {
        if (!hasWaiting) return undefined;
        const t = setInterval(() => { if (!document.hidden) setNow(Date.now()); }, 1000);
        return () => clearInterval(t);
    }, [hasWaiting]);

    const act = async (e, item, fn, okMsg) => {
        e.stopPropagation();
        setBusyId(item.id);
        try {
            await fn(item.id);
            toast.success(okMsg);
            await load();
            if (onChanged) onChanged();
        } catch (err) {
            toast.error(`${itemTitle(item)}: ${errorText(err)}`);
        } finally {
            setBusyId('');
        }
    };

    const pages = Math.max(1, Math.ceil(total / PAGE));
    const page = Math.floor(offset / PAGE) + 1;

    return (
        <Card
            title={`Items (${total})`}
            subtitle="Every file Autopilot has seen, newest first."
            padded={false}
            actions={(
                <>
                    <select
                        value={state} onChange={(e) => onStateChange(e.target.value)} aria-label="Filter by state"
                        className={`${INPUT} !w-auto`}
                    >
                        <option value="">All (active first)</option>
                        {STATE_ORDER.map(s => <option key={s} value={s}>{STATE_LABEL[s]}</option>)}
                    </select>
                    <div className="relative">
                        <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                        <input
                            type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search title or file" aria-label="Search items"
                            className={`${INPUT} w-44 pl-8`}
                        />
                    </div>
                </>
            )}
        >
            {error ? (
                <p className="p-5 text-sm text-rose-600 dark:text-rose-400">{error}</p>
            ) : loading && items.length === 0 ? (
                <Spinner label="Loading items..." />
            ) : items.length === 0 ? (
                <EmptyState
                    icon={Clock}
                    title={state || debounced ? 'No items match this filter' : 'Nothing here yet'}
                    description={state || debounced ? undefined : 'New Sonarr and Radarr imports show up as soon as they are detected.'}
                />
            ) : (
                <ul className="divide-y divide-gray-100 dark:divide-gray-750">
                    {items.map(it => {
                        const countdown = it.state === 'waiting' ? fmtCountdown(it.due_at, now) : null;
                        const busy = busyId === it.id;
                        const Kind = it.kind === 'movie' ? Film : Tv;
                        return (
                            <li
                                key={it.id}
                                onClick={() => setOpenId(it.id)}
                                className="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-800/50 sm:px-5"
                            >
                                <Kind size={16} className="shrink-0 text-gray-400" aria-hidden="true" />
                                <div className="min-w-0 flex-1 basis-56">
                                    <div className="truncate text-sm font-semibold text-gray-900 dark:text-white" title={it.media_path}>{itemTitle(it)}</div>
                                    <div className="truncate text-xs text-gray-500 dark:text-gray-400">{baseName(it.media_path)}</div>
                                </div>
                                <div className="shrink-0 basis-36">
                                    <StateChip state={it.state} />
                                    {countdown && (
                                        <div className="mt-1 text-xs tabular-nums text-amber-600 dark:text-amber-400">
                                            {countdown === 'now' ? 'checks any moment' : `checks in ${countdown}`}
                                        </div>
                                    )}
                                    {it.state === 'failed' && it.attempts > 0 && (
                                        <div className="mt-1 text-xs text-gray-500">after {it.attempts} attempt{it.attempts === 1 ? '' : 's'}</div>
                                    )}
                                </div>
                                <div className="min-w-0 flex-1 basis-56 text-xs text-gray-500 dark:text-gray-400">
                                    <div className="line-clamp-2" title={lastEvent(it)}>{lastEvent(it) || (it.reason ? reasonText(it.reason) : '')}</div>
                                    <div className="mt-0.5">
                                        {SOURCE_KIND_LABEL[it.source_kind] || it.source_kind || ''}{(SOURCE_KIND_LABEL[it.source_kind] || it.source_kind) && it.updated_at ? ' · ' : ''}{fmtAgo(it.updated_at, now)}
                                    </div>
                                </div>
                                <div className="flex shrink-0 gap-1.5">
                                    {canCheckNow(it.state) && (
                                        <Button size="sm" icon={Play} loading={busy} title="Do not wait, check this file now" onClick={(e) => act(e, it, api.autopilotItemNow, 'Checking now.')}>Check now</Button>
                                    )}
                                    {canRetry(it.state) && (
                                        <Button size="sm" icon={RotateCcw} loading={busy} title="Run this item again from the start" onClick={(e) => act(e, it, api.retryAutopilotItem, 'Queued for another try.')}>Retry</Button>
                                    )}
                                    {canSkip(it.state) && (
                                        <Button size="sm" variant="ghost" icon={SkipForward} disabled={busy} title="Leave this file alone" onClick={(e) => act(e, it, api.skipAutopilotItem, 'Skipped.')}>Skip</Button>
                                    )}
                                </div>
                            </li>
                        );
                    })}
                </ul>
            )}

            {total > PAGE && (
                <div className="flex items-center justify-between border-t border-gray-100 p-3 text-xs text-gray-500 dark:border-gray-750 dark:text-gray-400">
                    <span>Page {page} of {pages}</span>
                    <div className="flex gap-1.5">
                        <Button size="sm" icon={ChevronLeft} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
                        <Button size="sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next <ChevronRight size={14} /></Button>
                    </div>
                </div>
            )}

            {openId && (
                <ItemDrawer
                    itemId={openId} toast={toast} onClose={() => setOpenId(null)}
                    onChanged={() => { load(); if (onChanged) onChanged(); }}
                />
            )}
        </Card>
    );
};

export default ItemsTable;
