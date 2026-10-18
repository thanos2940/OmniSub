import { useState, useEffect, useCallback, useRef } from 'react';
import { Search, Loader2, Play, RotateCcw, SkipForward, ChevronLeft, ChevronRight, Film, Tv } from 'lucide-react';
import { api } from '../../api';
import {
    STATE_ORDER, STATE_LABEL, SOURCE_KIND_LABEL, asList, baseName, canCheckNow, canRetry,
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

    const actBtn = 'inline-flex items-center gap-1 text-[11px] font-semibold border px-2 py-1 rounded-lg bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50';
    const pages = Math.max(1, Math.ceil(total / PAGE));
    const page = Math.floor(offset / PAGE) + 1;

    return (
        <div className="bg-white dark:bg-gray-850 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm">
            <div className="p-4 flex flex-col sm:flex-row sm:items-center gap-3 border-b border-gray-100 dark:border-gray-800">
                <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider sm:mr-auto">Items ({total})</h3>
                <select
                    value={state} onChange={(e) => onStateChange(e.target.value)} aria-label="Filter by state"
                    className="px-2 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
                >
                    <option value="">All (active first)</option>
                    {STATE_ORDER.map(s => <option key={s} value={s}>{STATE_LABEL[s]}</option>)}
                </select>
                <div className="relative">
                    <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
                    <input
                        value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search title or file"
                        className="pl-8 pr-3 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 w-full sm:w-56"
                    />
                </div>
            </div>

            {error ? (
                <div className="p-6 text-sm text-rose-600 dark:text-rose-400">{error}</div>
            ) : loading && items.length === 0 ? (
                <div className="p-10 flex justify-center text-gray-400"><Loader2 className="animate-spin" size={22} /></div>
            ) : items.length === 0 ? (
                <div className="p-10 text-center text-sm text-gray-500 dark:text-gray-400">
                    {state || debounced
                        ? 'No items match this filter.'
                        : 'Nothing here yet. New Sonarr and Radarr imports will show up as soon as they are detected.'}
                </div>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs">
                        <thead className="text-[10px] uppercase tracking-wider text-gray-400">
                            <tr>
                                <th className="px-4 py-2 font-semibold">Title</th>
                                <th className="px-2 py-2 font-semibold">State</th>
                                <th className="px-2 py-2 font-semibold">Source</th>
                                <th className="px-2 py-2 font-semibold">Outputs</th>
                                <th className="px-2 py-2 font-semibold">Last event</th>
                                <th className="px-4 py-2 font-semibold text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                            {items.map(it => {
                                const outputs = asList(it.outputs);
                                const countdown = it.state === 'waiting' ? fmtCountdown(it.due_at, now) : null;
                                const busy = busyId === it.id;
                                return (
                                    <tr key={it.id} onClick={() => setOpenId(it.id)}
                                        className="cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 align-top">
                                        <td className="px-4 py-2.5 max-w-[260px]">
                                            <div className="flex items-start gap-1.5">
                                                {it.kind === 'movie' ? <Film size={13} className="mt-0.5 text-gray-400 shrink-0" /> : <Tv size={13} className="mt-0.5 text-gray-400 shrink-0" />}
                                                <div className="min-w-0">
                                                    <div className="font-semibold text-gray-900 dark:text-white truncate" title={it.media_path}>{itemTitle(it)}</div>
                                                    <div className="text-[10px] text-gray-400 truncate">{baseName(it.media_path)}</div>
                                                </div>
                                            </div>
                                        </td>
                                        <td className="px-2 py-2.5 whitespace-nowrap">
                                            <StateChip state={it.state} />
                                            {countdown && (
                                                <div className="text-[10px] text-amber-600 dark:text-amber-400 mt-1 tabular-nums">
                                                    {countdown === 'now' ? 'checks any moment' : `checks in ${countdown}`}
                                                </div>
                                            )}
                                            {it.state === 'failed' && it.attempts > 0 && (
                                                <div className="text-[10px] text-gray-400 mt-1">after {it.attempts} attempt{it.attempts === 1 ? '' : 's'}</div>
                                            )}
                                        </td>
                                        <td className="px-2 py-2.5 text-gray-600 dark:text-gray-300 whitespace-nowrap">
                                            {SOURCE_KIND_LABEL[it.source_kind] || (it.source_kind ? it.source_kind : <span className="text-gray-400">-</span>)}
                                        </td>
                                        <td className="px-2 py-2.5 max-w-[180px]">
                                            {outputs.length === 0 ? <span className="text-gray-400">-</span> : outputs.map((o, i) => (
                                                <div key={i} className="truncate text-gray-600 dark:text-gray-300" title={String(o)}>{baseName(typeof o === 'string' ? o : o?.path)}</div>
                                            ))}
                                        </td>
                                        <td className="px-2 py-2.5 max-w-[240px] text-gray-500 dark:text-gray-400">
                                            <div className="line-clamp-2" title={lastEvent(it)}>{lastEvent(it) || (it.reason ? reasonText(it.reason) : '')}</div>
                                            <div className="text-[10px] text-gray-400">{fmtAgo(it.updated_at, now)}</div>
                                        </td>
                                        <td className="px-4 py-2.5 text-right whitespace-nowrap">
                                            <div className="inline-flex gap-1.5">
                                                {canCheckNow(it.state) && (
                                                    <button type="button" disabled={busy} className={actBtn} title="Do not wait, check this file now"
                                                        onClick={(e) => act(e, it, api.autopilotItemNow, 'Checking now.')}>
                                                        {busy ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} Check now
                                                    </button>
                                                )}
                                                {canRetry(it.state) && (
                                                    <button type="button" disabled={busy} className={actBtn} title="Run this item again from the start"
                                                        onClick={(e) => act(e, it, api.retryAutopilotItem, 'Queued for another try.')}>
                                                        {busy ? <Loader2 size={11} className="animate-spin" /> : <RotateCcw size={11} />} Retry
                                                    </button>
                                                )}
                                                {canSkip(it.state) && (
                                                    <button type="button" disabled={busy} className={actBtn} title="Leave this file alone"
                                                        onClick={(e) => act(e, it, api.skipAutopilotItem, 'Skipped.')}>
                                                        <SkipForward size={11} /> Skip
                                                    </button>
                                                )}
                                            </div>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}

            {total > PAGE && (
                <div className="p-3 flex items-center justify-between border-t border-gray-100 dark:border-gray-800 text-xs text-gray-500 dark:text-gray-400">
                    <span>Page {page} of {pages}</span>
                    <div className="flex gap-1.5">
                        <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} className={actBtn}><ChevronLeft size={12} /> Previous</button>
                        <button type="button" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)} className={actBtn}>Next <ChevronRight size={12} /></button>
                    </div>
                </div>
            )}

            {openId && (
                <ItemDrawer
                    itemId={openId} toast={toast} onClose={() => setOpenId(null)}
                    onChanged={() => { load(); if (onChanged) onChanged(); }}
                />
            )}
        </div>
    );
};

export default ItemsTable;
