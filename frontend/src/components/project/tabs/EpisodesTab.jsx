import React, { useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
    Film, Search, Play, Download, Trash2, CheckCircle2,
    Clock, RefreshCw, Wrench, ShieldAlert, FolderDown,
    ChevronDown, ChevronRight, Layers, LayoutGrid, List,
    FileText, ExternalLink, AlertCircle, Sparkles, AlertTriangle, Activity, Bot
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { useJobs } from '../../../context/JobContext';
import { formatEta } from '../../../utils/formatters';
import { api } from '../../../api';

const EpisodesTab = ({
    project,
    episodes = [],
    subprojects = [],
    selectedEpisodes,
    toggleEpisode,
    toggleMultiple,
    selectAll,
    clearSelection,
    invertSelection,
    onTranslateEpisode,
    onBatchTranslate,
    onAgentTranslate,
    onBatchAgentTranslate,
    preparingAgent,
    onApplyFixes,
    onCheckMissing,
    onScriptFix,
    onEmbeddedTracks,
    onDeleteEpisode,
    onDeleteTranslation,
    onBatchDownload,
    onBatchExportToPath,
    onBatchDelete,
    onBatchExtractEmbedded,
    onUpload,
    projectName,
}) => {
    const { activeJobs } = useJobs();

    const [searchQuery, setSearchQuery] = useState('');
    const [statusFilter, setStatusFilter] = useState('all'); // all | running | translated | untranslated | nosub
    const [viewMode, setViewMode] = useState('grid'); // grid | table
    const [collapsedSeasons, setCollapsedSeasons] = useState({});

    const handleDeleteTranslationClick = async (epName) => {
        if (!window.confirm(`Delete translation for episode "${epName}"?`)) return;
        const deleteDiskFile = window.confirm(`Also delete the detected .el.srt / .el.ass file from disk?\n\nClick OK to delete disk sidecars, or Cancel to keep them on disk.`);
        if (onDeleteTranslation) {
            await onDeleteTranslation(epName, deleteDiskFile);
        } else if (onDeleteEpisode) {
            await onDeleteEpisode(epName, deleteDiskFile);
        } else {
            try {
                await api.deleteEpisodeTranslation(projectName, epName, deleteDiskFile);
            } catch (err) {
                console.error('Failed to delete translation', err);
            }
        }
    };

    const handleBatchDeleteClick = async () => {
        if (!onBatchDelete) return;
        const deleteDiskFile = window.confirm(`Also delete detected .el.srt / .el.ass files from disk for selected episodes?\n\nClick OK to delete disk sidecars, or Cancel to keep them on disk.`);
        await onBatchDelete(deleteDiskFile);
    };

    const isEpHasSource = (ep) => {
        if (ep.has_source === true) return true;
        if (ep.has_source === false && ep.line_count === 0 && !ep.metadata?.arr_sub_path && !ep.has_ass) return false;
        return (ep.line_count > 0 || !!ep.metadata?.arr_sub_path || !!ep.has_ass || ep.has_source !== false);
    };

    // Normalizer for episode names (strips extensions like .en.srt, .ass, trims, lowercases)
    const normalizeEpName = (name) => {
        if (!name) return '';
        return name.trim().toLowerCase().replace(/\.(srt|ass|ssa|vtt)$/i, '');
    };

    // Build fast lookup map keyed by exact and normalized episode names
    const activeJobsByEpisode = useMemo(() => {
        const map = new Map();
        if (!activeJobs) return map;

        const currentProj = (projectName || project?.name || '').trim().toLowerCase();

        for (const job of Object.values(activeJobs)) {
            if (!job || ['completed', 'failed', 'cancelled'].includes(job.status)) continue;

            const jProj = (job.project_name || job.metadata?.projectId || job.metadata?.projectName || '').trim().toLowerCase();

            // Scope job to current project
            if (!currentProj || !jProj || jProj === currentProj) {
                const jEp = job.episode_name || job.metadata?.episodeName;
                if (jEp) {
                    map.set(jEp, job);
                    map.set(normalizeEpName(jEp), job);
                }
            }
        }
        return map;
    }, [activeJobs, projectName, project?.name]);

    const getActiveJob = (epName) => {
        return activeJobsByEpisode.get(epName) || activeJobsByEpisode.get(normalizeEpName(epName));
    };

    const renderQualityBadge = (ep, pName) => {
        const activeJob = getActiveJob(ep.name);
        if (activeJob) {
            if (activeJob.status === 'running') {
                return (
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-bold flex items-center gap-1.5 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800/50 animate-pulse">
                        <RefreshCw size={10} className="animate-spin text-indigo-600 dark:text-indigo-400" />
                        <span>Translating</span>
                    </span>
                );
            }
            if (activeJob.status === 'pending') {
                return (
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-bold flex items-center gap-1.5 bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-800/50">
                        <Clock size={10} className="animate-pulse" />
                        <span>Queued</span>
                    </span>
                );
            }
        }

        const hasSource = isEpHasSource(ep);
        if (!hasSource) {
            return (
                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold flex items-center gap-1 bg-amber-100 dark:bg-amber-950/60 text-amber-800 dark:text-amber-300 border border-amber-200 dark:border-amber-800/40">
                    <AlertCircle size={10} /> No Subtitle
                </span>
            );
        }
        if (!ep.translated) {
            return (
                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold flex items-center gap-1 bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300">
                    <Clock size={10} /> Pending
                </span>
            );
        }

        const needsReview = ep.needs_review_count ?? ep.metadata?.needs_review_count ?? 0;
        const autoRepaired = ep.auto_repaired_count ?? ep.metadata?.auto_repaired_count ?? 0;
        // Set by the translation audit / agent submissions: lines still in the source language.
        const gaps = ep.metadata?.translation_gaps;
        const gapCount = gaps ? (gaps.dialogue || 0) + (gaps.signs || 0) : 0;

        if (gapCount > 0) {
            return (
                <span
                    className="px-2 py-0.5 rounded-full text-[10px] font-bold flex items-center gap-1 bg-orange-100 dark:bg-orange-950 text-orange-800 dark:text-orange-300 border border-orange-300 dark:border-orange-700/60"
                    title={`${gaps.dialogue || 0} dialogue line(s) and ${gaps.signs || 0} on-screen text line(s) are still untranslated`}
                >
                    <AlertTriangle size={10} className="text-orange-600 dark:text-orange-400" />
                    Incomplete ({gapCount} line{gapCount > 1 ? 's' : ''})
                </span>
            );
        }

        // Set by the translation audit: lines whose translation belongs to another line.
        const misplacedCount = gaps?.misplaced || 0;
        if (misplacedCount > 0) {
            return (
                <span
                    className="px-2 py-0.5 rounded-full text-[10px] font-bold flex items-center gap-1 bg-rose-100 dark:bg-rose-950 text-rose-800 dark:text-rose-300 border border-rose-300 dark:border-rose-700/60"
                    title={`${misplacedCount} line(s) carry the translation of another line (found by the translation check)`}
                >
                    <AlertTriangle size={10} className="text-rose-600 dark:text-rose-400" />
                    Misaligned ({misplacedCount} line{misplacedCount > 1 ? 's' : ''})
                </span>
            );
        }

        // Set by the translation audit: garbled encoding, leftover prompt text or mixed alphabets in the translation.
        const brokenCount = Number(gaps?.broken) || 0;
        if (brokenCount > 0) {
            return (
                <span
                    className="px-2 py-0.5 rounded-full text-[10px] font-bold flex items-center gap-1 bg-rose-100 dark:bg-rose-950 text-rose-800 dark:text-rose-300 border border-rose-300 dark:border-rose-700/60"
                    title={`${brokenCount} line(s) contain garbled text, leftover prompt text or mixed alphabets (found by the translation check). Open Sync Disk & Server to repair or queue them.`}
                >
                    <AlertTriangle size={10} className="text-rose-600 dark:text-rose-400" />
                    Garbled ({brokenCount})
                </span>
            );
        }

        if (needsReview > 0) {
            const targetProject = pName || projectName || project?.name;
            return (
                <Link
                    to={`/review?project=${encodeURIComponent(targetProject)}&episode=${encodeURIComponent(ep.name)}`}
                    onClick={(e) => e.stopPropagation()}
                    className="px-2 py-0.5 rounded-full text-[10px] font-bold flex items-center gap-1 bg-amber-100 dark:bg-amber-950 text-amber-800 dark:text-amber-300 border border-amber-300 dark:border-amber-700/60 hover:bg-amber-200 dark:hover:bg-amber-900/60 transition-colors shadow-sm"
                    title={`Click to review ${needsReview} flagged line(s) in Review Queue`}
                >
                    <AlertTriangle size={10} className="text-amber-600 dark:text-amber-400" />
                    Needs Review ({needsReview} line{needsReview > 1 ? 's' : ''})
                </Link>
            );
        }

        if (autoRepaired > 0) {
            return (
                <span
                    className="px-2 py-0.5 rounded-full text-[10px] font-bold flex items-center gap-1 bg-purple-100 dark:bg-purple-950 text-purple-800 dark:text-purple-300 border border-purple-200 dark:border-purple-800/40"
                    title={`${autoRepaired} cue(s) automatically recovered by QC repair`}
                >
                    <Sparkles size={10} className="text-purple-600 dark:text-purple-400" />
                    Auto-Repaired ({autoRepaired})
                </span>
            );
        }

        return (
            <span
                className="px-2 py-0.5 rounded-full text-[10px] font-bold flex items-center gap-1 bg-emerald-100 dark:bg-emerald-950 text-emerald-800 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800/40"
                title="All cues translated and verified with 0 warnings"
            >
                <CheckCircle2 size={10} className="text-emerald-600 dark:text-emerald-400" />
                Clean
            </span>
        );
    };

    // Filter computation
    const filteredEpisodes = useMemo(() => {
        return episodes.filter(ep => {
            const matchesSearch = !searchQuery || ep.name.toLowerCase().includes(searchQuery.toLowerCase());
            let matchesStatus = true;
            const hasSource = isEpHasSource(ep);

            if (statusFilter === 'translated') matchesStatus = !!ep.translated;
            else if (statusFilter === 'untranslated') matchesStatus = !ep.translated && hasSource;
            else if (statusFilter === 'nosub') matchesStatus = !hasSource;
            else if (statusFilter === 'running') {
                const activeJob = getActiveJob(ep.name);
                matchesStatus = !!activeJob && ['running', 'in_progress', 'pending'].includes(activeJob.status);
            }
            return matchesSearch && matchesStatus;
        });
    }, [episodes, searchQuery, statusFilter, activeJobsByEpisode]);


    // Grouping by season (e.g. S01E01 -> Season 1)
    const groupedBySeason = useMemo(() => {
        const groups = {};
        filteredEpisodes.forEach(ep => {
            const match = ep.name.match(/S(\d+)E(\d+)/i) || ep.name.match(/Season\s*(\d+)/i);
            const seasonNum = match ? parseInt(match[1], 10) : 0;
            const seasonKey = seasonNum > 0 ? `Season ${seasonNum}` : 'General / Standalone';
            if (!groups[seasonKey]) groups[seasonKey] = [];
            groups[seasonKey].push(ep);
        });
        return groups;
    }, [filteredEpisodes]);

    const toggleSeasonCollapse = (seasonKey) => {
        setCollapsedSeasons(prev => ({ ...prev, [seasonKey]: !prev[seasonKey] }));
    };

    const isParent = project?.type === 'parent';

    return (
        <div className="space-y-6">
            {/* If Parent Universe Project, show subprojects list first */}
            {isParent && (
                <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-4">
                    <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-700 pb-3">
                        <div className="flex items-center gap-2">
                            <Layers className="text-indigo-600 dark:text-indigo-400" size={20} />
                            <h3 className="text-base font-bold text-gray-900 dark:text-white">Universe Shows ({subprojects.length})</h3>
                        </div>
                    </div>

                    {subprojects.length === 0 ? (
                        <p className="text-sm text-gray-400 italic py-4 text-center">No linked shows in this universe yet.</p>
                    ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                            {subprojects.map(sp => (
                                <Link
                                    key={sp.name}
                                    to={`/project/${encodeURIComponent(sp.name)}`}
                                    className="p-4 rounded-xl border border-gray-100 dark:border-gray-700 hover:border-indigo-400 bg-gray-50/50 dark:bg-gray-750/50 flex items-center justify-between transition-all group"
                                >
                                    <div>
                                        <div className="font-bold text-sm text-gray-900 dark:text-white group-hover:text-indigo-600 dark:group-hover:text-indigo-400">
                                            {sp.name}
                                        </div>
                                        <div className="text-xs text-gray-500 mt-0.5">
                                            {sp.target_language || 'Default language'} &middot; {sp.episodes_count || 0} eps
                                        </div>
                                    </div>
                                    <ExternalLink size={16} className="text-gray-400 group-hover:text-indigo-500" />
                                </Link>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {/* Episode Controls Toolbar */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-4 border border-gray-200 dark:border-gray-700 shadow-sm flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3 flex-1 min-w-[280px]">
                    <div className="relative flex-1 max-w-sm">
                        <Search className="w-4 h-4 text-gray-400 absolute left-3 top-2.5" />
                        <input
                            type="text"
                            placeholder="Filter episodes by name..."
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            className="w-full pl-9 pr-3 py-1.5 text-xs rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 focus:ring-1 focus:ring-indigo-500 outline-none text-gray-800 dark:text-gray-200"
                        />
                    </div>

                    {/* Status Pill Filters */}
                    <div className="flex bg-gray-100 dark:bg-gray-700 p-1 rounded-xl gap-0.5 text-xs font-semibold">
                        {[
                            { id: 'all', label: 'All', count: episodes.length },
                            { id: 'running', label: 'In Progress', count: episodes.filter(e => !!getActiveJob(e.name)).length },
                            { id: 'translated', label: 'Translated', count: episodes.filter(e => e.translated).length },
                            { id: 'untranslated', label: 'Pending', count: episodes.filter(e => !e.translated && isEpHasSource(e)).length },
                            { id: 'nosub', label: 'No Subtitle', count: episodes.filter(e => !isEpHasSource(e)).length },
                        ].map(({ id, label, count }) => (
                            <button
                                key={id}
                                type="button"
                                onClick={() => setStatusFilter(id)}
                                className={`px-2.5 py-1 rounded-lg transition-all flex items-center gap-1.5 ${
                                    statusFilter === id
                                        ? 'bg-white dark:bg-gray-800 text-indigo-600 dark:text-indigo-400 shadow-sm'
                                        : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
                                }`}
                            >
                                <span>{label}</span>
                                <span className="text-[10px] px-1 bg-black/10 dark:bg-white/10 rounded-full">{count}</span>
                            </button>
                        ))}
                    </div>
                </div>

                <div className="flex items-center gap-2">
                    {/* View mode toggle */}
                    <div className="flex bg-gray-100 dark:bg-gray-700 p-1 rounded-xl gap-0.5">
                        <button
                            type="button"
                            onClick={() => setViewMode('grid')}
                            className={`p-1.5 rounded-lg transition-colors ${viewMode === 'grid' ? 'bg-white dark:bg-gray-800 text-indigo-600' : 'text-gray-400'}`}
                            title="Grid Card View"
                        >
                            <LayoutGrid size={16} />
                        </button>
                        <button
                            type="button"
                            onClick={() => setViewMode('table')}
                            className={`p-1.5 rounded-lg transition-colors ${viewMode === 'table' ? 'bg-white dark:bg-gray-800 text-indigo-600' : 'text-gray-400'}`}
                            title="Table List View"
                        >
                            <List size={16} />
                        </button>
                    </div>

                    {/* Batch Extract Embedded Subtitles */}
                    {onBatchExtractEmbedded && (
                        <button
                            type="button"
                            onClick={() => onBatchExtractEmbedded(selectedEpisodes.size > 0 ? Array.from(selectedEpisodes) : null)}
                            className="px-3.5 py-2 bg-amber-500/10 dark:bg-amber-500/20 text-amber-700 dark:text-amber-300 hover:bg-amber-500/25 rounded-xl text-xs font-bold flex items-center gap-1.5 border border-amber-500/30 transition-all cursor-pointer"
                            title={selectedEpisodes.size > 0 ? `Batch extract embedded tracks for ${selectedEpisodes.size} selected episode(s)` : "Batch extract embedded ASS/SRT tracks from all video files"}
                        >
                            <Layers size={14} /> {selectedEpisodes.size > 0 ? `Extract Embedded (${selectedEpisodes.size})` : 'Extract All Embedded'}
                        </button>
                    )}

                    {/* Upload button */}
                    <label className="px-3.5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 shadow-sm transition-all cursor-pointer">
                        <input type="file" multiple onChange={onUpload} accept=".srt,.ass,.ssa,.vtt" className="hidden" />
                        <FolderDown size={14} /> Upload Subtitles
                    </label>
                </div>
            </div>


            {/* Episodes List / Grouped by Season */}
            <div className="space-y-6">
                {filteredEpisodes.length === 0 ? (
                    <div className="bg-white dark:bg-gray-800 rounded-2xl p-12 text-center border border-gray-200 dark:border-gray-700 text-gray-400 space-y-2">
                        <Film size={40} className="mx-auto opacity-40" />
                        <p className="font-semibold text-gray-500">No episodes found matching criteria.</p>
                    </div>
                ) : (
                    Object.entries(groupedBySeason).map(([seasonKey, eps]) => {
                        const isCollapsed = collapsedSeasons[seasonKey];
                        const seasonSelected = eps.every(e => selectedEpisodes.has(e.name));

                        return (
                            <div key={seasonKey} className="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 overflow-hidden shadow-sm">
                                {/* Season Accordion Header */}
                                <div className="p-4 bg-gray-50/70 dark:bg-gray-850/80 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
                                    <div className="flex items-center gap-3">
                                        <button
                                            type="button"
                                            onClick={() => toggleSeasonCollapse(seasonKey)}
                                            className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded-lg"
                                        >
                                            {isCollapsed ? <ChevronRight size={18} /> : <ChevronDown size={18} />}
                                        </button>
                                        <input
                                            type="checkbox"
                                            checked={seasonSelected}
                                            onChange={() => {
                                                if (toggleMultiple) {
                                                    toggleMultiple(eps.map(e => e.name));
                                                }
                                            }}
                                            className="w-4 h-4 text-indigo-600 rounded border-gray-300"
                                        />
                                        <h4 className="font-bold text-sm text-gray-900 dark:text-white flex items-center gap-2">
                                            <span>{seasonKey}</span>
                                            <span className="text-xs font-normal text-gray-400">({eps.length} items)</span>
                                        </h4>
                                    </div>

                                    <div className="text-xs text-gray-400">
                                        {eps.filter(e => e.translated).length} / {eps.length} translated
                                    </div>
                                </div>

                                {/* Season Episodes Content */}
                                {!isCollapsed && (
                                    <div className="p-4">
                                        {viewMode === 'grid' ? (
                                             <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                                                {eps.map(ep => {
                                                    const isSelected = selectedEpisodes.has(ep.name);
                                                    const hasSource = isEpHasSource(ep);
                                                    return (
                                                        <div
                                                            key={ep.name}
                                                            className={`p-4 rounded-xl border transition-all flex flex-col justify-between gap-3 ${
                                                                isSelected
                                                                    ? 'border-indigo-500 bg-indigo-50/40 dark:bg-indigo-950/20 shadow-sm'
                                                                    : 'border-gray-200 dark:border-gray-700 bg-gray-50/40 dark:bg-gray-900/30 hover:border-gray-300'
                                                            }`}
                                                        >
                                                            <div className="flex items-start justify-between gap-2">
                                                                <div className="flex items-start gap-2.5">
                                                                    <input
                                                                        type="checkbox"
                                                                        checked={isSelected}
                                                                        onChange={(e) => toggleEpisode(ep.name, e.nativeEvent.shiftKey, episodes)}
                                                                        className="mt-1 w-4 h-4 text-indigo-600 rounded border-gray-300"
                                                                    />
                                                                    <div>
                                                                        <div className="font-bold text-sm text-gray-900 dark:text-white line-clamp-1">
                                                                            {ep.name}
                                                                        </div>
                                                                        <div className="flex items-center gap-2 mt-1 flex-wrap">
                                                                            {renderQualityBadge(ep, projectName)}
                                                                            {ep.has_ass && (
                                                                                <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300 uppercase">
                                                                                    ASS
                                                                                </span>
                                                                            )}
                                                                        </div>
                                                                    </div>
                                                                </div>

                                                                <div className="flex items-center gap-1">
                                                                    {(() => {
                                                                        const activeJob = getActiveJob(ep.name);
                                                                        if (activeJob && activeJob.status === 'running') {
                                                                            return (
                                                                                <div className="p-1.5 bg-indigo-50 dark:bg-indigo-950/60 text-indigo-500 rounded-lg cursor-not-allowed" title="Translation actively running">
                                                                                    <RefreshCw size={14} className="animate-spin" />
                                                                                </div>
                                                                            );
                                                                        }
                                                                        if (hasSource) {
                                                                            return (
                                                                                <div className="flex items-center gap-1">
                                                                                    {onAgentTranslate && (
                                                                                        <button
                                                                                            type="button"
                                                                                            onClick={() => onAgentTranslate(ep.name)}
                                                                                            disabled={preparingAgent}
                                                                                            className="p-1.5 bg-purple-50 hover:bg-purple-100 dark:bg-purple-950/60 dark:hover:bg-purple-900 text-purple-600 dark:text-purple-400 rounded-lg transition-colors border border-purple-200/50 dark:border-purple-800/50"
                                                                                            title="Agent Translate Episode (AI Agent Context)"
                                                                                        >
                                                                                            <Bot size={14} />
                                                                                        </button>
                                                                                    )}
                                                                                    <button
                                                                                        type="button"
                                                                                        onClick={() => onTranslateEpisode(ep.name)}
                                                                                        className="p-1.5 bg-indigo-50 hover:bg-indigo-100 dark:bg-indigo-950 text-indigo-600 dark:text-indigo-400 rounded-lg transition-colors"
                                                                                        title="Translate Episode"
                                                                                    >
                                                                                        <Play size={14} />
                                                                                    </button>
                                                                                </div>
                                                                            );
                                                                        }
                                                                        return (
                                                                            <button
                                                                                type="button"
                                                                                onClick={() => onEmbeddedTracks(ep.name)}
                                                                                className="px-2.5 py-1 bg-purple-100 hover:bg-purple-200 dark:bg-purple-900/60 dark:hover:bg-purple-850 text-purple-700 dark:text-purple-200 rounded-lg transition-all flex items-center gap-1.5 text-xs font-bold shadow-sm"
                                                                                title="Extract Subtitle Track from Video File"
                                                                            >
                                                                                <Film size={13} /> Extract
                                                                            </button>
                                                                        );
                                                                    })()}
                                                                </div>
                                                            </div>

                                                            {/* Inline Progress Bar for Active Translation */}
                                                            {(() => {
                                                                const activeJob = getActiveJob(ep.name);
                                                                if (!activeJob || activeJob.status !== 'running') return null;
                                                                return (
                                                                    <div className="mt-2.5 p-2.5 rounded-xl bg-indigo-50/70 dark:bg-indigo-950/40 border border-indigo-100 dark:border-indigo-900/40 space-y-1.5">
                                                                        <div className="flex items-center justify-between text-[11px] font-semibold">
                                                                            <span className="text-indigo-900 dark:text-indigo-200 flex items-center gap-1">
                                                                                <Activity size={12} className="text-indigo-500 animate-pulse" />
                                                                                {activeJob.scenes_total > 0
                                                                                    ? `Scene ${activeJob.scenes_done || 0}/${activeJob.scenes_total}`
                                                                                    : 'Translating...'}
                                                                            </span>
                                                                            <span className="text-indigo-600 dark:text-indigo-400 font-bold tabular-nums">
                                                                                {Math.round(activeJob.progress || 0)}%
                                                                            </span>
                                                                        </div>
                                                                        <div className="w-full h-1.5 bg-indigo-200/60 dark:bg-indigo-900/60 rounded-full overflow-hidden relative">
                                                                            <div
                                                                                className="h-full bg-gradient-to-r from-indigo-500 to-purple-600 rounded-full transition-all duration-500 relative"
                                                                                style={{ width: `${Math.max(3, Math.min(100, activeJob.progress || 0))}%` }}
                                                                            >
                                                                                <div className="absolute inset-0 bg-white/30 animate-pulse" />
                                                                            </div>
                                                                        </div>
                                                                        <div className="flex items-center justify-between text-[10px] text-gray-500 dark:text-gray-400 font-medium">
                                                                            <span>
                                                                                {activeJob.cues_total > 0
                                                                                    ? `Cues: ${activeJob.cues_translated || 0}/${activeJob.cues_total}`
                                                                                    : activeJob.cues_translated > 0
                                                                                        ? `${activeJob.cues_translated} cues`
                                                                                        : 'Processing...'}
                                                                            </span>
                                                                            {activeJob.eta_seconds != null && (
                                                                                <span className="text-indigo-600 dark:text-indigo-400 font-semibold flex items-center gap-0.5">
                                                                                    <Clock size={10} />
                                                                                    ETA: {formatEta(activeJob.eta_seconds)}
                                                                                </span>
                                                                            )}
                                                                        </div>
                                                                    </div>
                                                                );
                                                            })()}

                                                            {/* Secondary Action Buttons */}
                                                            <div className="flex items-center justify-between pt-2 border-t border-gray-200/50 dark:border-gray-700/50 text-xs">
                                                                <div className="flex items-center gap-1">
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => onApplyFixes(ep.name)}
                                                                        disabled={!hasSource}
                                                                        className={`p-1 rounded ${hasSource ? 'text-gray-400 hover:text-indigo-600' : 'text-gray-300 dark:text-gray-600 cursor-not-allowed'}`}
                                                                        title="Apply SubtitleEdit Fixes"
                                                                    >
                                                                        <Wrench size={13} />
                                                                    </button>
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => onCheckMissing(ep.name)}
                                                                        disabled={!hasSource}
                                                                        className={`p-1 rounded ${hasSource ? 'text-gray-400 hover:text-amber-500' : 'text-gray-300 dark:text-gray-600 cursor-not-allowed'}`}
                                                                        title="Check Missing Lines"
                                                                    >
                                                                        <Search size={13} />
                                                                    </button>
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => onScriptFix(ep.name)}
                                                                        disabled={!hasSource}
                                                                        className={`p-1 rounded ${hasSource ? 'text-gray-400 hover:text-purple-500' : 'text-gray-300 dark:text-gray-600 cursor-not-allowed'}`}
                                                                        title="ScriptGuard Homoglyph Check"
                                                                    >
                                                                        <ShieldAlert size={13} />
                                                                    </button>
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => onEmbeddedTracks(ep.name)}
                                                                        className={`p-1 rounded transition-colors ${!hasSource ? 'text-purple-600 bg-purple-50 dark:bg-purple-950 font-bold' : 'text-gray-400 hover:text-purple-600'}`}
                                                                        title="Inspect / Extract Embedded Video Tracks"
                                                                    >
                                                                        <Film size={13} />
                                                                    </button>
                                                                </div>

                                                                <div className="flex items-center gap-1">
                                                                    {ep.translated && hasSource && (
                                                                        <Link
                                                                            to={`/project/${encodeURIComponent(projectName)}/episode/${encodeURIComponent(ep.name)}`}
                                                                            className="px-2 py-1 text-[11px] font-semibold text-indigo-600 dark:text-indigo-400 hover:underline flex items-center gap-1"
                                                                        >
                                                                            <FileText size={12} /> Lines
                                                                        </Link>
                                                                    )}
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => handleDeleteTranslationClick(ep.name)}
                                                                        className="p-1 text-gray-400 hover:text-rose-600 rounded"
                                                                        title="Delete Translation"
                                                                    >
                                                                        <Trash2 size={13} />
                                                                    </button>
                                                                </div>
                                                            </div>
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        ) : (
                                            <div className="overflow-x-auto">
                                                <table className="w-full text-left text-xs">
                                                    <thead>
                                                        <tr className="border-b border-gray-100 dark:border-gray-700 text-gray-400 font-bold uppercase">
                                                            <th className="p-2 w-8"></th>
                                                            <th className="p-2">Episode</th>
                                                            <th className="p-2">Status</th>
                                                            <th className="p-2 text-right">Actions</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                                                        {eps.map(ep => {
                                                            const hasSource = isEpHasSource(ep);
                                                            return (
                                                                <tr key={ep.name} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/50">
                                                                    <td className="p-2">
                                                                        <input
                                                                            type="checkbox"
                                                                            checked={selectedEpisodes.has(ep.name)}
                                                                            onChange={(e) => toggleEpisode(ep.name, e.nativeEvent.shiftKey, episodes)}
                                                                            className="w-4 h-4 text-indigo-600 rounded border-gray-300"
                                                                        />
                                                                    </td>
                                                                    <td className="p-2 font-semibold text-gray-900 dark:text-white">
                                                                        {ep.name}
                                                                    </td>
                                                                    <td className="p-2">
                                                                        {(() => {
                                                                            const activeJob = getActiveJob(ep.name);
                                                                            if (activeJob && activeJob.status === 'running') {
                                                                                return (
                                                                                    <div className="space-y-1 min-w-[150px] max-w-[200px]">
                                                                                        <div className="flex items-center justify-between text-[10px] font-bold">
                                                                                            <span className="text-indigo-600 dark:text-indigo-400 flex items-center gap-1">
                                                                                                <RefreshCw size={9} className="animate-spin" />
                                                                                                {activeJob.scenes_total > 0 ? `S${activeJob.scenes_done || 0}/${activeJob.scenes_total}` : 'Translating'}
                                                                                            </span>
                                                                                            <span className="tabular-nums text-gray-700 dark:text-gray-300">
                                                                                                {Math.round(activeJob.progress || 0)}%
                                                                                            </span>
                                                                                        </div>
                                                                                        <div className="w-full h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                                                                                            <div
                                                                                                className="h-full bg-gradient-to-r from-indigo-500 to-purple-600 rounded-full transition-all duration-500"
                                                                                                style={{ width: `${Math.max(3, Math.min(100, activeJob.progress || 0))}%` }}
                                                                                            />
                                                                                        </div>
                                                                                        <div className="flex items-center justify-between text-[9px] text-gray-400">
                                                                                            <span>
                                                                                                {activeJob.cues_total > 0 ? `${activeJob.cues_translated || 0}/${activeJob.cues_total} cues` : ''}
                                                                                            </span>
                                                                                            {activeJob.eta_seconds != null && (
                                                                                                <span className="text-indigo-500 font-semibold">ETA: {formatEta(activeJob.eta_seconds)}</span>
                                                                                            )}
                                                                                        </div>
                                                                                    </div>
                                                                                );
                                                                            }
                                                                            return renderQualityBadge(ep, projectName);
                                                                        })()}
                                                                    </td>
                                                                    <td className="p-2 text-right">
                                                                        <div className="flex items-center justify-end gap-1">
                                                                            {(() => {
                                                                                const activeJob = getActiveJob(ep.name);
                                                                                if (activeJob && activeJob.status === 'running') {
                                                                                    return (
                                                                                        <div className="p-1 text-indigo-500 cursor-not-allowed" title="Translation running">
                                                                                            <RefreshCw size={14} className="animate-spin" />
                                                                                        </div>
                                                                                    );
                                                                                }
                                                                                if (hasSource) {
                                                                                    return (
                                                                                        <>
                                                                                            {onAgentTranslate && (
                                                                                                <button
                                                                                                    type="button"
                                                                                                    onClick={() => onAgentTranslate(ep.name)}
                                                                                                    disabled={preparingAgent}
                                                                                                    className="p-1 text-purple-600 hover:bg-purple-50 dark:hover:bg-purple-950/50 rounded transition-colors"
                                                                                                    title="Agent Translate Episode"
                                                                                                >
                                                                                                    <Bot size={14} />
                                                                                                </button>
                                                                                            )}
                                                                                            <button onClick={() => onTranslateEpisode(ep.name)} className="p-1 text-indigo-600 hover:bg-indigo-50 rounded" title="Translate Episode">
                                                                                                <Play size={14} />
                                                                                            </button>
                                                                                        </>
                                                                                    );
                                                                                }
                                                                                return (
                                                                                    <button onClick={() => onEmbeddedTracks(ep.name)} className="p-1 text-purple-600 hover:bg-purple-50 rounded" title="Extract Subtitle from Video">
                                                                                        <Film size={14} />
                                                                                    </button>
                                                                                );
                                                                            })()}
                                                                            <button onClick={() => handleDeleteTranslationClick(ep.name)} className="p-1 text-gray-400 hover:text-rose-600 rounded" title="Delete Translation">
                                                                                <Trash2 size={14} />
                                                                            </button>
                                                                        </div>
                                                                    </td>
                                                                </tr>
                                                            );
                                                        })}
                                                    </tbody>

                                                </table>
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    })
                )}
            </div>

            {/* Bottom Floating Batch Action Bar */}
            <AnimatePresence>
                {selectedEpisodes.size > 0 && (
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: 20 }}
                        className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 bg-gray-900/95 text-white px-6 py-3.5 rounded-2xl shadow-2xl backdrop-blur-md border border-gray-700 flex items-center gap-4 flex-wrap max-w-2xl"
                    >
                        <div className="flex items-center gap-2">
                            <span className="font-bold text-xs bg-indigo-600 px-2.5 py-1 rounded-lg">
                                {selectedEpisodes.size} selected
                            </span>
                            <button
                                type="button"
                                onClick={clearSelection}
                                className="text-xs text-gray-400 hover:text-white underline"
                            >
                                Clear
                            </button>
                        </div>

                        <div className="flex items-center gap-2 flex-wrap">
                            <button
                                type="button"
                                onClick={onBatchTranslate}
                                className="px-3.5 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 shadow-sm transition-all"
                            >
                                <Play size={12} /> Translate
                            </button>

                            {onBatchAgentTranslate && (
                                <button
                                    type="button"
                                    onClick={() => onBatchAgentTranslate(Array.from(selectedEpisodes))}
                                    disabled={preparingAgent}
                                    className="px-3.5 py-1.5 bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 shadow-sm transition-all border border-purple-400/30 disabled:opacity-50"
                                    title="Translate selected episode(s) using AI Agent context"
                                >
                                    <Bot size={13} className={preparingAgent ? "animate-spin" : ""} />
                                    Agent Translate ({selectedEpisodes.size})
                                </button>
                            )}

                            {selectedEpisodes.size === 1 && onEmbeddedTracks && (
                                <button
                                    type="button"
                                    onClick={() => onEmbeddedTracks(Array.from(selectedEpisodes)[0])}
                                    className="px-3 py-1.5 bg-purple-600 hover:bg-purple-700 text-white rounded-xl text-xs font-semibold flex items-center gap-1.5 transition-colors shadow-sm"
                                    title="Inspect & extract embedded subtitle tracks for this selected episode"
                                >
                                    <Film size={12} /> Inspect / Extract Subs
                                </button>
                            )}

                            {onBatchExtractEmbedded && (
                                <button
                                    type="button"
                                    onClick={() => onBatchExtractEmbedded(Array.from(selectedEpisodes))}
                                    className="px-3 py-1.5 bg-amber-600/80 hover:bg-amber-600 text-white rounded-xl text-xs font-semibold flex items-center gap-1.5 transition-colors border border-amber-500/30"
                                    title="Extract embedded subtitle tracks from selected video files"
                                >
                                    <Layers size={12} /> Extract Subs ({selectedEpisodes.size})
                                </button>
                            )}

                            <button
                                type="button"
                                onClick={onBatchExportToPath}
                                className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-200 rounded-xl text-xs font-semibold flex items-center gap-1.5 transition-colors border border-gray-700"
                            >
                                <FolderDown size={12} /> Export to Disk
                            </button>


                            <button
                                type="button"
                                onClick={() => onApplyFixes(null, true)}
                                className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-200 rounded-xl text-xs font-semibold flex items-center gap-1.5 transition-colors border border-gray-700"
                            >
                                <Wrench size={12} /> Fixes
                            </button>

                            <button
                                type="button"
                                onClick={onBatchDownload}
                                className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-200 rounded-xl text-xs font-semibold flex items-center gap-1.5 transition-colors border border-gray-700"
                            >
                                <Download size={12} /> Zip
                            </button>

                            <button
                                type="button"
                                onClick={handleBatchDeleteClick}
                                className="p-1.5 text-rose-400 hover:bg-rose-950/50 rounded-lg transition-colors"
                                title="Delete Selected Episodes Translation"
                            >
                                <Trash2 size={16} />
                            </button>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
};

export default EpisodesTab;
