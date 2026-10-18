import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Link } from 'react-router-dom';
import {
    Languages, Search, Loader2, ChevronDown, ChevronRight, Wrench, Bot,
    AlertTriangle, CheckCircle2, ArrowRight,
} from 'lucide-react';
import { api } from '../../api';
import { useToast, formatErrorMessage } from '../../context/ToastContext';
import { BROKEN_LABELS, BROKEN_HINTS, brokenGroups, joinParts, repairParts, toCount } from '../../utils/translationAudit';

// Library-wide translation check: scans every translated episode of every project (a background job
// on the server) for untranslated lines, lines that carry another line's translation, and garbled /
// leftover prompt text. It can repair what is safe to repair and queue the rest for the agent runner.

const POLL_MS = 2000;

const TONES = {
    orange: 'bg-orange-100 dark:bg-orange-950 text-orange-800 dark:text-orange-300 border-orange-300 dark:border-orange-700/60',
    rose: 'bg-rose-100 dark:bg-rose-950 text-rose-800 dark:text-rose-300 border-rose-300 dark:border-rose-700/60',
    amber: 'bg-amber-100 dark:bg-amber-950 text-amber-800 dark:text-amber-300 border-amber-300 dark:border-amber-700/60',
    gray: 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 border-gray-200 dark:border-gray-700',
};

const VALUE_TONES = {
    neutral: 'text-gray-900 dark:text-white',
    orange: 'text-orange-500',
    rose: 'text-rose-500',
    gray: 'text-gray-500 dark:text-gray-400',
};

const BUTTON = 'flex items-center gap-1.5 text-sm font-semibold border px-3 py-1.5 rounded-lg shadow-sm disabled:opacity-50';

const isState = (data) => !!data && typeof data === 'object' && typeof data.status === 'string';

const toDate = (value) => {
    if (value == null || value === '') return null;
    // Epoch seconds or milliseconds, or an ISO string.
    const d = typeof value === 'number' ? new Date(value < 1e12 ? value * 1000 : value) : new Date(value);
    return Number.isNaN(d.getTime()) ? null : d;
};

const fmtWhen = (value) => {
    const d = toDate(value);
    return d ? d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
};

const fmtTook = (from, to) => {
    const a = toDate(from);
    const b = toDate(to);
    if (!a || !b) return '';
    const sec = Math.max(0, Math.round((b - a) / 1000));
    if (sec < 60) return `${sec} s`;
    const min = Math.floor(sec / 60);
    return min < 60 ? `${min} min ${sec % 60} s` : `${Math.floor(min / 60)} h ${min % 60} min`;
};

// What a finished run was ("Check finished …") and what a running one is doing ("Checking …").
const runLabel = (options) => (options?.requeue ? 'Repair & queue' : options?.repair ? 'Repair' : 'Check');
const activityLabel = (options) => (options?.requeue ? 'Repairing & queuing' : options?.repair ? 'Repairing' : 'Checking');

// One project of the result with the numbers the UI needs. `rows` only holds episodes with issues; the
// per-project lists/counts are cross-checked against them so a missing field never hides a problem.
const summarizeProject = (project) => {
    const rows = project.episodes || [];
    const derived = {
        incomplete: rows.filter((r) => toCount(r.dialogue) + toCount(r.signs) > 0).length,
        misplaced: rows.filter((r) => !r.imported && toCount(r.misplaced) > 0).length,
        imported: rows.filter((r) => r.imported && toCount(r.misplaced) > 0).length,
        broken: rows.filter((r) => brokenGroups(r.broken).total > 0).length,
    };
    const counts = {
        incomplete: Math.max(toCount(project.incomplete), derived.incomplete),
        misplaced: Math.max(toCount(project.misplaced), derived.misplaced),
        imported: Math.max(toCount(project.misplaced_imported), derived.imported),
        broken: Math.max(toCount(project.broken), derived.broken),
    };
    const issues = counts.incomplete + counts.misplaced + counts.imported + counts.broken;
    return {
        name: project.project_name,
        raw: project,
        rows,
        counts,
        issues,
        visible: issues > 0 || rows.length > 0 || !!project.error,
    };
};

const sumOf = (projects, pick) => projects.reduce((n, p) => n + toCount(pick(p)), 0);

