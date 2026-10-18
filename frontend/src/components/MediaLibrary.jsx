import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import { api, apiClient } from '../api';
import { useJobs } from '../context/JobContext';
import { useToast } from '../context/ToastContext';
import {
    RefreshCw, Tv, Film, EyeOff, ChevronDown, ChevronRight,
    Languages, Check, AlertCircle, Clock, Ban, ExternalLink,
    Library, Filter, Search, Play, Pause, Activity,
    Flame, ArrowDown, CheckSquare
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import EmbeddedTracksModal from './project/modals/EmbeddedTracksModal';
import LibrarySyncModal from './LibrarySyncModal';

const TABS = { SHOWS: 'shows', MOVIES: 'movies', DISABLED: 'disabled' };

const PriorityBadgeSelector = ({ priority = 'normal', onChange, disabled = false }) => {
    const tier = priority || 'normal';
    const config = {
        high: { label: 'High', icon: Flame, className: 'bg-amber-100 text-amber-800 border-amber-300 dark:bg-amber-950/50 dark:text-amber-300 dark:border-amber-800' },
        normal: { label: 'Normal', icon: Clock, className: 'bg-slate-100 text-slate-700 border-slate-300 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-700' },
        low: { label: 'Low', icon: ArrowDown, className: 'bg-blue-100 text-blue-800 border-blue-300 dark:bg-blue-950/50 dark:text-blue-300 dark:border-blue-800' },
        ignored: { label: 'Ignored', icon: EyeOff, className: 'bg-rose-100 text-rose-800 border-rose-300 dark:bg-rose-950/50 dark:text-rose-300 dark:border-rose-800' },
    }[tier] || { label: 'Normal', icon: Clock, className: 'bg-slate-100 text-slate-700 border-slate-300 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-700' };

    const Icon = config.icon;

    return (
        <div className="relative inline-flex items-center" onClick={(e) => e.stopPropagation()}>
            <span className={`flex items-center gap-1 text-[11px] font-bold px-2 py-0.5 rounded-full border shadow-xs transition-colors ${config.className}`}>
                <Icon size={11} />
                <span>{config.label}</span>
                <ChevronDown size={10} className="ml-0.5 opacity-60" />
            </span>
            <select
                value={tier}
                disabled={disabled}
                onChange={(e) => onChange(e.target.value)}
                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer text-xs"
                title={`Change priority (currently ${config.label})`}
            >
                <option value="high">🔥 High — Translate First</option>
                <option value="normal">⏱ Normal</option>
                <option value="low">⬇ Low — Translate Last</option>
                <option value="ignored">🚫 Ignored — Do Not Translate</option>
            </select>
        </div>
    );
};

const StatusBadge = ({ episodeCount, translatedCount, disabled }) => {
    if (disabled) return <span className="flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400"><Ban size={10} /> Disabled</span>;
    if (episodeCount === 0) return <span className="flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400"><Clock size={10} /> No subs</span>;
    if (translatedCount >= episodeCount) return <span className="flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-green-50 text-green-600 dark:bg-green-900/30 dark:text-green-400"><Check size={10} /> Complete</span>;
    if (translatedCount > 0) return <span className="flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400"><Languages size={10} /> {translatedCount}/{episodeCount}</span>;
    return <span className="flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400"><Clock size={10} /> Pending</span>;
};

const EpisodeRow = ({ ep, status, onTranslate, onInspectEmbedded, isSelected = false, onToggleSelect }) => {
    const hasSource = ep.has_source !== false && (ep.line_count > 0 || !!ep.metadata?.arr_sub_path);
    const isAss = ['ass', 'ssa'].includes((ep.metadata?.original_format || ep.metadata?.original_extension || '').toLowerCase());
    const isMuxed = !!ep.metadata?.embedded_extracted;
    const hasMedia = !!ep.metadata?.arr_media_path;

    const getBadge = () => {
        const base = "flex items-center gap-1.5 text-xs font-semibold px-2 py-0.5 rounded-full w-fit";
        if (!hasSource) {
            return <span className={`${base} bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-300 border border-amber-200/60 dark:border-amber-800/40`}><AlertCircle size={10} /> No Subtitle</span>;
        }
        switch (status) {
            case 'translated':
                return <span className={`${base} bg-green-50 text-green-700 dark:bg-green-950/20 dark:text-green-400`}><Check size={10} /> Complete</span>;
            case 'running':
                return <span className={`${base} bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400 animate-pulse`}><Activity size={10} className="animate-spin" /> Translating</span>;
            case 'queued':
                return <span className={`${base} bg-indigo-50 text-indigo-700 dark:bg-indigo-900/20 dark:text-indigo-400`}><Clock size={10} /> Queued</span>;
            case 'failed':
                return <span className={`${base} bg-rose-50 text-rose-700 dark:bg-rose-900/20 dark:text-rose-450`}><AlertCircle size={10} /> Failed</span>;
            default:
                return <span className={`${base} bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400`}><Clock size={10} /> Pending</span>;
        }
    };

    return (
        <div className={`flex items-center justify-between py-2 px-3 rounded-xl border shadow-sm text-sm transition-colors ${
            isSelected
                ? 'bg-indigo-50/70 dark:bg-indigo-950/30 border-indigo-300 dark:border-indigo-700'
                : 'bg-white dark:bg-gray-800/80 border-gray-150 dark:border-gray-700/60'
        }`}>
            <div className="flex items-center gap-2 min-w-0 flex-wrap">
                {onToggleSelect && (
                    <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={onToggleSelect}
                        className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500 cursor-pointer h-4 w-4 shrink-0 mr-0.5"
                    />
                )}
                <span className="font-semibold text-gray-800 dark:text-gray-200">{ep.name}</span>
                {hasSource && (
                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-tight ${
                        isAss
                            ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300'
                            : 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
                    }`}>
                        {isAss ? 'ASS' : 'SRT'}
                    </span>
                )}
                {isMuxed && (
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-purple-50 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300 flex items-center gap-0.5 uppercase tracking-tight">
                        <Film size={9} /> #{ep.metadata?.embedded_track?.index ?? ''}
                    </span>
                )}
                {ep.metadata?.episode_title && (
                    <span className="text-xs text-gray-400 dark:text-gray-500 truncate max-w-[120px] sm:max-w-[160px]" title={ep.metadata.episode_title}>
                        {ep.metadata.episode_title}
                    </span>
                )}
            </div>
            
            <div className="flex items-center gap-1.5 flex-shrink-0">
                {hasMedia && hasSource && (
                    <button
                        onClick={() => onInspectEmbedded(ep.name)}
                        className="p-1 text-purple-600 hover:text-purple-700 hover:bg-purple-50 dark:text-purple-400 dark:hover:bg-purple-900/30 rounded-lg transition-colors"
                        title="Inspect & extract embedded subtitle tracks from media container"
                    >
                        <Film size={14} />
                    </button>
                )}
                {getBadge()}
                {hasSource ? (
                    status !== 'translated' && status !== 'running' && status !== 'queued' && (
                        <button
                            onClick={() => onTranslate(ep.name)}
                            className="px-2.5 py-1 text-xs font-bold text-indigo-600 dark:text-indigo-400 bg-indigo-50 hover:bg-indigo-100 dark:bg-indigo-950 dark:hover:bg-indigo-900/40 rounded-lg transition-colors border border-indigo-100/50 dark:border-indigo-900/30"
                        >
                            Translate
                        </button>
                    )
                ) : (
                    hasMedia && (
                        <button
                            onClick={() => onInspectEmbedded(ep.name)}
                            className="px-2.5 py-1 text-xs font-bold text-purple-700 dark:text-purple-300 bg-purple-100 hover:bg-purple-200 dark:bg-purple-900/50 rounded-lg transition-colors border border-purple-200 dark:border-purple-800/40 flex items-center gap-1"
                            title="Extract subtitle from video"
                        >
                            <Film size={11} /> Extract
                        </button>
                    )
                )}
            </div>
        </div>
    );
};


const LibraryCard = ({
    item,
    onDisable,
    onEnable,
    onPriorityChange,
    queueData,
    refreshQueue,
    isProjectSelected = false,
    toggleProject,
    selectedEpisodes = new Set(),
    toggleEpisode,
    toggleShowEpisodes,
}) => {
    const [expanded, setExpanded] = useState(false);
    const [episodes, setEpisodes] = useState([]);
    const [loadingEpisodes, setLoadingEpisodes] = useState(false);
    const [embeddedModalEpisode, setEmbeddedModalEpisode] = useState(null);
    const toast = useToast();

    const currentPriority = item.translation_priority || (item.arr_disabled ? 'ignored' : 'normal');
    const isDisabled = item.arr_disabled || currentPriority === 'ignored';
    const isMovie = item.arr_media_type === 'movie' || item.type === 'movie';

    // Get queue items for this project
    const projectQueueItems = (queueData?.items || []).filter(q => q.project_name === item.name);
    const queuedCount = projectQueueItems.filter(q => q.status === 'pending' || q.status === 'paused_daily_limit').length;
    const runningCount = projectQueueItems.filter(q => q.status === 'running').length;

    useEffect(() => {
        if (!expanded || episodes.length > 0) return;

        const fetchEpisodes = async () => {
            setLoadingEpisodes(true);
            try {
                const res = await api.getEpisodes(item.name);
                setEpisodes(res.data);
            } catch (err) {
                console.error("Failed to load episodes", err);
                toast.error("Failed to load episodes list");
            } finally {
                setLoadingEpisodes(false);
            }
        };

        fetchEpisodes();
    }, [expanded, item.name, episodes.length, toast]);

    const handleTranslateEpisode = async (episodeName) => {
        try {
            await api.enqueueEpisodes(item.name, [episodeName]);
            toast.success(`Enqueued ${episodeName} for translation`);
            if (refreshQueue) refreshQueue();
        } catch (err) {
            console.error("Failed to enqueue episode", err);
            toast.error("Failed to enqueue episode translation");
        }
    };

    const handleEnqueueMissing = async () => {
        try {
            const res = await api.enqueueMissing(item.name);
            toast.success(`Enqueued ${res.data.enqueued_count} missing subtitles`);
            if (refreshQueue) refreshQueue();
        } catch (err) {
            console.error("Failed to enqueue missing show subtitles", err);
            toast.error("Failed to enqueue missing subtitles");
        }
    };

    const getEpisodeStatus = (ep) => {
        const qItem = projectQueueItems.find(q => q.episode_name === ep.name);
        if (qItem) {
            if (qItem.status === 'running') return 'running';
            if (qItem.status === 'failed') return 'failed';
            return 'queued'; // pending / paused_daily_limit
        }
        if (ep.translated || ep.metadata?.arr_has_target) {
            return 'translated';
        }
        return 'pending';
    };

    return (
        <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            className={`bg-white dark:bg-gray-800 rounded-xl border transition-all overflow-hidden ${
                isDisabled
                    ? 'border-gray-200 dark:border-gray-700 opacity-60'
                    : 'border-gray-200 dark:border-gray-700 hover:border-indigo-300 dark:hover:border-indigo-600 hover:shadow-md'
            }`}
        >
            <div className="p-4 flex items-center justify-between gap-4">
                <div className="flex items-center gap-3 min-w-0 flex-1">
                    {toggleProject ? (
                        <input
                            type="checkbox"
                            checked={isProjectSelected}
                            onChange={() => toggleProject(item.name)}
                            className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500 cursor-pointer h-4 w-4 shrink-0"
                            title={`Select "${item.show_name || item.name}" for bulk actions`}
                        />
                    ) : (isMovie && toggleEpisode && (
                        <input
                            type="checkbox"
                            checked={selectedEpisodes.has(`${item.name}::${item.name}`)}
                            onChange={() => toggleEpisode(item.name, item.name)}
                            className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500 cursor-pointer h-4 w-4 shrink-0"
                            title="Select movie for batch actions"
                        />
                    ))}
                    <div className={`w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0 ${
                        isMovie
                            ? 'bg-purple-100 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400'
                            : 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400'
                    }`}>
                        {isMovie ? <Film size={20} /> : <Tv size={20} />}
                    </div>
                    <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                            <h3 className="font-semibold text-gray-900 dark:text-white truncate">
                                {item.show_name}
                            </h3>
                            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider ${
                                isMovie ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400' : 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                            }`}>
                                {isMovie ? 'Movie' : 'Series'}
                            </span>
                            <StatusBadge
                                episodeCount={item.episode_count}
                                translatedCount={item.translated_count}
                                disabled={isDisabled}
                            />
                            <PriorityBadgeSelector
                                priority={currentPriority}
                                onChange={(prio) => onPriorityChange && onPriorityChange(item.name, prio)}
                            />
                            {(queuedCount > 0 || runningCount > 0) && (
                                <span className="flex items-center gap-1.5 text-xs font-semibold px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300">
                                    {runningCount > 0 && <span className="w-1.5 h-1.5 bg-amber-500 rounded-full animate-pulse" />}
                                    {queuedCount > 0 && `${queuedCount} queued`}
                                    {queuedCount > 0 && runningCount > 0 && " / "}
                                    {runningCount > 0 && `${runningCount} running`}
                                </span>
                            )}
                        </div>
                        <div className="flex items-center gap-3 mt-1 text-xs text-gray-500 dark:text-gray-400">
                            <span>→ {item.target_language}</span>
                            {!isMovie && <span>{item.episode_count} episode{item.episode_count !== 1 ? 's' : ''} imported</span>}
                            {item.arr_last_sync && (
                                <span>Synced {new Date(item.arr_last_sync).toLocaleDateString()}</span>
                            )}
                        </div>
                    </div>
                </div>

                <div className="flex items-center gap-2 flex-shrink-0">
                    {!isDisabled && item.episode_count > 0 && (
                        <button
                            onClick={handleEnqueueMissing}
                            className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-semibold text-purple-600 hover:bg-purple-50 dark:text-purple-400 dark:hover:bg-purple-900/20 rounded-lg transition-colors"
                            title="Enqueue all missing subtitles to background queue"
                        >
                            Translate Missing
                        </button>
                    )}

                    <Link
                        to={`/project/${encodeURIComponent(item.name)}`}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-indigo-600 hover:bg-indigo-50 dark:text-indigo-400 dark:hover:bg-indigo-900/20 rounded-lg transition-colors"
                    >
                        <ExternalLink size={14} /> View
                    </Link>

                    {isDisabled ? (
                        <button
                            onClick={() => onEnable(item.name)}
                            className="px-3 py-1.5 text-sm font-medium text-green-600 hover:bg-green-50 dark:text-green-400 dark:hover:bg-green-900/20 rounded-lg transition-colors"
                        >
                            Enable
                        </button>
                    ) : (
                        <button
                            onClick={() => onDisable(item.name)}
                            className="px-3 py-1.5 text-sm font-medium text-gray-500 hover:bg-gray-100 dark:text-gray-450 dark:hover:bg-gray-700 rounded-lg transition-colors"
                        >
                            Disable
                        </button>
                    )}

                    {!isMovie && item.episode_count > 0 && (
                        <button
                            onClick={() => setExpanded(!expanded)}
                            className="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded-lg transition-colors"
                        >
                            {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                        </button>
                    )}
                </div>
            </div>

            {/* Progress bar */}
            {item.episode_count > 0 && !isDisabled && (
                <div className="px-4 pb-3">
                    <div className="w-full h-1.5 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
                        <div
                            className="h-full rounded-full transition-all duration-500 bg-gradient-to-r from-indigo-500 to-purple-500"
                            style={{ width: `${Math.round((item.translated_count / Math.max(item.episode_count, 1)) * 100)}%` }}
                        />
                    </div>
                </div>
            )}

            {/* Expanded episode list */}
            <AnimatePresence>
                {expanded && (
                    <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        className="px-4 pb-4 border-t border-gray-100 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/40"
                    >
                        <div className="pt-3 space-y-2">
                            <div className="flex items-center justify-between mb-2">
                                <h4 className="text-xs font-bold text-gray-400 uppercase tracking-wider">Episodes List</h4>
                                {episodes.length > 0 && toggleShowEpisodes && (
                                    <label className="flex items-center gap-1.5 text-xs text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 font-medium cursor-pointer select-none">
                                        <input
                                            type="checkbox"
                                            checked={episodes.every(ep => selectedEpisodes.has(`${item.name}::${ep.name}`))}
                                            onChange={() => toggleShowEpisodes(item.name, episodes)}
                                            className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500 cursor-pointer h-3.5 w-3.5"
                                        />
                                        <span>Select All ({episodes.length})</span>
                                    </label>
                                )}
                            </div>
                            {loadingEpisodes ? (
                                <div className="text-xs text-gray-500 flex items-center gap-2 py-2">
                                    <RefreshCw size={12} className="animate-spin" /> Loading episodes...
                                </div>
                            ) : episodes.length === 0 ? (
                                <div className="text-xs text-gray-400 italic py-2">No episodes imported.</div>
                            ) : (
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                                    {episodes.map(ep => {
                                        const status = getEpisodeStatus(ep);
                                        return (
                                            <EpisodeRow
                                                key={ep.name}
                                                ep={ep}
                                                status={status}
                                                isSelected={selectedEpisodes.has(`${item.name}::${ep.name}`)}
                                                onToggleSelect={toggleEpisode ? () => toggleEpisode(item.name, ep.name) : undefined}
                                                onTranslate={handleTranslateEpisode}
                                                onInspectEmbedded={(epName) => setEmbeddedModalEpisode(epName)}
                                            />
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            <EmbeddedTracksModal
                isOpen={!!embeddedModalEpisode}
                onClose={() => setEmbeddedModalEpisode(null)}
                projectName={item.name}
                episodeName={embeddedModalEpisode}
                onExtractionComplete={() => {
                    api.getEpisodes(item.name).then(res => setEpisodes(res.data)).catch(() => {});
                }}
            />
        </motion.div>
    );
};

const MediaLibrary = () => {
    const [library, setLibrary] = useState(null);
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);
    const [syncJobId, setSyncJobId] = useState(null);
    const [activeTab, setActiveTab] = useState(TABS.SHOWS);
    const [searchQuery, setSearchQuery] = useState('');
    const [filterStatus, setFilterStatus] = useState('all');
    const [filterPriority, setFilterPriority] = useState('all');
    const [sortBy, setSortBy] = useState('default');
    const [arrConfigured, setArrConfigured] = useState(true); // assume yes until checked, to avoid a flash
    const [selectedProjects, setSelectedProjects] = useState(new Set());
    const [selectedEpisodes, setSelectedEpisodes] = useState(new Set());
    const [batchProcessing, setBatchProcessing] = useState(false);

    useEffect(() => {
        api.getArrStatus()
            .then(res => setArrConfigured(!!(res.data?.sonarr?.configured || res.data?.radarr?.configured)))
            .catch(() => {});
    }, []);

    // Background Queue State
    const [queueData, setQueueData] = useState({ items: [], summary: {}, rate_limits: {}, paused: false });
    const [syncModalOpen, setSyncModalOpen] = useState(false);
    
    const { activeJobs, addJob } = useJobs();
    const toast = useToast();

    const loadLibrary = useCallback(async () => {
        try {
            const res = await api.getArrLibrary();
            setLibrary(res.data);
        } catch (err) {
            console.error('Failed to load library', err);
            toast.error('Failed to load media library');
        } finally {
            setLoading(false);
        }
    }, [toast]);

    const fetchQueue = useCallback(async () => {
        try {
            const res = await api.getQueue();
            setQueueData(res.data);
        } catch (e) {
            console.error("Failed to fetch queue:", e);
        }
    }, []);

    useEffect(() => {
        loadLibrary();
    }, [loadLibrary]);

    useEffect(() => {
        fetchQueue();
        const interval = setInterval(() => { if (!document.hidden) fetchQueue(); }, 5000);
        return () => clearInterval(interval);
    }, [fetchQueue]);

    // Auto-refresh library metrics when queue items complete
    const prevItemsRef = useRef([]);
    useEffect(() => {
        const prevItems = prevItemsRef.current;
        const currentItems = queueData.items || [];
        
        const completedOrCancelled = currentItems.filter(item => 
            !['pending', 'running', 'paused_daily_limit'].includes(item.status)
        );
        const prevCompletedOrCancelled = prevItems.filter(item => 
            !['pending', 'running', 'paused_daily_limit'].includes(item.status)
        );
        
        if (completedOrCancelled.length > prevCompletedOrCancelled.length) {
            loadLibrary();
        }
        
        prevItemsRef.current = currentItems;
    }, [queueData.items, loadLibrary]);

    // Poll sync job
    useEffect(() => {
        if (!syncJobId) return;
        const job = activeJobs[syncJobId];
        if (job?.status === 'completed') {
            setSyncing(false);
            setSyncJobId(null);
            loadLibrary();
            toast.success(job.message || 'Sync complete!');
        } else if (job?.status === 'failed') {
            setSyncing(false);
            setSyncJobId(null);
            toast.error(job.message || 'Sync failed');
        }
    }, [activeJobs, syncJobId, loadLibrary, toast]);

    const handleOpenSyncModal = () => {
        setSyncModalOpen(true);
    };

    const handleStartSyncWithOptions = async (options) => {
        setSyncing(true);
        try {
            let res;
            if (options.source === 'sonarr') {
                res = await api.syncSonarr(options);
            } else if (options.source === 'radarr') {
                res = await api.syncRadarr(options);
            } else {
                res = await api.syncArrAll(options);
            }
            if (res.data?.job_id) {
                setSyncJobId(res.data.job_id);
                const label = options.source === 'sonarr' ? 'Sonarr' : options.source === 'radarr' ? 'Radarr' : 'Sonarr & Radarr';
                addJob(res.data.job_id, 'arr_sync', `Syncing ${label} library...`, { link: '/library' });
            }
        } catch (err) {
            setSyncing(false);
            toast.error('Failed to start sync');
        }
    };

    const handlePauseResume = async () => {
        try {
            if (queueData.paused) {
                await api.resumeQueue();
                toast.success('Translation worker resumed');
            } else {
                await api.pauseQueue();
                toast.info('Translation worker paused');
            }
            fetchQueue();
        } catch (e) {
            console.error("Failed to toggle queue worker pause:", e);
            toast.error('Failed to toggle worker state');
        }
    };

    const handleEnqueueAllMissing = async () => {
        try {
            const res = await api.enqueueAllMissing();
            toast.success(`Enqueued ${res.data.enqueued_count} missing subtitles across all shows`);
            fetchQueue();
        } catch (e) {
            console.error("Failed to enqueue all missing subtitles:", e);
            toast.error('Failed to enqueue missing subtitles');
        }
    };

    const handlePriorityChange = async (projectName, newPriority) => {
        try {
            await apiClient.put(`/projects/${encodeURIComponent(projectName)}/priority`, { priority: newPriority });
            toast.success(`Set priority of "${projectName}" to ${newPriority}`);
            await loadLibrary();
            fetchQueue();
        } catch (err) {
            console.error("Failed to update project priority:", err);
            toast.error("Failed to update priority");
        }
    };

    const handleBulkPriority = async (priority) => {
        if (selectedProjects.size === 0 || batchProcessing) return;
        setBatchProcessing(true);
        try {
            const projectNames = Array.from(selectedProjects);
            await apiClient.post('/projects/bulk-priority', {
                projects: projectNames,
                priority: priority,
            });
            toast.success(`Updated ${projectNames.length} project(s) to ${priority} priority`);
            setSelectedProjects(new Set());
            await loadLibrary();
            fetchQueue();
        } catch (err) {
            console.error("Bulk priority update failed:", err);
            toast.error("Failed to update bulk priority");
        } finally {
            setBatchProcessing(false);
        }
    };

    const handleDisable = async (projectName) => {
        try {
            await handlePriorityChange(projectName, 'ignored');
        } catch (err) {
            toast.error('Failed to disable entry');
        }
    };

    const handleEnable = async (projectName) => {
        try {
            await handlePriorityChange(projectName, 'normal');
        } catch (err) {
            toast.error('Failed to enable entry');
        }
    };

    const toggleProject = useCallback((projectName) => {
        setSelectedProjects(prev => {
            const next = new Set(prev);
            if (next.has(projectName)) next.delete(projectName);
            else next.add(projectName);
            return next;
        });
    }, []);

    const toggleEpisode = useCallback((projectName, episodeName) => {
        const key = `${projectName}::${episodeName}`;
        setSelectedEpisodes(prev => {
            const next = new Set(prev);
            if (next.has(key)) next.delete(key);
            else next.add(key);
            return next;
        });
    }, []);

    const toggleShowEpisodes = useCallback((projectName, epList) => {
        if (!epList || epList.length === 0) return;
        const keys = epList.map(ep => `${projectName}::${ep.name}`);
        setSelectedEpisodes(prev => {
            const next = new Set(prev);
            const allSelected = keys.every(k => next.has(k));
            if (allSelected) {
                keys.forEach(k => next.delete(k));
            } else {
                keys.forEach(k => next.add(k));
            }
            return next;
        });
    }, []);

    const handleBatchTranslateSelected = async () => {
        if (selectedEpisodes.size === 0 || batchProcessing) return;
        setBatchProcessing(true);
        try {
            const grouped = {};
            for (const key of selectedEpisodes) {
                const [proj, ep] = key.split('::');
                if (!grouped[proj]) grouped[proj] = [];
                grouped[proj].push(ep);
            }
            let total = 0;
            const remaining = new Set(selectedEpisodes);
            for (const [proj, eps] of Object.entries(grouped)) {
                try {
                    await api.enqueueEpisodes(proj, eps);
                    total += eps.length;
                    eps.forEach(ep => remaining.delete(`${proj}::${ep}`));
                    setSelectedEpisodes(new Set(remaining));
                } catch (projErr) {
                    console.error(`Failed to enqueue episodes for ${proj}:`, projErr);
                    toast.error(`Failed to enqueue episodes for ${proj}`);
                }
            }
            if (total > 0) {
                toast.success(`Enqueued ${total} episode(s) across ${Object.keys(grouped).length} show(s)`);
            }
            fetchQueue();
        } catch (err) {
            console.error("Batch translation enqueue error:", err);
            toast.error("Failed to enqueue selected episodes");
        } finally {
            setBatchProcessing(false);
        }
    };

    const handleBatchExtractSelected = async () => {
        if (selectedEpisodes.size === 0 || batchProcessing) return;
        setBatchProcessing(true);
        try {
            const grouped = {};
            for (const key of selectedEpisodes) {
                const [proj, ep] = key.split('::');
                if (!grouped[proj]) grouped[proj] = [];
                grouped[proj].push(ep);
            }
            let total = 0;
            const remaining = new Set(selectedEpisodes);
            for (const [proj, eps] of Object.entries(grouped)) {
                try {
                    const res = await api.batchExtractEmbedded(proj, { episode_names: eps });
                    if (res.data?.job_id) {
                        addJob(res.data.job_id, 'batch_extract_embedded', `Extracting embedded subs for ${proj}...`, { link: `/project/${encodeURIComponent(proj)}` });
                    }
                    total += eps.length;
                    eps.forEach(ep => remaining.delete(`${proj}::${ep}`));
                    setSelectedEpisodes(new Set(remaining));
                } catch (projErr) {
                    console.error(`Failed to extract subtitles for ${proj}:`, projErr);
                    toast.error(`Failed to extract subtitles for ${proj}`);
                }
            }
            if (total > 0) {
                toast.success(`Started embedded subtitle extraction for ${total} episode(s)`);
            }
            loadLibrary();
        } catch (err) {
            console.error("Batch extraction error:", err);
            toast.error("Failed to start embedded subtitle extraction");
        } finally {
            setBatchProcessing(false);
        }
    };

    const filterItems = (items) => {
        let filtered = items || [];
        if (searchQuery) {
            const q = searchQuery.toLowerCase();
            filtered = filtered.filter(i => (i.show_name || i.name || '').toLowerCase().includes(q));
        }
        if (filterStatus === 'translated') filtered = filtered.filter(i => i.translated_count > 0 && i.translated_count >= i.episode_count);
        else if (filterStatus === 'pending') filtered = filtered.filter(i => i.episode_count > 0 && i.translated_count < i.episode_count);
        else if (filterStatus === 'no_subs') filtered = filtered.filter(i => i.episode_count === 0);

        if (filterPriority !== 'all') {
            filtered = filtered.filter(i => {
                const p = i.translation_priority || (i.arr_disabled ? 'ignored' : 'normal');
                return p === filterPriority;
            });
        }

        if (sortBy === 'priority') {
            const prioRank = { high: 0, normal: 1, low: 2, ignored: 3 };
            filtered = [...filtered].sort((a, b) => {
                const pa = prioRank[a.translation_priority || (a.arr_disabled ? 'ignored' : 'normal')] ?? 1;
                const pb = prioRank[b.translation_priority || (b.arr_disabled ? 'ignored' : 'normal')] ?? 1;
                if (pa !== pb) return pa - pb;
                return (a.show_name || a.name || '').localeCompare(b.show_name || b.name || '');
            });
        }

        return filtered;
    };

    const syncJob = syncJobId ? activeJobs[syncJobId] : null;

    if (loading) {
        return (
            <div className="min-h-screen flex items-center justify-center">
                <div className="text-center">
                    <RefreshCw className="w-8 h-8 text-indigo-500 animate-spin mx-auto mb-3" />
                    <p className="text-gray-500 dark:text-gray-400">Loading library...</p>
                </div>
            </div>
        );
    }

    const tabs = [
        { id: TABS.SHOWS, label: 'Shows', icon: Tv, count: library?.totals?.series || 0 },
        { id: TABS.MOVIES, label: 'Movies', icon: Film, count: library?.totals?.movies || 0 },
        { id: TABS.DISABLED, label: 'Disabled', icon: EyeOff, count: library?.totals?.disabled || 0 },
    ];

    const currentItems = activeTab === TABS.SHOWS ? library?.series
        : activeTab === TABS.MOVIES ? library?.movies
        : library?.disabled;

    const filteredItems = filterItems(currentItems || []);

    const allVisibleSelected = filteredItems.length > 0 && filteredItems.every(i => selectedProjects.has(i.name));
    const toggleSelectAllVisible = () => {
        setSelectedProjects(prev => {
            const next = new Set(prev);
            if (allVisibleSelected) {
                filteredItems.forEach(i => next.delete(i.name));
            } else {
                filteredItems.forEach(i => next.add(i.name));
            }
            return next;
        });
    };

    return (
        <div className="min-h-screen pb-20">
            {/* Header */}
            <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
                    <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                        <div className="flex items-center gap-3">
                            <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-xl flex items-center justify-center shadow-lg shadow-blue-200 dark:shadow-none">
                                <Library className="text-white" size={22} />
                            </div>
                            <div>
                                <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Media Library</h1>
                                <p className="text-sm text-gray-500 dark:text-gray-400">
                                    {library?.last_sync
                                        ? `Last synced: ${new Date(library.last_sync).toLocaleString()}`
                                        : 'Not synced yet'
                                    }
                                    {library && ` • ${library.totals.series} shows • ${library.totals.movies} movies`}
                                </p>
                            </div>
                        </div>

                        <div className="flex flex-wrap items-center gap-2">
                            <button
                                onClick={handlePauseResume}
                                className={`flex items-center gap-2 px-4 py-2.5 text-sm font-semibold rounded-xl border transition-all ${
                                    queueData.paused
                                        ? 'bg-amber-500/10 border-amber-500/30 text-amber-600 dark:text-amber-400 hover:bg-amber-500/20'
                                        : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/20'
                                }`}
                            >
                                {queueData.paused ? <Play size={14} /> : <Pause size={14} />}
                                {queueData.paused ? 'Resume Worker' : 'Pause Worker'}
                            </button>

                            <button
                                onClick={handleEnqueueAllMissing}
                                className="flex items-center gap-2 px-4 py-2.5 bg-purple-600 hover:bg-purple-700 text-white rounded-xl text-sm font-semibold transition-all shadow-md shadow-purple-200 dark:shadow-none hover:-translate-y-0.5 active:translate-y-0"
                            >
                                <Languages size={14} /> Translate All Missing
                            </button>

                            <button
                                onClick={handleOpenSyncModal}
                                disabled={syncing}
                                className="flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 disabled:opacity-50 text-white rounded-xl text-sm font-semibold transition-all shadow-md shadow-blue-200 dark:shadow-none hover:-translate-y-0.5 active:translate-y-0"
                            >
                                <RefreshCw size={14} className={syncing ? 'animate-spin' : ''} />
                                {syncing ? 'Syncing...' : 'Sync Now'}
                            </button>
                        </div>
                    </div>

                    {/* Sync progress */}
                    {syncJob && syncing && (
                        <div className="mt-4 bg-blue-50/50 dark:bg-blue-900/10 rounded-xl p-3 border border-blue-100 dark:border-blue-900/30">
                            <div className="flex items-center gap-3">
                                <RefreshCw size={14} className="text-blue-500 animate-spin flex-shrink-0" />
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium text-blue-800 dark:text-blue-300 truncate">
                                        {syncJob.message || 'Syncing...'}
                                    </p>
                                    <div className="mt-1.5 w-full h-1.5 bg-blue-100 dark:bg-blue-900/30 rounded-full overflow-hidden">
                                        <div
                                            className="h-full bg-blue-500 rounded-full transition-all duration-300"
                                            style={{ width: `${syncJob.progress || 0}%` }}
                                        />
                                    </div>
                                </div>
                                <span className="text-xs font-mono text-blue-600 dark:text-blue-400 flex-shrink-0">
                                    {Math.round(syncJob.progress || 0)}%
                                </span>
                            </div>
                        </div>
                    )}
                </div>
            </header>

            {/* Tabs + Filters */}
            <div className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex items-center justify-between">
                        <nav className="flex gap-1" aria-label="Library tabs">
                            {tabs.map(tab => {
                                const Icon = tab.icon;
                                const isActive = activeTab === tab.id;
                                return (
                                    <button
                                        key={tab.id}
                                        onClick={() => setActiveTab(tab.id)}
                                        className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                                            isActive
                                                ? 'border-indigo-600 text-indigo-600 dark:text-indigo-400'
                                                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300 dark:text-gray-400'
                                        }`}
                                    >
                                        <Icon size={16} />
                                        {tab.label}
                                        <span className={`text-xs px-1.5 py-0.5 rounded-full ${
                                            isActive ? 'bg-indigo-100 text-indigo-600 dark:bg-indigo-900/50' : 'bg-gray-100 text-gray-500 dark:bg-gray-700'
                                        }`}>
                                            {tab.count}
                                        </span>
                                    </button>
                                );
                            })}
                        </nav>

                        <div className="flex items-center gap-2 flex-wrap py-2">
                            <div className="relative">
                                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                                <input
                                    type="text"
                                    placeholder="Search..."
                                    value={searchQuery}
                                    onChange={(e) => setSearchQuery(e.target.value)}
                                    className="pl-8 pr-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none w-44 transition-all"
                                />
                            </div>
                            <select
                                value={filterStatus}
                                onChange={(e) => setFilterStatus(e.target.value)}
                                className="px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                            >
                                <option value="all">All Status</option>
                                <option value="translated">Translated</option>
                                <option value="pending">Pending</option>
                                <option value="no_subs">No Subs</option>
                            </select>
                            <select
                                value={filterPriority}
                                onChange={(e) => setFilterPriority(e.target.value)}
                                className="px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                title="Filter by Priority"
                            >
                                <option value="all">All Priorities</option>
                                <option value="high">High Priority</option>
                                <option value="normal">Normal</option>
                                <option value="low">Low Priority</option>
                                <option value="ignored">Ignored</option>
                            </select>
                            <select
                                value={sortBy}
                                onChange={(e) => setSortBy(e.target.value)}
                                className="px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                title="Sort order"
                            >
                                <option value="default">Default Order</option>
                                <option value="priority">Priority (High → Low)</option>
                            </select>
                            {filteredItems.length > 0 && (
                                <button
                                    type="button"
                                    onClick={toggleSelectAllVisible}
                                    className="px-2.5 py-1.5 text-xs font-semibold text-gray-600 dark:text-gray-300 hover:text-indigo-600 dark:hover:text-indigo-400 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-650 rounded-lg flex items-center gap-1.5 transition-colors"
                                    title={allVisibleSelected ? "Deselect all visible projects" : "Select all visible projects"}
                                >
                                    <CheckSquare size={13} />
                                    <span>{allVisibleSelected ? 'Deselect All' : `Select (${filteredItems.length})`}</span>
                                </button>
                            )}
                        </div>
                    </div>
                </div>
            </div>

            {/* Content */}
            <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
                <AnimatePresence mode="wait">
                    {filteredItems.length === 0 ? (
                        <motion.div
                            key="empty"
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            className="text-center py-16"
                        >
                            <div className="w-16 h-16 bg-gray-100 dark:bg-gray-800 rounded-2xl flex items-center justify-center mx-auto mb-4">
                                {activeTab === TABS.DISABLED
                                    ? <EyeOff size={28} className="text-gray-400" />
                                    : activeTab === TABS.MOVIES
                                        ? <Film size={28} className="text-gray-400" />
                                        : <Tv size={28} className="text-gray-400" />
                                }
                            </div>
                            <p className="text-lg font-medium text-gray-500 dark:text-gray-400">
                                {searchQuery
                                    ? 'No results match your search'
                                    : activeTab === TABS.DISABLED
                                        ? 'No disabled entries'
                                        : !arrConfigured
                                            ? 'Connect Sonarr or Radarr to import your library automatically'
                                            : library?.last_sync
                                                ? 'No items found — try syncing'
                                                : 'Click "Sync Now" to import from Sonarr/Radarr'
                                }
                            </p>
                            {!library?.last_sync && !searchQuery && (
                                arrConfigured ? (
                                    <button
                                        onClick={handleOpenSyncModal}
                                        disabled={syncing}
                                        className="mt-4 px-6 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-sm font-medium transition-all"
                                    >
                                        Sync with Sonarr & Radarr
                                    </button>
                                ) : (
                                    <Link
                                        to="/settings"
                                        className="mt-4 inline-block px-6 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-sm font-medium transition-all"
                                    >
                                        Connect Sonarr/Radarr
                                    </Link>
                                )
                            )}
                        </motion.div>
                    ) : (
                        <motion.div
                            key={activeTab}
                            initial={{ opacity: 0, y: 8 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -8 }}
                            transition={{ duration: 0.15 }}
                            className="space-y-2"
                        >
                            {filteredItems.map(item => (
                                <LibraryCard
                                    key={item.name}
                                    item={item}
                                    onDisable={handleDisable}
                                    onEnable={handleEnable}
                                    onPriorityChange={handlePriorityChange}
                                    isProjectSelected={selectedProjects.has(item.name)}
                                    toggleProject={toggleProject}
                                    queueData={queueData}
                                    refreshQueue={fetchQueue}
                                    selectedEpisodes={selectedEpisodes}
                                    toggleEpisode={toggleEpisode}
                                    toggleShowEpisodes={toggleShowEpisodes}
                                />
                            ))}
                        </motion.div>
                    )}
                </AnimatePresence>

                {/* Sync result warnings */}
                {syncJob?.status === 'completed' && syncJob.result?.unreachable_paths?.length > 0 && (
                    <div className="mt-6 bg-amber-50 dark:bg-amber-900/10 rounded-xl p-4 border border-amber-200 dark:border-amber-900/30">
                        <div className="flex gap-3">
                            <AlertCircle className="w-5 h-5 text-amber-500 flex-shrink-0 mt-0.5" />
                            <div>
                                <h4 className="font-semibold text-amber-800 dark:text-amber-300 text-sm">Unreachable Paths</h4>
                                <p className="text-xs text-amber-700 dark:text-amber-400 mt-1">
                                    Some subtitle paths could not be accessed. Check your path mappings in Settings.
                                </p>
                                <ul className="mt-2 text-xs text-amber-600 dark:text-amber-400 space-y-0.5">
                                    {syncJob.result.unreachable_paths.map((p, i) => (
                                        <li key={i} className="font-mono">
                                            <Link to={`/settings?test_path=${encodeURIComponent(p)}`} className="underline hover:text-amber-700 dark:hover:text-amber-300">
                                                {p}
                                            </Link>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        </div>
                    </div>
                )}
            </main>

            {/* Floating Batch Action Bar */}
            <AnimatePresence>
                {selectedProjects.size > 0 && (
                    <motion.div
                        initial={{ y: 60, opacity: 0 }}
                        animate={{ y: 0, opacity: 1 }}
                        exit={{ y: 60, opacity: 0 }}
                        className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 bg-gray-900/95 dark:bg-gray-800/95 backdrop-blur-md text-white px-5 py-3 rounded-2xl shadow-2xl border border-gray-700/60 flex items-center gap-3 text-sm flex-wrap justify-center"
                    >
                        <div className="flex items-center gap-2 pr-3 border-r border-gray-700">
                            <span className="w-2 h-2 rounded-full bg-indigo-500 animate-ping" />
                            <span className="font-semibold text-xs text-gray-200">
                                {selectedProjects.size} project{selectedProjects.size !== 1 ? 's' : ''} selected
                            </span>
                        </div>

                        <div className="flex items-center gap-1.5 flex-wrap">
                            <button
                                type="button"
                                onClick={() => handleBulkPriority('high')}
                                disabled={batchProcessing}
                                className="px-3 py-1.5 bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 shadow-sm transition-all"
                            >
                                <Flame size={13} /> Set High
                            </button>
                            <button
                                type="button"
                                onClick={() => handleBulkPriority('normal')}
                                disabled={batchProcessing}
                                className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 shadow-sm transition-all"
                            >
                                <Clock size={13} /> Set Normal
                            </button>
                            <button
                                type="button"
                                onClick={() => handleBulkPriority('low')}
                                disabled={batchProcessing}
                                className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 shadow-sm transition-all"
                            >
                                <ArrowDown size={13} /> Set Low
                            </button>
                            <button
                                type="button"
                                onClick={() => handleBulkPriority('ignored')}
                                disabled={batchProcessing}
                                className="px-3 py-1.5 bg-rose-700 hover:bg-rose-600 disabled:opacity-50 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 shadow-sm transition-all"
                            >
                                <EyeOff size={13} /> Set Ignored
                            </button>
                            <button
                                type="button"
                                onClick={() => setSelectedProjects(new Set())}
                                className="px-2.5 py-1.5 text-xs text-gray-400 hover:text-gray-200 hover:bg-gray-800 rounded-lg transition-colors ml-1"
                            >
                                Clear
                            </button>
                        </div>
                    </motion.div>
                )}

                {selectedEpisodes.size > 0 && selectedProjects.size === 0 && (
                    <motion.div
                        initial={{ y: 60, opacity: 0 }}
                        animate={{ y: 0, opacity: 1 }}
                        exit={{ y: 60, opacity: 0 }}
                        className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 bg-gray-900/95 dark:bg-gray-800/95 backdrop-blur-md text-white px-5 py-3 rounded-2xl shadow-2xl border border-gray-700/60 flex items-center gap-4 text-sm"
                    >
                        <div className="flex items-center gap-2 pr-3 border-r border-gray-700">
                            <span className="w-2 h-2 rounded-full bg-indigo-500 animate-ping" />
                            <span className="font-semibold text-xs text-gray-200">
                                {selectedEpisodes.size} item{selectedEpisodes.size !== 1 ? 's' : ''} selected
                            </span>
                        </div>

                        <div className="flex items-center gap-2">
                            <button
                                type="button"
                                onClick={handleBatchTranslateSelected}
                                disabled={batchProcessing}
                                className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 shadow-sm transition-all"
                            >
                                <Languages size={13} /> Translate ({selectedEpisodes.size})
                            </button>

                            <button
                                type="button"
                                onClick={handleBatchExtractSelected}
                                disabled={batchProcessing}
                                className="px-3 py-1.5 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white rounded-xl text-xs font-semibold flex items-center gap-1.5 transition-colors shadow-sm"
                            >
                                <Film size={13} /> Extract Subs ({selectedEpisodes.size})
                            </button>

                            <button
                                type="button"
                                onClick={() => setSelectedEpisodes(new Set())}
                                className="px-2.5 py-1.5 text-xs text-gray-400 hover:text-gray-200 hover:bg-gray-800 rounded-lg transition-colors"
                            >
                                Clear
                            </button>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            <LibrarySyncModal
                isOpen={syncModalOpen}
                onClose={() => setSyncModalOpen(false)}
                onConfirm={handleStartSyncWithOptions}
            />
        </div>
    );
};

export default MediaLibrary;
