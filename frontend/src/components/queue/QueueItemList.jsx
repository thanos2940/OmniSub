import { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import {
    AlertTriangle, ArrowUp, Bot, Check, CheckCircle2, Clock, FileText, Folder, ListTodo, Loader2, Pause,
    Play, RotateCcw, Terminal, Trash2, X, XCircle,
} from 'lucide-react';
import { api } from '../../api';
import { formatEta } from '../../utils/formatters';
import { useToast } from '../../context/ToastContext';
import { Button, Card, ConfirmDialog, EmptyState, IconButton, OverflowMenu, ProgressBar, StatusChip } from '../ui';
import AgentTaskModal from '../modals/AgentTaskModal';
import { isQueueStatus } from './queueStatus';

const STATUS = {
    pending: { tone: 'info', icon: Clock, label: 'Waiting' },
    queued: { tone: 'info', icon: ListTodo, label: 'Next up' },
    postponed: { tone: 'warning', icon: Pause, label: 'Postponed' },
    running: { tone: 'accent', icon: Loader2, label: 'Translating', spin: true },
    paused_daily_limit: { tone: 'warning', icon: Pause, label: 'Out of quota' },
    completed: { tone: 'success', icon: CheckCircle2, label: 'Done' },
    failed: { tone: 'danger', icon: AlertTriangle, label: 'Failed' },
    cancelled: { tone: 'neutral', icon: XCircle, label: 'Cancelled' },
};

const WORK_TYPES = {
    translation: { label: 'Translation', icon: null },
    retranslate: { label: 'Retranslation', icon: null },
    extraction: { label: 'Extraction', icon: FileText },
    export_retry: { label: 'Export retry', icon: Folder },
};

const PRIORITY_LABEL = (p) => {
    if (p === 10) return 'Run now';
    if (p < 0) return 'High';
    return { 0: 'Webhook', 1: 'Manual', 2: 'Sync', 3: 'Backlog', 5: 'Low' }[p] || 'Backlog';
};

const formatWhen = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
};

const countdown = (iso) => {
    if (!iso) return null;
    const diff = new Date(iso) - new Date();
    if (diff <= 0) return 'ready';
    return `in ${Math.ceil(diff / 60000)} min`;
};

function StatusCell({ item }) {
    const key = item.status === 'completed' && item.review_count > 0 ? 'review' : item.status;
    const s = key === 'review' ? { tone: 'warning', icon: AlertTriangle, label: 'Needs review' } : STATUS[key] || { tone: 'neutral', label: key };
    return (
        <div className="min-w-0 space-y-1.5">
            <div className="flex flex-wrap items-center gap-1.5">
                <StatusChip tone={s.tone} icon={s.icon} className={s.spin ? '[&>svg]:animate-spin' : ''}>{s.label}</StatusChip>
                {item.engine === 'agent' && !['completed', 'cancelled'].includes(item.status) && (
                    <StatusChip tone="neutral" icon={Bot} title={item.agent?.task_id ? 'Claimed by the agent runner' : 'Waits for the agent runner'}>agent</StatusChip>
                )}
                {item.status === 'completed' && item.review_count > 0 && (
                    <Link
                        to={`/review?project=${encodeURIComponent(item.project_name)}&episode=${encodeURIComponent(item.episode_name)}`}
                        className="text-xs font-medium text-amber-700 hover:underline dark:text-amber-400"
                    >
                        {item.review_count} line{item.review_count > 1 ? 's' : ''} to review
                    </Link>
                )}
                {item.status === 'completed' && !item.review_count && item.auto_repaired_count > 0 && (
                    <span className="text-xs text-gray-500 dark:text-gray-400">{item.auto_repaired_count} line(s) repaired automatically</span>
                )}
            </div>
            {item.status === 'running' && (
                <div className="w-full max-w-[16rem]">
                    <div className="flex items-center gap-2">
                        <ProgressBar value={Math.max(3, item.progress || 0)} />
                        <span className="w-9 shrink-0 text-right text-xs tabular-nums text-gray-600 dark:text-gray-300">{Math.round(item.progress || 0)}%</span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-2 text-xs text-gray-500 dark:text-gray-400">
                        {item.agent
                            ? <span>{item.agent.units_done ?? 0}/{item.agent.units ?? '?'} lines</span>
                            : (
                                <>
                                    {item.scenes_total > 0 && <span>Scene {item.scenes_done || 0}/{item.scenes_total}</span>}
                                    {item.cues_total > 0 && <span>{item.cues_translated || 0}/{item.cues_total} lines</span>}
                                </>
                            )}
                        {item.eta_seconds != null && <span>about {formatEta(item.eta_seconds)} left</span>}
                    </div>
                </div>
            )}
        </div>
    );
}