// What a repair/requeue run changed across the library (totals first, per-project sums as fallback).
const libraryChanges = (result) => {
    const totals = result?.totals || {};
    const projects = result?.projects || [];
    const enqueued = totals.enqueued ?? sumOf(projects, (p) => p.enqueue?.enqueued_count);
    const parts = repairParts({
        repaired_lines: totals.repaired_lines ?? sumOf(projects, (p) => p.repaired_lines),
        cleared_lines: totals.cleared_lines ?? sumOf(projects, (p) => p.cleared_lines),
        broken_cleared: sumOf(projects, (p) => p.broken_cleared),
        disk_repaired: sumOf(projects, (p) => p.disk_repaired),
        reexported: sumOf(projects, (p) => p.reexported),
    });
    if (toCount(enqueued)) parts.push(`queued ${toCount(enqueued)} episode(s) for the agent`);
    return { text: joinParts(parts), enqueued: toCount(enqueued) };
};

// The toast text when a run we watched finishes: [kind, message].
const finishedToast = (job) => {
    const options = job.options || {};
    const { text } = libraryChanges(job.result);
    if (options.repair || options.requeue) {
        return ['success', text ? `Library repair finished. ${text}.` : 'Library repair finished; nothing needed repairing.'];
    }
    const totals = job.result?.totals || {};
    const found = [];
    if (toCount(totals.incomplete)) found.push(`${toCount(totals.incomplete)} with untranslated lines`);
    if (toCount(totals.misplaced)) found.push(`${toCount(totals.misplaced)} with lines on the wrong line`);
    if (toCount(totals.broken)) found.push(`${toCount(totals.broken)} with garbled text`);
    if (found.length === 0) return ['success', 'Library check finished: every translated episode looks complete.'];
    return ['warning', `Library check finished: ${found.join(', ')}.`];
};

const Chip = ({ tone = 'gray', title, children }) => (
    <span title={title} className={`px-2 py-0.5 rounded-full text-[10px] font-bold border whitespace-nowrap ${TONES[tone]}`}>
        {children}
    </span>
);

const Stat = ({ label, note, value, tone = 'neutral', title }) => (
    <div title={title} className="p-3 rounded-xl border border-gray-100 dark:border-gray-800 bg-gray-50/60 dark:bg-gray-900/30">
        <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider leading-tight">{label}</div>
        <div className={`text-xl font-bold mt-1 tabular-nums ${value > 0 ? VALUE_TONES[tone] : VALUE_TONES.neutral}`}>{value}</div>
        {note && <div className="text-[10px] text-gray-400 leading-tight">{note}</div>}
    </div>
);

const EpisodeRow = ({ projectName, row }) => {
    const untranslated = toCount(row.dialogue) + toCount(row.signs);
    const flagged = toCount(row.misplaced);
    const broken = brokenGroups(row.broken);
    return (
        <li className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-xs">
            <Link
                to={`/project/${encodeURIComponent(projectName)}/episode/${encodeURIComponent(row.episode_name)}`}
                className="font-mono font-semibold text-gray-800 dark:text-gray-200 hover:text-indigo-600 dark:hover:text-indigo-400"
            >
                {row.episode_name}
            </Link>
            {row.imported && (
                <span className="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400"
                    title="An imported subtitle file, not translated by Omnisub">
                    imported
                </span>
            )}
            <div className="flex flex-wrap items-center gap-1.5">
                {untranslated > 0 && (
                    <Chip tone="orange" title={`${toCount(row.dialogue)} dialogue line(s) and ${toCount(row.signs)} on-screen text line(s) are still untranslated`}>
                        {untranslated} untranslated
                    </Chip>
                )}
                {flagged > 0 && !row.imported && (
                    <Chip tone="rose" title="These lines carry the translation of another line">
                        {flagged} on the wrong line
                    </Chip>
                )}
                {flagged > 0 && row.imported && (
                    <Chip tone="gray" title="This imported subtitle does not line up with the source (another cut or timing). It is left as it is.">
                        {flagged} don&rsquo;t match the source
                    </Chip>
                )}
                {broken.garbled > 0 && <Chip tone="rose" title={BROKEN_HINTS.garbled}>{broken.garbled} {BROKEN_LABELS.garbled}</Chip>}
                {broken.leftover > 0 && <Chip tone="rose" title={BROKEN_HINTS.leftover}>{broken.leftover} {BROKEN_LABELS.leftover}</Chip>}
                {broken.mixed > 0 && <Chip tone="amber" title={BROKEN_HINTS.mixed}>{broken.mixed} {BROKEN_LABELS.mixed}</Chip>}
                {broken.other > 0 && <Chip tone="rose" title={BROKEN_HINTS.other}>{broken.other} {BROKEN_LABELS.other}</Chip>}
            </div>
            {toCount(row.total_lines) > 0 && (
                <span className="ml-auto text-[10px] text-gray-400 tabular-nums">
                    {toCount(row.translated_lines)} / {toCount(row.total_lines)} lines translated
                </span>
            )}
        </li>
    );
};

