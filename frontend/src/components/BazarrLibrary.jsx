import React, { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useJobs } from '../context/JobContext';
import { useToast } from '../context/ToastContext';
import {
    RefreshCw, Tv, Film, EyeOff, ChevronDown, ChevronRight,
    Languages, Check, AlertCircle, Clock, Ban, ExternalLink,
    Library, Filter, Search
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const TABS = { SHOWS: 'shows', MOVIES: 'movies', DISABLED: 'disabled' };

const StatusBadge = ({ episodeCount, translatedCount, disabled }) => {
    if (disabled) return <span className="flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400"><Ban size={10} /> Disabled</span>;
    if (episodeCount === 0) return <span className="flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400"><Clock size={10} /> No subs</span>;
    if (translatedCount >= episodeCount) return <span className="flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-green-50 text-green-600 dark:bg-green-900/30 dark:text-green-400"><Check size={10} /> Complete</span>;
    if (translatedCount > 0) return <span className="flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400"><Languages size={10} /> {translatedCount}/{episodeCount}</span>;
    return <span className="flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400"><Clock size={10} /> Pending</span>;
};

const LibraryCard = ({ item, onDisable, onEnable }) => {
    const [expanded, setExpanded] = useState(false);
    const isDisabled = item.bazarr_disabled;
    const isMovie = item.bazarr_media_type === 'movie';

    return (
        <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            className={`bg-white dark:bg-gray-800 rounded-xl border transition-all ${
                isDisabled
                    ? 'border-gray-200 dark:border-gray-700 opacity-60'
                    : 'border-gray-200 dark:border-gray-700 hover:border-indigo-300 dark:hover:border-indigo-600 hover:shadow-md'
            }`}
        >
            <div className="p-4 flex items-center justify-between gap-4">
                <div className="flex items-center gap-3 min-w-0 flex-1">
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
                        </div>
                        <div className="flex items-center gap-3 mt-1 text-xs text-gray-500 dark:text-gray-400">
                            <span>→ {item.target_language}</span>
                            {!isMovie && <span>{item.episode_count} episode{item.episode_count !== 1 ? 's' : ''} imported</span>}
                            {item.bazarr_last_sync && (
                                <span>Synced {new Date(item.bazarr_last_sync).toLocaleDateString()}</span>
                            )}
                        </div>
                    </div>
                </div>

                <div className="flex items-center gap-2 flex-shrink-0">
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
                            className="px-3 py-1.5 text-sm font-medium text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700 rounded-lg transition-colors"
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
        </motion.div>
    );
};

const BazarrLibrary = () => {
    const [library, setLibrary] = useState(null);
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);
    const [syncJobId, setSyncJobId] = useState(null);
    const [activeTab, setActiveTab] = useState(TABS.SHOWS);
    const [searchQuery, setSearchQuery] = useState('');
    const [filterStatus, setFilterStatus] = useState('all');
    const { activeJobs, addJob } = useJobs();
    const toast = useToast();

    const loadLibrary = useCallback(async () => {
        try {
            const res = await api.getBazarrLibrary();
            setLibrary(res.data);
        } catch (err) {
            console.error('Failed to load library', err);
            toast.error('Failed to load Bazarr library');
        } finally {
            setLoading(false);
        }
    }, [toast]);

    useEffect(() => { loadLibrary(); }, [loadLibrary]);

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

    const handleSync = async () => {
        setSyncing(true);
        try {
            const res = await api.syncBazarr();
            if (res.data.job_id) {
                setSyncJobId(res.data.job_id);
                addJob(res.data.job_id, 'bazarr_sync', 'Syncing Bazarr library...', {});
            }
        } catch (err) {
            setSyncing(false);
            toast.error('Failed to start sync');
        }
    };

    const handleDisable = async (projectName) => {
        try {
            await api.disableLibraryEntry(projectName);
            toast.info(`Disabled: ${projectName}`);
            loadLibrary();
        } catch (err) {
            toast.error('Failed to disable entry');
        }
    };

    const handleEnable = async (projectName) => {
        try {
            await api.enableLibraryEntry(projectName);
            toast.success(`Enabled: ${projectName}`);
            loadLibrary();
        } catch (err) {
            toast.error('Failed to enable entry');
        }
    };

    const filterItems = (items) => {
        let filtered = items;
        if (searchQuery) {
            const q = searchQuery.toLowerCase();
            filtered = filtered.filter(i => i.show_name?.toLowerCase().includes(q));
        }
        if (filterStatus === 'translated') filtered = filtered.filter(i => i.translated_count > 0 && i.translated_count >= i.episode_count);
        else if (filterStatus === 'pending') filtered = filtered.filter(i => i.episode_count > 0 && i.translated_count < i.episode_count);
        else if (filterStatus === 'no_subs') filtered = filtered.filter(i => i.episode_count === 0);
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

    return (
        <div className="min-h-screen pb-20">
            {/* Header */}
            <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-xl flex items-center justify-center shadow-lg shadow-blue-200 dark:shadow-none">
                                <Library className="text-white" size={22} />
                            </div>
                            <div>
                                <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Bazarr Library</h1>
                                <p className="text-sm text-gray-500 dark:text-gray-400">
                                    {library?.last_sync
                                        ? `Last synced: ${new Date(library.last_sync).toLocaleString()}`
                                        : 'Not synced yet'
                                    }
                                    {library && ` • ${library.totals.series} shows • ${library.totals.movies} movies`}
                                </p>
                            </div>
                        </div>

                        <button
                            onClick={handleSync}
                            disabled={syncing}
                            className="flex items-center gap-2 px-5 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 disabled:opacity-50 text-white rounded-xl text-sm font-bold transition-all shadow-lg shadow-blue-200 dark:shadow-none hover:-translate-y-0.5 active:translate-y-0"
                        >
                            <RefreshCw size={16} className={syncing ? 'animate-spin' : ''} />
                            {syncing ? 'Syncing...' : 'Sync Now'}
                        </button>
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

                        <div className="flex items-center gap-2">
                            <div className="relative">
                                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                                <input
                                    type="text"
                                    placeholder="Search..."
                                    value={searchQuery}
                                    onChange={(e) => setSearchQuery(e.target.value)}
                                    className="pl-8 pr-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none w-48 transition-all"
                                />
                            </div>
                            <select
                                value={filterStatus}
                                onChange={(e) => setFilterStatus(e.target.value)}
                                className="px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                            >
                                <option value="all">All</option>
                                <option value="translated">Translated</option>
                                <option value="pending">Pending</option>
                                <option value="no_subs">No Subs</option>
                            </select>
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
                                        : library?.last_sync
                                            ? 'No items found — try syncing'
                                            : 'Click "Sync Now" to import from Bazarr'
                                }
                            </p>
                            {!library?.last_sync && !searchQuery && (
                                <button
                                    onClick={handleSync}
                                    disabled={syncing}
                                    className="mt-4 px-6 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-sm font-medium transition-all"
                                >
                                    Sync with Bazarr
                                </button>
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
                                        <li key={i} className="font-mono">{p}</li>
                                    ))}
                                </ul>
                            </div>
                        </div>
                    </div>
                )}
            </main>
        </div>
    );
};

export default BazarrLibrary;
