import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, EyeOff, Film, FolderPlus, Languages, Library, RefreshCw, Search, Settings2, Tv, Folder, Eye } from 'lucide-react';
import { api } from '../../api';
import { useJobs } from '../../context/JobContext';
import { usePoll } from '../../hooks/usePoll';
import { useToast } from '../../context/ToastContext';
import { Button, Card, ConfirmDialog, EmptyState, INPUT, Page, PageHeader, ProgressBar, Spinner, Tabs } from '../ui';
import EmbeddedTracksModal from '../project/modals/EmbeddedTracksModal';
import LibrarySyncModal from '../LibrarySyncModal';
import LibraryRow from './LibraryRow';
import NewProjectModal from './NewProjectModal';
import { describeEnqueueResult, isMovieItem, itemPriority } from './libraryUtils';
import { getSavedSyncOptions } from './syncOptions';

const ACTIVE_STATES = ['pending', 'queued', 'running', 'paused_daily_limit'];
const PRIORITY_RANK = { high: 0, normal: 1, low: 2, ignored: 3 };

const byName = (a, b) => (a.show_name || a.name || '').localeCompare(b.show_name || b.name || '');
const missingOf = (item) => Math.max(0, (item.episode_count || 0) - (item.translated_count || 0));

export default function LibraryPage() {
    const toast = useToast();
    const { activeJobs, addJob, snapshot } = useJobs();

    const [library, setLibrary] = useState(null);
    const [manual, setManual] = useState([]);
    const [loading, setLoading] = useState(true);
    const [queueItems, setQueueItems] = useState([]);
    const [arrConfigured, setArrConfigured] = useState(true); // assume yes until checked, to avoid a flash
    const [defaultLanguage, setDefaultLanguage] = useState('Greek');

    const [tab, setTab] = useState('series');
    const [search, setSearch] = useState('');
    const [filterStatus, setFilterStatus] = useState('all');
    const [filterPriority, setFilterPriority] = useState('all');
    const [sortBy, setSortBy] = useState('name');
    const [selected, setSelected] = useState(() => new Set());

    const [syncing, setSyncing] = useState(false);
    const [syncJobId, setSyncJobId] = useState(null);
    const [syncModalOpen, setSyncModalOpen] = useState(false);
    const [newProjectOpen, setNewProjectOpen] = useState(false);
    const [confirmAll, setConfirmAll] = useState(false);
    const [translatingAll, setTranslatingAll] = useState(false);
    const [translatingProject, setTranslatingProject] = useState('');
    const [bulkBusy, setBulkBusy] = useState(false);
    const [embeddedTarget, setEmbeddedTarget] = useState(null);

    // ------------------------------------------------------------------ data

    const loadLibrary = useCallback(async () => {
        try {
            const res = await api.getArrLibrary();
            setLibrary(res.data);
        } catch (err) {
            console.error('Failed to load library', err);
            toast.error('Could not load the library.');
        } finally {
            setLoading(false);
        }
    }, [toast]);

    const loadManual = useCallback(async () => {
        try {
            const res = await api.getProjects();
            setManual((res.data || []).filter(p => !p.arr_source));
        } catch (err) {
            console.error('Failed to load projects', err);
        }
    }, []);

    // An unreachable queue is reported on the Activity page; a failure here only makes the poller back off.
    const loadQueue = useCallback(async ({ signal } = {}) => {
        const res = await api.getQueue(false, undefined, signal);
        setQueueItems(res.data?.items || []);
    }, []);

    useEffect(() => {
        loadLibrary();
        loadManual();
        api.getArrStatus()
            .then(res => setArrConfigured(!!(res.data?.sonarr?.configured || res.data?.radarr?.configured)))
            .catch(() => {});
        api.getSettings()
            .then(res => { if (res.data?.default_target_language) setDefaultLanguage(res.data.default_target_language); })
            .catch(() => {});
    }, [loadLibrary, loadManual]);

    usePoll(loadQueue, { intervalMs: 30000, hiddenIntervalMs: 90000, refreshOn: ['queue_updated', 'agent_submission'] });

    // Counts change when queue items finish. The activity snapshot (5 s) is fresher than the list (30 s).
    const activeCount = useMemo(() => {
        const summary = snapshot?.summary;
        if (summary) return (summary.pending || 0) + (summary.running || 0) + (summary.queued || 0) + (summary.paused_daily_limit || 0);
        return queueItems.filter(i => ACTIVE_STATES.includes(i.status)).length;
    }, [queueItems, snapshot]);
    const prevActive = useRef(0);
    useEffect(() => {
        if (activeCount < prevActive.current) loadLibrary();
        prevActive.current = activeCount;
    }, [activeCount, loadLibrary]);

    // The library sync runs as a background job.
    useEffect(() => {
        if (!syncJobId) return;
        const job = activeJobs[syncJobId];
        if (job?.status === 'completed') {
            setSyncing(false);
            loadLibrary();
            toast.success(job.message || 'Library sync finished.');
        } else if (job?.status === 'failed') {
            setSyncing(false);
            setSyncJobId(null);
            toast.error(job.message || 'Library sync failed.');
        }
    }, [activeJobs, syncJobId, loadLibrary, toast]);

    const queueByProject = useMemo(() => {
        const map = {};
        for (const q of queueItems) {
            if (!ACTIVE_STATES.includes(q.status)) continue;
            const entry = map[q.project_name] || (map[q.project_name] = { queued: 0, running: 0 });
            if (q.status === 'running') entry.running += 1;
            else entry.queued += 1;
        }
        return map;
    }, [queueItems]);

    // ------------------------------------------------------------------ tabs and filters

    const groups = useMemo(() => {
        const manualIgnored = manual.filter(p => itemPriority(p) === 'ignored');
        const manualActive = manual.filter(p => itemPriority(p) !== 'ignored');
        return {
            series: library?.series || [],
            movies: library?.movies || [],
            other: manualActive,
            ignored: [...(library?.disabled || []), ...manualIgnored],
        };
    }, [library, manual]);

    const summary = useMemo(() => {
        const items = [...groups.series, ...groups.movies];
        const total = items.reduce((n, i) => n + (i.episode_count || 0), 0);
        const done = items.reduce((n, i) => n + (i.translated_count || 0), 0);
        return { titles: items.length, total, done, missing: Math.max(0, total - done), pct: total ? Math.round((done / total) * 100) : 0 };
    }, [groups]);

    const visible = useMemo(() => {
        let list = groups[tab] || [];
        const q = search.trim().toLowerCase();
        if (q) list = list.filter(i => (i.show_name || i.name || '').toLowerCase().includes(q));
        if (filterStatus === 'translated') list = list.filter(i => i.episode_count > 0 && i.translated_count >= i.episode_count);
        else if (filterStatus === 'pending') list = list.filter(i => i.episode_count > 0 && i.translated_count < i.episode_count);
        else if (filterStatus === 'no_subs') list = list.filter(i => i.episode_count === 0);
        else if (filterStatus === 'queued') list = list.filter(i => queueByProject[i.name]);
        if (filterPriority !== 'all') list = list.filter(i => itemPriority(i) === filterPriority);
        const sorted = [...list];
        if (sortBy === 'priority') sorted.sort((a, b) => (PRIORITY_RANK[itemPriority(a)] - PRIORITY_RANK[itemPriority(b)]) || byName(a, b));
        else if (sortBy === 'missing') sorted.sort((a, b) => (missingOf(b) - missingOf(a)) || byName(a, b));
        else sorted.sort(byName);
        return sorted;
    }, [groups, tab, search, filterStatus, filterPriority, sortBy, queueByProject]);

    const tabs = [
        { id: 'series', label: 'Series', icon: Tv, count: groups.series.length },
        { id: 'movies', label: 'Movies', icon: Film, count: groups.movies.length },
        { id: 'other', label: 'Other', icon: Folder, count: groups.other.length },
        { id: 'ignored', label: 'Ignored', icon: EyeOff, count: groups.ignored.length },
    ];

    const changeTab = (id) => { setTab(id); setSelected(new Set()); };

    // ------------------------------------------------------------------ actions

    const toggle = useCallback((name) => {
        setSelected(prev => {
            const next = new Set(prev);
            if (next.has(name)) next.delete(name);
            else next.add(name);
            return next;
        });
    }, []);

    const allSelected = visible.length > 0 && visible.every(i => selected.has(i.name));
    const toggleAll = () => setSelected(allSelected ? new Set() : new Set(visible.map(i => i.name)));

    const startSync = async (options) => {
        setSyncing(true);
        try {
            let res;
            if (options.source === 'sonarr') res = await api.syncSonarr(options);
            else if (options.source === 'radarr') res = await api.syncRadarr(options);
            else res = await api.syncArrAll(options);
            if (res.data?.job_id) {
                setSyncJobId(res.data.job_id);
                const label = options.source === 'sonarr' ? 'Sonarr' : options.source === 'radarr' ? 'Radarr' : 'Sonarr and Radarr';
                addJob(res.data.job_id, 'arr_sync', `Syncing ${label}...`, { link: '/' });
            } else {
                setSyncing(false);
            }
        } catch (err) {
            setSyncing(false);
            toast.error(`Could not start the sync: ${err.response?.data?.detail || err.message}`);
        }
    };

    const translateAllMissing = async () => {
        setTranslatingAll(true);
        try {
            const res = await api.enqueueAllMissing();
            const queued = res.data?.enqueued_count ?? 0;
            if (res.data?.job_id) {
                addJob(res.data.job_id, 'translate_missing_library', 'Looking for subtitles inside videos...', { link: '/activity' });
            }
            toast.success(`${queued ? `Queued ${queued} now` : 'Nothing with an imported subtitle to queue'}; looking for subtitles inside videos in the background.`);
            loadQueue();
        } catch (err) {
            toast.error(`Could not queue the missing translations: ${err.response?.data?.detail || err.message}`);
        } finally {
            setTranslatingAll(false);
            setConfirmAll(false);
        }
    };

    const translateMissing = async (item) => {
        setTranslatingProject(item.name);
        try {
            const res = await api.enqueueMissing(item.name);
            toast.success(`${item.show_name || item.name}: ${describeEnqueueResult(res.data || {})}`);
            if (res.data?.error) toast.warning(res.data.error);
            loadQueue();
        } catch (err) {
            toast.error(`Could not queue ${item.show_name || item.name}: ${err.response?.data?.detail || err.message}`);
        } finally {
            setTranslatingProject('');
        }
    };

    const setPriority = async (names, priority) => {
        try {
            if (names.length === 1) await api.updateProjectPriority(names[0], priority);
            else await api.bulkUpdateProjectPriority(names, priority);
            toast.success(names.length === 1 ? `Priority set to ${priority}.` : `${names.length} titles set to ${priority}.`);
            await Promise.all([loadLibrary(), loadManual()]);
            loadQueue();
        } catch (err) {
            toast.error(`Could not change the priority: ${err.response?.data?.detail || err.message}`);
        }
    };

    const bulkPriority = async (priority) => {
        setBulkBusy(true);
        await setPriority(Array.from(selected), priority);
        setSelected(new Set());
        setBulkBusy(false);
    };

    const bulkTranslate = async () => {
        setBulkBusy(true);
        const items = visible.filter(i => selected.has(i.name) && i.arr_source && itemPriority(i) !== 'ignored');
        let queued = 0;
        for (const item of items) {
            try {
                const res = await api.enqueueMissing(item.name);
                queued += (res.data?.queued_for_translation ?? res.data?.enqueued_count ?? 0) + (res.data?.queued_for_extraction ?? 0);
            } catch (err) {
                toast.error(`${item.show_name || item.name}: ${err.response?.data?.detail || err.message}`);
            }
        }
        toast.success(`Queued ${queued} item${queued === 1 ? '' : 's'} from ${items.length} title${items.length === 1 ? '' : 's'}.`);
        setSelected(new Set());
        setBulkBusy(false);
        loadQueue();
    };

    const extractFromVideo = async (item) => {
        try {
            const res = await api.batchExtractEmbedded(item.name, {});
            if (res.data?.job_id) {
                addJob(res.data.job_id, 'batch_extract_embedded', `Extracting subtitles for ${item.show_name || item.name}...`, { link: `/project/${encodeURIComponent(item.name)}` });
            }
            toast.success('Extracting subtitles from the video files.');
        } catch (err) {
            toast.error(`Could not start the extraction: ${err.response?.data?.detail || err.message}`);
        }
    };

    const menuFor = (item) => [
        { label: 'Stop ignoring', icon: Eye, hidden: itemPriority(item) !== 'ignored', onClick: () => setPriority([item.name], 'normal') },
        { label: 'Inspect tracks in the video', icon: Search, hidden: !isMovieItem(item) || !item.arr_source || itemPriority(item) === 'ignored', onClick: () => setEmbeddedTarget({ project: item.name, episode: item.name }) },
        { label: 'Extract subtitles from videos', icon: Film, hidden: !item.arr_source || itemPriority(item) === 'ignored', onClick: () => extractFromVideo(item) },
    ];

    // ------------------------------------------------------------------ render

    const syncJob = syncJobId ? activeJobs[syncJobId] : null;
    const subtitle = library
        ? [
            `${summary.titles} title${summary.titles === 1 ? '' : 's'}`,
            summary.total ? `${summary.pct}% translated` : null,
            summary.missing ? `${summary.missing} episode${summary.missing === 1 ? '' : 's'} missing` : null,
            activeCount ? `${activeCount} in queue` : null,
            library.last_sync ? `synced ${new Date(library.last_sync).toLocaleString()}` : 'not synced yet',
        ].filter(Boolean).join(' · ')
        : 'Loading...';

    const emptyFor = () => {
        if (search || filterStatus !== 'all' || filterPriority !== 'all') {
            return <EmptyState icon={Search} title="Nothing matches these filters" description="Clear the search or the filters to see everything in this tab." />;
        }
        if (tab === 'other') {
            return (
                <EmptyState
                    icon={Folder}
                    title="No projects of your own"
                    description="Projects that do not come from Sonarr or Radarr appear here, for subtitle files you add yourself."
                    action={<Button variant="primary" icon={FolderPlus} onClick={() => setNewProjectOpen(true)}>New project</Button>}
                />
            );
        }
        if (tab === 'ignored') {
            return <EmptyState icon={EyeOff} title="No ignored titles" description="Titles set to Ignored are skipped by automatic translation. They stay in the library and you can still translate them by hand." />;
        }
        if (!arrConfigured) {
            return (
                <EmptyState
                    icon={Library}
                    title="Connect Sonarr or Radarr"
                    description="OmbiSub imports your library from them. You can also add a project of your own in the Other tab."
                    action={<Link to="/settings?section=connections"><Button variant="primary">Open connection settings</Button></Link>}
                />
            );
        }
        return (
            <EmptyState
                icon={tab === 'movies' ? Film : Tv}
                title={library?.last_sync ? `No ${tab === 'movies' ? 'movies' : 'series'} found` : 'The library is empty'}
                description={library?.last_sync ? 'Run a sync if you added titles recently.' : 'Run a sync to import your titles from Sonarr and Radarr.'}
                action={<Button variant="primary" icon={RefreshCw} loading={syncing} onClick={() => startSync(getSavedSyncOptions())}>Sync now</Button>}
            />
        );
    };

    return (
        <Page>
            <PageHeader
                title="Library"
                subtitle={subtitle}
                actions={(
                    <>
                        <Button variant="primary" icon={RefreshCw} loading={syncing} disabled={!arrConfigured} onClick={() => startSync(getSavedSyncOptions())}
                            title={arrConfigured ? 'Import new titles and episodes from Sonarr and Radarr' : 'Connect Sonarr or Radarr first'}>
                            {syncing ? 'Syncing...' : 'Sync now'}
                        </Button>
                        <Button icon={Languages} disabled={!arrConfigured || summary.titles === 0} onClick={() => setConfirmAll(true)}>
                            Translate all missing
                        </Button>
                        <Button variant="ghost" icon={Settings2} onClick={() => setSyncModalOpen(true)} title="Choose what a sync scans">
                            <span className="hidden sm:inline">Sync options</span>
                            <span className="sm:hidden">Options</span>
                        </Button>
                    </>
                )}
            />

            {syncing && (
                <Card className="mb-5">
                    <div className="flex items-center gap-3">
                        <RefreshCw size={16} className="shrink-0 animate-spin text-indigo-500" />
                        <div className="min-w-0 flex-1">
                            <p className="truncate text-sm text-gray-700 dark:text-gray-200">{syncJob?.message || 'Starting the sync...'}</p>
                            <ProgressBar value={syncJob?.progress || 0} className="mt-2" />
                        </div>
                        <span className="text-xs tabular-nums text-gray-500">{Math.round(syncJob?.progress || 0)}%</span>
                    </div>
                </Card>
            )}

            <Tabs tabs={tabs} value={tab} onChange={changeTab} />

            <div className="mb-4 flex flex-wrap items-center gap-2">
                <div className="relative min-w-[12rem] flex-1 sm:max-w-xs">
                    <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                    <input
                        type="search"
                        className={`${INPUT} pl-8`}
                        placeholder="Search titles"
                        aria-label="Search titles"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                    />
                </div>
                <select className={`${INPUT} !w-auto`} aria-label="Filter by status" value={filterStatus} onChange={e => setFilterStatus(e.target.value)}>
                    <option value="all">Any status</option>
                    <option value="translated">Complete</option>
                    <option value="pending">Not complete</option>
                    <option value="no_subs">No subtitles yet</option>
                    <option value="queued">In the queue</option>
                </select>
                <select className={`${INPUT} !w-auto`} aria-label="Filter by priority" value={filterPriority} onChange={e => setFilterPriority(e.target.value)}>
                    <option value="all">Any priority</option>
                    <option value="high">High</option>
                    <option value="normal">Normal</option>
                    <option value="low">Low</option>
                    <option value="ignored">Ignored</option>
                </select>
                <select className={`${INPUT} !w-auto`} aria-label="Sort" value={sortBy} onChange={e => setSortBy(e.target.value)}>
                    <option value="name">Sort by name</option>
                    <option value="priority">Sort by priority</option>
                    <option value="missing">Sort by most missing</option>
                </select>
                {tab === 'other' && (
                    <Button variant="primary" icon={FolderPlus} className="ml-auto" onClick={() => setNewProjectOpen(true)}>New project</Button>
                )}
            </div>

            {loading ? (
                <Spinner label="Loading the library..." />
            ) : visible.length === 0 ? (
                <Card padded={false}>{emptyFor()}</Card>
            ) : (
                <Card padded={false} bodyClassName="">
                    <div className="flex items-center gap-3 border-b border-gray-100 px-4 py-2 text-xs text-gray-500 dark:border-gray-750 dark:text-gray-400 sm:px-5">
                        <input
                            type="checkbox"
                            checked={allSelected}
                            onChange={toggleAll}
                            className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 dark:border-gray-600"
                            aria-label="Select all shown titles"
                        />
                        <span>{visible.length} title{visible.length === 1 ? '' : 's'}{selected.size ? ` · ${selected.size} selected` : ''}</span>
                    </div>
                    <ul className="divide-y divide-gray-100 dark:divide-gray-750">
                        {visible.map(item => (
                            <LibraryRow
                                key={item.name}
                                item={item}
                                queued={queueByProject[item.name]?.queued || 0}
                                running={queueByProject[item.name]?.running || 0}
                                selected={selected.has(item.name)}
                                onToggle={toggle}
                                onPriorityChange={(name, priority) => setPriority([name], priority)}
                                onTranslateMissing={translateMissing}
                                translating={translatingProject === item.name}
                                menuItems={menuFor(item)}
                            />
                        ))}
                    </ul>
                </Card>
            )}

            {syncJob?.status === 'completed' && syncJob.result?.unreachable_paths?.length > 0 && (
                <Card className="mt-5" title="Some subtitle paths could not be reached" subtitle="Check the path mappings in the connection settings.">
                    <ul className="space-y-1 font-mono text-xs text-amber-700 dark:text-amber-400">
                        {syncJob.result.unreachable_paths.map(p => (
                            <li key={p} className="flex items-center gap-2">
                                <AlertTriangle size={12} className="shrink-0" />
                                <Link to={`/settings?section=connections&test_path=${encodeURIComponent(p)}`} className="break-all underline">{p}</Link>
                            </li>
                        ))}
                    </ul>
                </Card>
            )}

            {selected.size > 0 && (
                <div className="fixed inset-x-3 bottom-4 z-40 mx-auto flex max-w-2xl flex-wrap items-center justify-center gap-2 rounded-xl border border-gray-200 bg-white p-3 shadow-lg dark:border-gray-700 dark:bg-gray-850">
                    <span className="mr-1 text-sm font-medium text-gray-800 dark:text-gray-100">{selected.size} selected</span>
                    <Button size="sm" disabled={bulkBusy} onClick={() => bulkPriority('high')}>High</Button>
                    <Button size="sm" disabled={bulkBusy} onClick={() => bulkPriority('normal')}>Normal</Button>
                    <Button size="sm" disabled={bulkBusy} onClick={() => bulkPriority('low')}>Low</Button>
                    <Button size="sm" disabled={bulkBusy} onClick={() => bulkPriority('ignored')}>Ignore</Button>
                    <Button size="sm" variant="primary" icon={Languages} loading={bulkBusy} onClick={bulkTranslate}>Translate missing</Button>
                    <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>Clear</Button>
                </div>
            )}

            <ConfirmDialog
                open={confirmAll}
                title="Translate all missing?"
                message={summary.missing
                    ? `${summary.missing} episode${summary.missing === 1 ? '' : 's'} across ${summary.titles} titles have no translation yet. They are queued now. OmbiSub also looks for subtitles inside your video files in the background and queues those.`
                    : 'Every imported episode is translated. OmbiSub will still look for subtitles inside your video files and queue any it finds.'}
                confirmLabel="Queue them"
                busy={translatingAll}
                onConfirm={translateAllMissing}
                onCancel={() => setConfirmAll(false)}
            />

            <LibrarySyncModal
                isOpen={syncModalOpen}
                onClose={() => setSyncModalOpen(false)}
                onConfirm={startSync}
            />

            <NewProjectModal
                open={newProjectOpen}
                onClose={() => setNewProjectOpen(false)}
                onCreated={loadManual}
                parents={manual.filter(p => p.type === 'parent')}
                defaultLanguage={defaultLanguage}
            />

            <EmbeddedTracksModal
                isOpen={!!embeddedTarget}
                onClose={() => setEmbeddedTarget(null)}
                projectName={embeddedTarget?.project}
                episodeName={embeddedTarget?.episode}
                onExtractionComplete={loadLibrary}
            />
        </Page>
    );
}