const ProjectGroup = ({ project, isOpen, onToggle }) => {
    const { name, rows, counts, raw } = project;
    const changes = joinParts(repairParts(raw));
    return (
        <div className="rounded-xl border border-gray-100 dark:border-gray-800 overflow-hidden">
            <button
                type="button"
                onClick={onToggle}
                aria-expanded={isOpen}
                className="w-full flex flex-wrap items-center gap-x-2 gap-y-1.5 px-3 py-2.5 text-left bg-gray-50/60 dark:bg-gray-900/30 hover:bg-gray-100/70 dark:hover:bg-gray-800/50 transition-colors"
            >
                <span className="flex items-center gap-2 min-w-0">
                    {isOpen ? <ChevronDown size={16} className="text-gray-400 shrink-0" /> : <ChevronRight size={16} className="text-gray-400 shrink-0" />}
                    <span className="font-bold text-sm text-gray-900 dark:text-white truncate">{name}</span>
                </span>
                <span className="ml-auto flex flex-wrap items-center justify-end gap-1.5">
                    {raw.error && <Chip tone="rose" title={String(raw.error)}>could not be checked</Chip>}
                    {counts.incomplete > 0 && <Chip tone="orange" title="Episodes with untranslated lines">{counts.incomplete} incomplete</Chip>}
                    {counts.misplaced > 0 && <Chip tone="rose" title="Episodes with lines on the wrong line">{counts.misplaced} misaligned</Chip>}
                    {counts.broken > 0 && <Chip tone="rose" title="Episodes with garbled, leftover or mixed-alphabet text">{counts.broken} garbled</Chip>}
                    {counts.imported > 0 && <Chip tone="gray" title="Imported subtitles that do not match the source">{counts.imported} imported</Chip>}
                </span>
            </button>
            {isOpen && (
                <div className="px-3 pb-3 pt-1 space-y-2">
                    {raw.error && (
                        <p className="text-xs text-rose-600 dark:text-rose-400 flex items-start gap-1.5">
                            <AlertTriangle size={13} className="shrink-0 mt-0.5" /> Could not check this project: {formatErrorMessage(raw.error)}
                        </p>
                    )}
                    {(changes || raw.enqueue?.message) && (
                        <p className="text-[11px] text-emerald-700 dark:text-emerald-400">
                            {[changes, raw.enqueue?.message].filter(Boolean).join(' · ')}
                        </p>
                    )}
                    {rows.length > 0 && (
                        <ul className="max-h-72 overflow-y-auto divide-y divide-gray-100 dark:divide-gray-800">
                            {rows.map((row) => <EpisodeRow key={row.episode_name} projectName={name} row={row} />)}
                        </ul>
                    )}
                    <Link
                        to={`/project/${encodeURIComponent(name)}`}
                        className="inline-flex items-center gap-1 text-[11px] font-semibold text-indigo-600 dark:text-indigo-400 hover:underline"
                    >
                        Open project <ArrowRight size={11} />
                    </Link>
                </div>
            )}
        </div>
    );
};