export default function QueueItemList({ items = [], view = 'queue', onViewLogs, onAction, onDelete, onBulkDelete, onRefresh }) {
    const toast = useToast();
    const [selected, setSelected] = useState(() => new Set());
    const [filter, setFilter] = useState('all'); // queue view: all | running | waiting | postponed
    const [workType, setWorkType] = useState('all');
    const [busy, setBusy] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [preparingId, setPreparingId] = useState(null);
    const [agentModalData, setAgentModalData] = useState(null);

    const shown = useMemo(() => {
        const inView = items.filter(i => (view === 'queue' ? isQueueStatus(i.status) : !isQueueStatus(i.status)));
        return inView.filter(i => {
            if (view === 'queue') {
                if (filter === 'running' && i.status !== 'running') return false;
                if (filter === 'waiting' && !['pending', 'queued', 'paused_daily_limit'].includes(i.status)) return false;
                if (filter === 'postponed' && i.status !== 'postponed') return false;
            }
            if (workType !== 'all' && (i.work_type || i.kind || 'translation').toLowerCase() !== workType) return false;
            return true;
        });
    }, [items, view, filter, workType]);

    const counts = useMemo(() => {
        const queue = items.filter(i => isQueueStatus(i.status));
        return {
            all: queue.length,
            running: queue.filter(i => i.status === 'running').length,
            waiting: queue.filter(i => ['pending', 'queued', 'paused_daily_limit'].includes(i.status)).length,
            postponed: queue.filter(i => i.status === 'postponed').length,
        };
    }, [items]);

    useEffect(() => { setSelected(new Set()); }, [view, filter, workType]);

    const allSelected = shown.length > 0 && shown.every(i => selected.has(i.id));
    const toggle = (id) => setSelected(prev => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
    });

    // One bulk action: run `fn` on every selected item whose status allows it and report the outcome.
    const runBulk = async (verb, allowed, fn) => {
        const targets = shown.filter(i => selected.has(i.id) && (!allowed || allowed.includes(i.status)));
        if (!targets.length) {
            toast.info(`None of the selected items can be ${verb}.`);
            return;
        }
        setBusy(true);
        const results = await Promise.allSettled(targets.map(t => fn(t.id)));
        const ok = results.filter(r => r.status === 'fulfilled').length;
        const failed = results.length - ok;
        if (failed === 0) toast.success(`${ok} item${ok === 1 ? '' : 's'} ${verb}.`);
        else if (ok > 0) toast.warning(`${ok} ${verb}, ${failed} failed.`);
        else toast.error(`Could not change ${failed} item${failed === 1 ? '' : 's'}.`);
        setSelected(new Set());
        setBusy(false);
        onRefresh?.();
    };

    const bulkDelete = async () => {
        setBusy(true);
        await onBulkDelete?.(Array.from(selected));
        setSelected(new Set());
        setBusy(false);
        setConfirmDelete(false);
    };

    const claimForAgent = async (item) => {
        setPreparingId(item.id);
        try {
            const res = await api.prepareAgentTask(item.id);
            if (res.data?.success) {
                setAgentModalData(res.data);
                onRefresh?.();
            } else {
                toast.error(res.data?.message || 'Could not prepare the agent workspace.');
            }
        } catch (err) {
            toast.error(err.response?.data?.message || err.response?.data?.detail || err.message);
        } finally {
            setPreparingId(null);
        }
    };

    const emptyText = view === 'queue'
        ? { title: 'Nothing in the queue', description: 'Translate a title from the Library, or let Autopilot pick up new files.' }
        : { title: 'No history yet', description: 'Finished and failed translations are listed here.' };

    const rowActions = (item) => {
        const waiting = ['pending', 'queued', 'paused_daily_limit'].includes(item.status);
        return (
            <div className="flex shrink-0 items-center gap-0.5">
                {view === 'queue' && (waiting || item.status === 'postponed') && (
                    <IconButton icon={Play} label="Run now" className="text-emerald-600 dark:text-emerald-400" onClick={() => onAction((id) => api.continueQueueItem(id, true), item.id, 'Moved to the front.')} />
                )}
                {view === 'queue' && ['pending', 'queued'].includes(item.status) && (
                    <IconButton icon={Pause} label="Postpone" onClick={() => onAction(api.postponeQueueItem, item.id, 'Postponed.')} />
                )}
                {view === 'queue' && ['running', 'pending', 'queued', 'postponed', 'paused_daily_limit'].includes(item.status) && (
                    <IconButton icon={X} label="Cancel" className="text-rose-500 dark:text-rose-400" onClick={() => onAction(api.stopQueueItem, item.id, 'Cancelled.')} />
                )}
                {view === 'history' && ['failed', 'cancelled', 'completed'].includes(item.status) && (
                    <IconButton icon={RotateCcw} label="Retry" onClick={() => onAction(api.retryQueueItem, item.id, 'Queued again.')} />
                )}
                <IconButton icon={Terminal} label="Logs" onClick={() => onViewLogs(item.id, `${item.project_name} · ${item.episode_name}`)} />
                <OverflowMenu
                    items={[
                        { label: 'Move to the top', icon: ArrowUp, hidden: view !== 'queue' || !waiting || !(item.priority > 0), onClick: () => onAction(api.prioritizeQueueItem, item.id, 'Moved up.') },
                        { label: 'Claim for an external agent', icon: Bot, hidden: item.status !== 'pending' || preparingId === item.id, onClick: () => claimForAgent(item) },
                        { label: 'Release the agent claim', icon: Check, hidden: !(item.status === 'running' && item.agent?.task_id), onClick: () => onAction(api.releaseAgentTask, item.agent.task_id, 'Released back to the queue.') },
                        { label: 'Remove from the list', icon: Trash2, danger: true, hidden: !onDelete, onClick: () => onDelete(item.id) },
                    ]}
                />
            </div>
        );
    };

    return (
        <Card padded={false}>
            <div className="flex flex-wrap items-center gap-2 border-b border-gray-100 px-4 py-3 dark:border-gray-750 sm:px-5">
                <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={() => setSelected(allSelected ? new Set() : new Set(shown.map(i => i.id)))}
                    className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 dark:border-gray-600"
                    aria-label="Select all shown"
                />
                {view === 'queue' && (
                    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter the queue">
                        {[['all', 'All'], ['running', 'Translating'], ['waiting', 'Waiting'], ['postponed', 'Postponed']].map(([id, label]) => (
                            <button
                                key={id}
                                type="button"
                                onClick={() => setFilter(id)}
                                aria-pressed={filter === id}
                                className={`rounded-full px-2.5 py-1 text-xs font-medium transition-colors ${filter === id
                                    ? 'bg-indigo-600 text-white'
                                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-750'}`}
                            >
                                {label} <span className="tabular-nums opacity-80">{counts[id]}</span>
                            </button>
                        ))}
                    </div>
                )}
                <select
                    value={workType}
                    onChange={e => setWorkType(e.target.value)}
                    aria-label="Filter by kind of work"
                    className="ml-auto h-8 rounded-lg border border-gray-250 bg-white px-2 text-xs text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200"
                >
                    <option value="all">All kinds of work</option>
                    {Object.entries(WORK_TYPES).map(([id, t]) => <option key={id} value={id}>{t.label}</option>)}
                </select>
            </div>

            {shown.length === 0 ? (
                <EmptyState icon={Clock} title={emptyText.title} description={emptyText.description} />
            ) : (
                <ul className="divide-y divide-gray-100 dark:divide-gray-750">
                    {shown.map(item => {
                        const type = WORK_TYPES[(item.work_type || item.kind || 'translation').toLowerCase()] || WORK_TYPES.translation;
                        const TypeIcon = type.icon;
                        const retry = ['pending', 'queued'].includes(item.status) && item.next_retry_at ? countdown(item.next_retry_at) : null;
                        return (
                            <li key={item.id} className={`flex flex-wrap items-start gap-x-4 gap-y-2 px-4 py-3 sm:px-5 ${selected.has(item.id) ? 'bg-indigo-50/60 dark:bg-indigo-950/20' : ''}`}>
                                <input
                                    type="checkbox"
                                    checked={selected.has(item.id)}
                                    onChange={() => toggle(item.id)}
                                    className="mt-1 h-4 w-4 shrink-0 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 dark:border-gray-600"
                                    aria-label={`Select ${item.project_name} ${item.episode_name}`}
                                />
                                <div className="min-w-0 flex-1 basis-56">
                                    <div className="flex items-center gap-2">
                                        <Link to={`/project/${encodeURIComponent(item.project_name)}`} className="truncate text-sm font-semibold text-gray-900 hover:text-indigo-600 dark:text-white dark:hover:text-indigo-400">
                                            {item.project_name}
                                        </Link>
                                        {(TypeIcon || type.label !== 'Translation') && (
                                            <StatusChip tone="neutral" icon={TypeIcon}>{type.label}</StatusChip>
                                        )}
                                    </div>
                                    <div className="mt-0.5 truncate text-xs text-gray-500 dark:text-gray-400">{item.episode_name}</div>
                                    <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-gray-500 dark:text-gray-400">
                                        <span>{PRIORITY_LABEL(item.priority)}</span>
                                        {item.attempts > 0 && <span>attempt {item.attempts}/5</span>}
                                        {retry && <span className="text-amber-600 dark:text-amber-400">retries {retry}</span>}
                                        {view === 'history' && (item.completed_at || item.started_at) && <span>{formatWhen(item.status === 'completed' ? item.completed_at : item.started_at)}</span>}
                                    </div>
                                    {item.last_error && (
                                        <div className="mt-1 truncate text-xs text-rose-600 dark:text-rose-400" title={item.last_error}>{item.last_error.split('\n')[0]}</div>
                                    )}
                                </div>
                                <div className="min-w-0 basis-52"><StatusCell item={item} /></div>
                                {rowActions(item)}
                            </li>
                        );
                    })}
                </ul>
            )}

            {selected.size > 0 && (
                <div className="fixed inset-x-3 bottom-4 z-40 mx-auto flex max-w-2xl flex-wrap items-center justify-center gap-2 rounded-xl border border-gray-200 bg-white p-3 shadow-lg dark:border-gray-700 dark:bg-gray-850">
                    <span className="mr-1 text-sm font-medium text-gray-800 dark:text-gray-100">{selected.size} selected</span>
                    {view === 'queue' ? (
                        <>
                            <Button size="sm" variant="primary" icon={Play} disabled={busy} onClick={() => runBulk('moved to the front', ['pending', 'queued', 'postponed', 'paused_daily_limit', 'failed'], (id) => api.continueQueueItem(id, true))}>Run now</Button>
                            <Button size="sm" icon={Pause} disabled={busy} onClick={() => runBulk('postponed', ['pending', 'queued'], api.postponeQueueItem)}>Postpone</Button>
                            <Button size="sm" variant="danger" icon={X} disabled={busy} onClick={() => runBulk('cancelled', ['running', 'pending', 'queued', 'postponed', 'paused_daily_limit'], api.stopQueueItem)}>Cancel</Button>
                        </>
                    ) : (
                        <Button size="sm" variant="primary" icon={RotateCcw} disabled={busy} onClick={() => runBulk('queued again', null, api.retryQueueItem)}>Retry</Button>
                    )}
                    <Button size="sm" variant="danger" icon={Trash2} disabled={busy} onClick={() => setConfirmDelete(true)}>Remove</Button>
                    <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>Clear</Button>
                </div>
            )}

            <ConfirmDialog
                open={confirmDelete}
                title="Remove from the list?"
                message={`${selected.size} item${selected.size === 1 ? '' : 's'} will be removed from the queue records. Subtitles already written are not touched.`}
                confirmLabel="Remove"
                danger
                busy={busy}
                onConfirm={bulkDelete}
                onCancel={() => setConfirmDelete(false)}
            />

            <AgentTaskModal agentModalData={agentModalData} onClose={() => setAgentModalData(null)} />
        </Card>
    );
}
