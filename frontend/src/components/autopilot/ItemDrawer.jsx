import { useState, useEffect, useCallback } from 'react';
import { X, Play, RotateCcw, SkipForward } from 'lucide-react';
import { api } from '../../api';
import { Button, IconButton, Spinner } from '../ui';
import {
    SOURCE_KIND_LABEL, asList, canCheckNow, canRetry, canSkip, errorText, fmtCountdown, fmtDateTime,
    isMissingEndpoint, itemTitle, reasonText,
} from './autopilotMeta';
import StateChip from './StateChip';

const Row = ({ label, children }) => (
    <div className="grid grid-cols-[110px_1fr] gap-2 py-1.5 border-b border-gray-100 dark:border-gray-800 last:border-0 text-xs">
        <dt className="text-gray-400 font-semibold">{label}</dt>
        <dd className="text-gray-800 dark:text-gray-200 break-words min-w-0">{children}</dd>
    </div>
);

const ItemDrawer = ({ itemId, toast, onClose, onChanged }) => {
    const [item, setItem] = useState(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [now, setNow] = useState(Date.now());

    const load = useCallback(async () => {
        try {
            const res = await api.getAutopilotItem(itemId);
            setItem(res.data);
            setError('');
        } catch (e) {
            setError(isMissingEndpoint(e) ? 'This item no longer exists.' : errorText(e));
        }
    }, [itemId]);

    useEffect(() => {
        load();
        const t = setInterval(() => { if (!document.hidden) { load(); setNow(Date.now()); } }, 5000);
        return () => clearInterval(t);
    }, [load]);

    useEffect(() => {
        const onKey = (e) => { if (e.key === 'Escape') onClose(); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [onClose]);

    const act = async (fn, okMsg) => {
        setBusy(true);
        try {
            const res = await fn(itemId);
            if (res.data && res.data.id) setItem(res.data);
            toast.success(okMsg);
            onChanged();
        } catch (e) {
            toast.error(errorText(e));
        } finally {
            setBusy(false);
        }
    };

    const events = asList(item?.events).slice().reverse();
    const outputs = asList(item?.outputs);

    return (
        <div className="fixed inset-0 z-[70] flex justify-end bg-black/40" onClick={onClose}>
            <aside
                role="dialog" aria-label="Autopilot item"
                className="w-full max-w-lg h-full overflow-y-auto bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-800 shadow-2xl p-5 space-y-4"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                        <h3 className="text-base font-bold text-gray-900 dark:text-white break-words">{item ? itemTitle(item) : 'Loading...'}</h3>
                        {item && <div className="mt-1"><StateChip state={item.state} /></div>}
                    </div>
                    <IconButton icon={X} label="Close" onClick={onClose} />
                </div>

                {error && <div className="text-sm text-rose-600 dark:text-rose-400">{error}</div>}
                {!item && !error && <Spinner />}

                {item && (
                    <>
                        <div className="flex flex-wrap gap-2">
                            {canCheckNow(item.state) && (
                                <Button size="sm" icon={Play} disabled={busy} onClick={() => act(api.autopilotItemNow, 'Checking now.')}>Check now</Button>
                            )}
                            {canRetry(item.state) && (
                                <Button size="sm" icon={RotateCcw} disabled={busy} onClick={() => act(api.retryAutopilotItem, 'Queued for another try.')}>Retry</Button>
                            )}
                            {canSkip(item.state) && (
                                <Button size="sm" variant="ghost" icon={SkipForward} disabled={busy} onClick={() => act(api.skipAutopilotItem, 'Skipped.')}>Skip</Button>
                            )}
                        </div>

                        <dl>
                            <Row label="Kind">{item.kind === 'movie' ? 'Movie' : 'Episode'} from {item.source === 'radarr' ? 'Radarr' : 'Sonarr'}</Row>
                            <Row label="Project">{item.project_name || '-'}{item.episode_name ? ` / ${item.episode_name}` : ''}</Row>
                            <Row label="Video file"><span className="font-mono text-[11px]">{item.media_path}</span></Row>
                            {item.reason && <Row label="Reason">{reasonText(item.reason)}</Row>}
                            {item.detail && <Row label="Detail">{item.detail}</Row>}
                            <Row label="Source">{SOURCE_KIND_LABEL[item.source_kind] || item.source_kind || '-'}</Row>
                            <Row label="Outputs">
                                {outputs.length === 0 ? '-' : outputs.map((o, i) => (
                                    <div key={i} className="font-mono text-[11px] break-all">{typeof o === 'string' ? o : o?.path}</div>
                                ))}
                            </Row>
                            <Row label="Detected">{fmtDateTime(item.detected_at) || '-'}</Row>
                            <Row label="Due">
                                {fmtDateTime(item.due_at) || '-'}
                                {item.state === 'waiting' && fmtCountdown(item.due_at, now) && (
                                    <span className="text-amber-600 dark:text-amber-400"> (in {fmtCountdown(item.due_at, now)})</span>
                                )}
                            </Row>
                            <Row label="Updated">{fmtDateTime(item.updated_at) || '-'}</Row>
                            <Row label="Attempts">{item.attempts ?? 0}{item.next_attempt_at ? `, next try ${fmtDateTime(item.next_attempt_at)}` : ''}</Row>
                            <Row label="Id"><span className="font-mono text-[11px]">{item.id}</span></Row>
                        </dl>

                        <div>
                            <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Timeline</h4>
                            {events.length === 0 ? (
                                <p className="text-xs text-gray-400">No events yet.</p>
                            ) : (
                                <ol className="relative border-l border-gray-200 dark:border-gray-700 ml-1.5 space-y-3">
                                    {events.map((ev, i) => (
                                        <li key={i} className="pl-4 relative">
                                            <span className="absolute -left-[5px] top-1 w-2.5 h-2.5 rounded-full bg-indigo-500" />
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <span className="text-[10px] text-gray-400 tabular-nums">{fmtDateTime(ev.t)}</span>
                                                {ev.state && <StateChip state={ev.state} />}
                                            </div>
                                            <div className="text-xs text-gray-700 dark:text-gray-300 mt-0.5 break-words">{ev.message}</div>
                                        </li>
                                    ))}
                                </ol>
                            )}
                        </div>
                    </>
                )}
            </aside>
        </div>
    );
};

export default ItemDrawer;