const LibraryTranslationCheck = () => {
    const toast = useToast();
    const [job, setJob] = useState(null);           // the last state the server sent
    const [loaded, setLoaded] = useState(false);    // the first GET has answered
    const [loadFailed, setLoadFailed] = useState(false);
    const [starting, setStarting] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const [open, setOpen] = useState({});           // project name -> expanded?
    const alive = useRef(true);
    const inFlight = useRef(false);
    const prevStatus = useRef(null);

    // Takes a state from the server. A run we saw going finish (or fail) gets a toast; the state we
    // find on load never does.
    const applyState = useCallback((data) => {
        if (!alive.current || !isState(data)) return;
        const before = prevStatus.current;
        prevStatus.current = data.status;
        setJob(data);
        setLoadFailed(false);
        if (before === 'running' && data.status === 'done') {
            const [kind, message] = finishedToast(data);
            toast[kind](message);
        } else if (before === 'running' && data.status === 'error') {
            toast.error(`Library check failed: ${formatErrorMessage(data.error) || 'unknown error'}`);
        }
    }, [toast]);

    const fetchState = useCallback(async () => {
        if (inFlight.current) return;
        inFlight.current = true;
        try {
            const res = await api.getLibraryAudit();
            applyState(res.data);
        } catch {
            // A failed poll is not fatal (the next tick tries again); the load error is shown below.
            if (alive.current) setLoadFailed(true);
        } finally {
            inFlight.current = false;
            if (alive.current) setLoaded(true);
        }
    }, [applyState]);

    useEffect(() => {
        alive.current = true;
        fetchState();
        return () => { alive.current = false; };
    }, [fetchState]);

    // Poll every 2 s while a run is going; skip ticks while the tab is hidden and catch up as soon as
    // it is visible again. The interval is cleared when the run ends or the page unmounts.
    const running = job?.status === 'running';
    useEffect(() => {
        if (!running) return undefined;
        const tick = () => { if (!document.hidden) fetchState(); };
        const timer = setInterval(tick, POLL_MS);
        document.addEventListener('visibilitychange', tick);
        return () => {
            clearInterval(timer);
            document.removeEventListener('visibilitychange', tick);
        };
    }, [running, fetchState]);

    const startRun = useCallback(async (options) => {
        setConfirming(false);
        setStarting(true);
        try {
            const res = await api.startLibraryAudit(options);
            if (isState(res.data)) applyState(res.data);
            else await fetchState();
        } catch (err) {
            const body = err?.response?.data;
            if (err?.response?.status === 409) {
                // Already running (maybe started elsewhere): just follow it.
                toast.info('A library check is already running — following its progress.');
                if (isState(body)) applyState(body);
                else await fetchState();
            } else {
                toast.error(`Could not start the library check: ${formatErrorMessage(body?.detail ?? body?.message ?? err?.message) || 'unknown error'}`);
            }
        } finally {
            if (alive.current) setStarting(false);
        }
    }, [applyState, fetchState, toast]);

    const busy = starting || running;
    const result = running ? null : (job?.result || null);

    const projects = useMemo(() => {
        const list = (result?.projects || []).map(summarizeProject).filter((p) => p.visible);
        return list.sort((a, b) => b.issues - a.issues || String(a.name).localeCompare(String(b.name)));
    }, [result]);

    const totals = result?.totals || {};
    const stat = (totalKey, countKey) => Math.max(toCount(totals[totalKey]), projects.reduce((n, p) => n + p.counts[countKey], 0));
    const n = {
        checked: toCount(totals.episodes_checked),
        incomplete: stat('incomplete', 'incomplete'),
        misplaced: stat('misplaced', 'misplaced'),
        imported: stat('misplaced_imported', 'imported'),
        broken: stat('broken', 'broken'),
    };
    // Per-kind detail decides whether a plain repair has anything to do (it leaves mixed alphabets alone).
    const fixableLines = projects.reduce((sum, p) => sum + p.rows.reduce((s, r) => s + brokenGroups(r.broken).fixable, 0), 0);
    const mixedLines = projects.reduce((sum, p) => sum + p.rows.reduce((s, r) => s + brokenGroups(r.broken).mixed, 0), 0);
    const canRepair = n.broken > 0 && (fixableLines > 0 || mixedLines === 0);
    const canQueue = n.incomplete + n.misplaced + n.broken > 0;
    const changes = result ? libraryChanges(result) : { text: '', enqueued: 0 };
    const showQueueLink = canQueue || changes.enqueued > 0;

    const progress = job?.progress || {};
    const total = toCount(progress.total);
    const done = total > 0 ? Math.min(toCount(progress.done), total) : toCount(progress.done);
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;

    const isOpen = (name) => open[name] ?? projects.length === 1;
    const toggle = (name) => setOpen((prev) => ({ ...prev, [name]: !isOpen(name) }));
    const setAllOpen = (value) => setOpen(Object.fromEntries(projects.map((p) => [p.name, value])));
    const allOpen = projects.length > 0 && projects.every((p) => isOpen(p.name));

    return (
        <div className="bg-white dark:bg-gray-850 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm p-5 space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
                <div className="flex items-start gap-3">
                    <Languages className="text-purple-500 mt-0.5 shrink-0" size={22} />
                    <div>
                        <h2 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">Library translation check</h2>
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                            Looks through every translated episode of every project for untranslated lines, lines that carry the translation of
                            another line, and garbled or leftover prompt text. Nothing changes unless you repair.
                        </p>
                    </div>
                </div>
                <button
                    type="button"
                    onClick={() => startRun({ repair: false, requeue: false, misplaced: true })}
                    disabled={busy}
                    className={`${BUTTON} shrink-0 bg-indigo-500/10 border-indigo-500/30 text-indigo-600 dark:text-indigo-400 hover:bg-indigo-500/20`}
                    title="Scan the whole library (read-only)"
                >
                    {busy ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
                    {running ? `${activityLabel(job.options)}…` : 'Check library'}
                </button>
            </div>

            {running && (
                <div className="space-y-1.5">
                    <div className="flex items-center justify-between gap-3 text-xs font-semibold text-gray-600 dark:text-gray-300">
                        <span className="flex items-center gap-1.5 min-w-0">
                            <Loader2 size={12} className="animate-spin text-purple-500 shrink-0" />
                            <span className="truncate">
                                {progress.project ? `${activityLabel(job.options)} ${progress.project}` : `${activityLabel(job.options)}…`}
                            </span>
                        </span>
                        {total > 0 && <span className="tabular-nums shrink-0">{done} / {total}</span>}
                    </div>
                    <div
                        className="h-2 rounded-full bg-gray-100 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 overflow-hidden"
                        role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
                    >
                        <div
                            className={`h-full rounded-full bg-gradient-to-r from-purple-500 to-indigo-500 transition-all duration-500 ${total > 0 ? '' : 'animate-pulse'}`}
                            style={{ width: total > 0 ? `${Math.max(pct, 3)}%` : '100%' }}
                        />
                    </div>
                </div>
            )}

            {!loaded && (
                <div className="flex items-center gap-2 text-xs text-gray-400">
                    <Loader2 size={12} className="animate-spin" /> Loading the last result…
                </div>
            )}

            {loaded && loadFailed && !job && (
                <p className="text-xs text-amber-600 dark:text-amber-400 flex items-center gap-1.5">
                    <AlertTriangle size={13} className="shrink-0" /> Could not load the last library check. You can still start a new one.
                </p>
            )}

            {job?.status === 'error' && (
                <div className="flex items-start gap-2 p-3 rounded-xl border border-rose-200 dark:border-rose-900/50 bg-rose-50/60 dark:bg-rose-950/20 text-xs text-rose-700 dark:text-rose-400">
                    <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                    <span>The last library check failed: {formatErrorMessage(job.error) || 'unknown error'}</span>
                </div>
            )}

            {loaded && job && !running && !result && job.status !== 'error' && (
                <p className="text-xs text-gray-400">
                    {job.status === 'idle'
                        ? 'No library check has been run yet. Press “Check library” to scan every project.'
                        : 'The last library check left no result. Press “Check library” to run it again.'}
                </p>
            )}

            {result && (
                <>
                    <p className="text-[11px] text-gray-400">
                        {runLabel(job.options)} finished {fmtWhen(job.finished_at)}
                        {fmtTook(job.started_at, job.finished_at) ? ` · took ${fmtTook(job.started_at, job.finished_at)}` : ''}
                        {toCount(totals.projects) ? ` · ${toCount(totals.projects)} project(s)` : ''}
                    </p>

                    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3">
                        <Stat label="Episodes checked" value={n.checked} />
                        <Stat label="With untranslated lines" value={n.incomplete} tone="orange"
                            title="Translated episodes that still contain English dialogue or untranslated signs and info cards" />
                        <Stat label="With lines on the wrong line" note="Omnisub translations" value={n.misplaced} tone="rose"
                            title="Episodes translated by Omnisub whose lines carry the translation of another line" />
                        <Stat label="Imported mismatches" note="imported subtitles" value={n.imported} tone="gray"
                            title="Imported subtitle files that do not line up with the source (another cut or timing). They are never changed or queued." />
                        <Stat label="With garbled text" value={n.broken} tone="rose"
                            title="Episodes with garbled encoding, leftover prompt text or mixed alphabets" />
                    </div>

                    {changes.text && (
                        <div className="flex items-start gap-2 p-3 rounded-xl border border-emerald-200 dark:border-emerald-900/50 bg-emerald-50/60 dark:bg-emerald-950/20 text-xs text-emerald-800 dark:text-emerald-300">
                            <CheckCircle2 size={14} className="shrink-0 mt-0.5" />
                            <span>{changes.text}. Run the check again to see what is left.</span>
                        </div>
                    )}

                    {projects.length === 0 ? (
                        !changes.text && (
                            <p className="flex items-center gap-1.5 text-sm font-semibold text-emerald-600 dark:text-emerald-400">
                                <CheckCircle2 size={16} /> Every translated episode in the library looks complete.
                            </p>
                        )
                    ) : (
                        <div className="space-y-2">
                            <div className="flex items-center justify-between">
                                <h3 className="text-[11px] font-bold text-gray-400 uppercase tracking-wider">
                                    Projects with issues ({projects.length})
                                </h3>
                                {projects.length > 1 && (
                                    <button type="button" onClick={() => setAllOpen(!allOpen)}
                                        className="text-[11px] font-semibold text-indigo-600 dark:text-indigo-400 hover:underline">
                                        {allOpen ? 'Collapse all' : 'Expand all'}
                                    </button>
                                )}
                            </div>
                            {projects.map((p) => (
                                <ProjectGroup key={p.name} project={p} isOpen={isOpen(p.name)} onToggle={() => toggle(p.name)} />
                            ))}
                            {n.imported > 0 && (
                                <p className="text-[11px] text-gray-400">
                                    Imported subtitles that do not match the source are left as they are and never queued; retranslate them in full if needed.
                                </p>
                            )}
                        </div>
                    )}

                    {(canRepair || canQueue || showQueueLink) && (
                        <div className="flex flex-wrap items-center gap-2 pt-1">
                            {canRepair && (
                                <button
                                    type="button"
                                    onClick={() => startRun({ repair: true, requeue: false, misplaced: true })}
                                    disabled={busy}
                                    className={`${BUTTON} bg-amber-500/10 border-amber-500/30 text-amber-600 dark:text-amber-400 hover:bg-amber-500/20`}
                                    title="Fixes garbled encoding and clears leftover prompt text across the library. Lines with mixed alphabets are left in place. Nothing is queued."
                                >
                                    <Wrench size={14} /> Repair garbled text
                                </button>
                            )}
                            {canQueue && (
                                <button
                                    type="button"
                                    onClick={() => setConfirming(true)}
                                    disabled={busy || confirming}
                                    className={`${BUTTON} bg-purple-500/10 border-purple-500/30 text-purple-600 dark:text-purple-400 hover:bg-purple-500/20`}
                                    title="Repair, clear what needs retranslating (with backups) and queue it for the agent runner"
                                >
                                    <Bot size={14} /> Repair &amp; queue fixes
                                </button>
                            )}
                            {showQueueLink && (
                                <Link to="/queue" className="flex items-center gap-1 text-sm font-semibold text-indigo-600 dark:text-indigo-400 hover:underline">
                                    Open Queue to run the agent <ArrowRight size={14} />
                                </Link>
                            )}
                        </div>
                    )}

                    {confirming && (
                        <div className="p-3 rounded-xl border border-amber-200 dark:border-amber-900/50 bg-amber-50/60 dark:bg-amber-950/20 text-xs space-y-2">
                            <div className="font-bold text-amber-800 dark:text-amber-300 flex items-center gap-1.5">
                                <AlertTriangle size={14} /> Repair and queue fixes for the whole library?
                            </div>
                            <ul className="list-disc pl-5 space-y-0.5 text-amber-900/80 dark:text-amber-200/80">
                                <li>Garbled encoding is repaired in place, without losing any text.</li>
                                <li>
                                    Leftover prompt text, lines on the wrong line and lines with mixed alphabets are cleared so the agent can
                                    retranslate them. The old text is saved in a backup next to each episode.
                                </li>
                                <li>Those lines, plus anything still untranslated, are queued for the agent. Start the agent from the Queue page.</li>
                                <li>Imported subtitles are only repaired for encoding. They are never cleared or queued.</li>
                            </ul>
                            <div className="flex items-center justify-end gap-2">
                                <button type="button" onClick={() => setConfirming(false)}
                                    className="px-3 py-1.5 text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white rounded-lg">
                                    Cancel
                                </button>
                                <button type="button" onClick={() => startRun({ repair: true, requeue: true, misplaced: true })} disabled={busy}
                                    className="px-3 py-1.5 font-bold text-white bg-purple-600 hover:bg-purple-700 rounded-lg disabled:opacity-50">
                                    Yes, repair &amp; queue
                                </button>
                            </div>
                        </div>
                    )}
                </>
            )}
        </div>
    );
};

export default LibraryTranslationCheck;
