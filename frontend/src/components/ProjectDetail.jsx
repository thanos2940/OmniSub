import React, { useState, useRef, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
    ArrowLeft, Settings as SettingsIcon, RefreshCw, Sparkles,
    Film, Book, Users, FileText, ShieldCheck, History, BarChart3,
    AlertCircle, Loader2, Play, Compass, Wrench, Layers, Bot
} from 'lucide-react';
import { useToast, formatErrorMessage } from '../context/ToastContext';
import { useJobs } from '../context/JobContext';
import { useProjectDetail } from '../hooks/useProjectDetail';
import { useEpisodeSelection } from '../hooks/useEpisodeSelection';
import { api } from '../api';

// Modals
import ProjectSettingsModal from './ProjectSettingsModal';
import TranslationModal from './project/modals/TranslationModal';
import ApplyFixesModal from './project/modals/ApplyFixesModal';
import CheckMissingModal from './project/modals/CheckMissingModal';
import ScriptFixModal from './project/modals/ScriptFixModal';
import EmbeddedTracksModal from './project/modals/EmbeddedTracksModal';
import SyncOptionsModal from './project/modals/SyncOptionsModal';
import SimplePipelineWizard from './SimplePipelineWizard';
import AgentTaskModal from './modals/AgentTaskModal';

// Tabs
import EpisodesTab from './project/tabs/EpisodesTab';
import ContextTab from './project/tabs/ContextTab';
import GlossaryTab from './project/tabs/GlossaryTab';
import ProfilesTab from './project/tabs/ProfilesTab';
import SummariesTab from './project/tabs/SummariesTab';
import VisualGuardTab from './project/tabs/VisualGuardTab';
import HistoryTab from './project/tabs/HistoryTab';
import StatsTab from './project/tabs/StatsTab';

const TABS = [
    { id: 'episodes', label: 'Episodes', icon: Film },
    { id: 'context', label: 'Context Guide', icon: Compass },
    { id: 'glossary', label: 'Glossary', icon: Book },
    { id: 'profiles', label: 'Character Profiles', icon: Users },
    { id: 'summaries', label: 'Episode Summaries', icon: FileText },
    { id: 'visual_guard', label: 'VisualGuard & Formatting', icon: ShieldCheck },
    { id: 'history', label: 'Translation Memory & QC', icon: History },
    { id: 'stats', label: 'Token Stats', icon: BarChart3 },
];

const ProjectDetail = () => {
    const { projectName, name } = useParams();
    const effectiveProjectName = projectName || name;
    const toast = useToast();
    const { addJob, activeJobs } = useJobs();

    const [activeTab, setActiveTab] = useState('episodes');


    // Modal Visibility States
    const [settingsModalOpen, setSettingsModalOpen] = useState(false);
    const [syncModalOpen, setSyncModalOpen] = useState(false);
    const [pipelineWizardOpen, setPipelineWizardOpen] = useState(false);

    const [translationModal, setTranslationModal] = useState({ open: false, episodeName: null, isBatch: false });
    const [fixesModal, setFixesModal] = useState({ open: false, episodeName: null, isBatch: false });
    const [checkMissingModal, setCheckMissingModal] = useState({ open: false, episodeName: null });
    const [scriptFixModal, setScriptFixModal] = useState({ open: false, episodeName: null });
    const [embeddedTracksModal, setEmbeddedTracksModal] = useState({ open: false, episodeName: null });
    const [agentModalData, setAgentModalData] = useState(null);
    const [preparingAgent, setPreparingAgent] = useState(false);
    const [startingRunner, setStartingRunner] = useState(false);

    // Project Data Hook
    const {
        project,
        episodes,
        subprojects,
        tokenSummary,
        loading,
        error,
        loadData,
        handleSaveSettings,
        handleUpload,
        handleDeleteEpisode,
        handleBatchDownload,
        handleBatchExportToPath,
        handleAutoTranslate,
        handleBatchExtractEmbedded,
        handleBatchTranslate,
        handleTranslateEpisode,
        handleBatchDelete,
        handleAiAction,
    } = useProjectDetail({ projectName: effectiveProjectName, addJob, activeJobs, toast });


    // Selection Hook
    const {
        selectedEpisodes,
        toggleEpisode,
        toggleMultiple,
        selectAll,
        clearSelection,
        invertSelection,
    } = useEpisodeSelection(episodes);

    // In-flight guard for translation dispatch
    const translationInFlightRef = useRef(false);

    // Auto-refresh when agent submits translation or disk sync completes
    useEffect(() => {
        const handleRefresh = (e) => {
            const detail = e?.detail || {};
            if (!detail.project_name || detail.project_name.toLowerCase() === effectiveProjectName.toLowerCase()) {
                loadData(true);
            }
        };
        window.addEventListener('omnisub:agent-submitted', handleRefresh);
        window.addEventListener('omnisub:sync-completed', handleRefresh);
        return () => {
            window.removeEventListener('omnisub:agent-submitted', handleRefresh);
            window.removeEventListener('omnisub:sync-completed', handleRefresh);
        };
    }, [effectiveProjectName, loadData]);

    // Translation Launch Handler
    const handleConfirmTranslation = async ({ translationType, force, enhanceGlossary, model, episodeName, isBatch }) => {
        if (translationInFlightRef.current) {
            if (toast?.warning) toast.warning("Translation dispatch is already in progress.");
            return;
        }
        translationInFlightRef.current = true;

        try {
            // --- Flow A: Single Episode ---
            if (episodeName) {
                const res = await api.translateEpisode(
                    effectiveProjectName,
                    episodeName,
                    model || undefined,
                    enhanceGlossary,
                    force,
                    translationType
                );
                const jobId = res.data?.job_id;
                if (jobId && addJob) {
                    addJob(jobId, 'translation', `Translating ${effectiveProjectName} - ${episodeName}`, {
                        projectId: effectiveProjectName,
                        episodeName: episodeName,
                        link: `/project/${encodeURIComponent(effectiveProjectName)}`
                    });
                }
                if (toast?.success) toast.success(`Translation enqueued for ${episodeName}`);
                return;
            }

            // --- Flow B: Batch / Multi-Episode ---
            let epList = [];
            if (isBatch && selectedEpisodes.size > 0) {
                epList = Array.from(selectedEpisodes);
            } else if (isBatch) {
                // "Translate All Pending" -> include untranslated episodes with subtitles OR media files (auto-extracted)
                epList = (episodes || [])
                    .filter(e => !e.translated && (e.has_source !== false || e.has_media || !!e.metadata?.arr_media_path || (e.line_count > 0 || e.metadata?.arr_sub_path)))
                    .map(e => e.name);
            }

            epList = epList.filter(name => name && String(name).trim().length > 0);

            if (epList.length === 0) {
                if (toast?.error) toast.error("No pending episodes found for translation.");
                return;
            }

            // If the batch resolves to exactly 1 episode, route as direct single-episode translation
            if (epList.length === 1) {
                const singleEp = epList[0];
                const res = await api.translateEpisode(
                    effectiveProjectName,
                    singleEp,
                    model || undefined,
                    enhanceGlossary,
                    force,
                    translationType
                );
                const jobId = res.data?.job_id;
                if (jobId && addJob) {
                    addJob(jobId, 'translation', `Translating ${effectiveProjectName} - ${singleEp}`, {
                        projectId: effectiveProjectName,
                        episodeName: singleEp,
                        link: `/project/${encodeURIComponent(effectiveProjectName)}`
                    });
                }
                if (toast?.success) toast.success(`Translation enqueued for ${singleEp}`);
                return;
            }

            // Dispatch to batchTranslate endpoint
            const res = await api.batchTranslate(effectiveProjectName, {
                episode_names: epList,
                model: model || undefined,
                enhance_glossary: enhanceGlossary,
                force,
                translation_type: translationType,
            });

            const jobId = res.data?.job_id;
            const subJobIds = res.data?.sub_jobs || res.data?.item_ids || [];
            if (jobId && addJob) {
                addJob(jobId, 'batch_translate', `Batch Translating ${effectiveProjectName} (${epList.length} episodes)`, {
                    projectId: effectiveProjectName,
                    episodeNames: epList,
                    sub_job_ids: subJobIds,
                    link: `/project/${encodeURIComponent(effectiveProjectName)}`
                });
            }
            if (toast?.success) toast.success(`Batch translation enqueued for ${epList.length} episodes.`);
        } catch (err) {
            console.error("Translation dispatch failed", err);
            const errMsg = err.response?.data?.detail || "Failed to start translation.";
            if (toast?.error) toast.error(errMsg);
        } finally {
            translationInFlightRef.current = false;
        }
    };

    // Agent Translation Handlers
    const handleAgentTranslateEpisode = async (epName) => {
        setPreparingAgent(true);
        try {
            const res = await api.prepareTargetAgentTask(effectiveProjectName, epName);
            if (res.data && res.data.success) {
                setAgentModalData(res.data);
                if (toast?.success) {
                    const d = res.data;
                    toast.success(
                        d.status === 'nothing_to_translate' ? `${epName} is already fully translated (re-exported).`
                            : d.status === 'already_claimed' ? `${epName} is already claimed by an agent.`
                                : `${epName} claimed for an agent: ${d.units} line(s) to translate.`
                    );
                }
            } else {
                if (toast?.error) toast.error(res.data?.message || "Failed to prepare agent workspace");
            }
        } catch (err) {
            if (toast?.error) toast.error(err.response?.data?.message || err.response?.data?.detail || err.message || "Failed to prepare agent workspace");
        } finally {
            setPreparingAgent(false);
        }
    };

    const handleBatchAgentTranslate = async (targetEpisodes = null) => {
        const epList = targetEpisodes || (selectedEpisodes.size > 0 ? Array.from(selectedEpisodes) : null);
        if (epList && epList.length === 1) {
            return await handleAgentTranslateEpisode(epList[0]);
        }
        setPreparingAgent(true);
        try {
            const res = await api.enqueueForAgent(effectiveProjectName, epList, 1);
            if (res.data && res.data.success) {
                setAgentModalData(res.data);
                if (toast?.success) toast.success(`Enqueued ${res.data.enqueued_count} episode(s) for Agent Translation`);
            } else {
                if (toast?.error) toast.error(res.data?.message || "Failed to enqueue episodes for agent");
            }
        } catch (err) {
            if (toast?.error) toast.error(err.response?.data?.detail || err.message || "Failed to enqueue episodes for agent");
        } finally {
            setPreparingAgent(false);
        }
    };

    // Queue the episodes exactly like "Agent Translate" does, then let the direct agent runner translate
    // them (starting a runner that is already going just keeps it going).
    const handleTranslateWithRunner = async () => {
        const epList = selectedEpisodes.size > 0 ? Array.from(selectedEpisodes) : null;
        setStartingRunner(true);
        try {
            const res = await api.enqueueForAgent(effectiveProjectName, epList, 1);
            if (!res.data?.success) {
                if (toast?.error) toast.error(res.data?.message || "Failed to enqueue episodes for the agent runner");
                return;
            }
            const queued = res.data.enqueued_count ?? 0;
            try {
                await api.startAgentRunner({});
            } catch (err) {
                const detail = formatErrorMessage(err.response?.data?.detail ?? err.response?.data?.message ?? err.message);
                if (toast?.error) toast.error(`Queued ${queued} episode(s), but the agent runner could not start: ${detail}. Start it from the Queue page.`);
                return;
            }
            if (toast?.success) {
                toast.success(queued > 0
                    ? `Queued ${queued} episode(s); the agent runner is translating them — follow it on the Queue page`
                    : 'Nothing new to queue; the agent runner is working through the queue — follow it on the Queue page');
            }
        } catch (err) {
            if (toast?.error) toast.error(err.response?.data?.detail || err.message || "Failed to enqueue episodes for the agent runner");
        } finally {
            setStartingRunner(false);
        }
    };



    if (loading) {
        return (
            <div className="max-w-6xl mx-auto p-8 flex flex-col items-center justify-center min-h-[400px]">
                <Loader2 className="w-8 h-8 text-indigo-600 animate-spin mb-3" />
                <p className="text-gray-500">Loading project details...</p>
            </div>
        );
    }

    if (error || !project) {
        return (
            <div className="max-w-4xl mx-auto p-8 text-center space-y-4">
                <AlertCircle className="w-12 h-12 text-rose-500 mx-auto" />
                <h2 className="text-xl font-bold text-gray-900 dark:text-white">Project Not Found</h2>
                <p className="text-sm text-gray-500">{error || "Could not retrieve project data."}</p>
                <Link
                    to="/"
                    className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-sm font-semibold transition-colors"
                >
                    <ArrowLeft size={16} /> Back to Projects
                </Link>
            </div>
        );
    }

    return (
        <div className="max-w-6xl mx-auto p-6 md:p-8 space-y-6">
            {/* Top Navigation & Breadcrumbs */}
            <div className="flex items-center justify-between">
                <Link
                    to="/"
                    className="inline-flex items-center gap-2 text-xs font-bold text-gray-500 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors"
                >
                    <ArrowLeft size={14} /> Back to Dashboard
                </Link>

                <div className="flex items-center gap-2">
                    <button
                        type="button"
                        onClick={() => setPipelineWizardOpen(true)}
                        className="px-3.5 py-1.5 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-700 hover:to-purple-700 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 shadow-sm transition-all"
                    >
                        <Sparkles size={14} /> Guided Pipeline Wizard
                    </button>
                    <button
                        type="button"
                        onClick={() => setSyncModalOpen(true)}
                        className="px-3 py-1.5 text-xs font-bold text-gray-700 dark:text-gray-200 bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-750 border border-gray-200 dark:border-gray-700 rounded-xl flex items-center gap-1.5 transition-colors shadow-sm"
                        title="Sync with Disk & Media Server"
                    >
                        <RefreshCw size={13} />
                        <span>Sync Disk & Server</span>
                    </button>
                    <button
                        type="button"
                        onClick={() => setSettingsModalOpen(true)}
                        className="p-2 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-xl transition-colors"
                        title="Project Settings"
                    >
                        <SettingsIcon size={16} />
                    </button>
                </div>
            </div>

            {/* Project Header Card */}
            <div className="bg-white dark:bg-gray-800 rounded-3xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                <div>
                    <div className="flex items-center gap-3">
                        <h1 className="text-2xl font-black text-gray-900 dark:text-white tracking-tight">
                            {project.name || effectiveProjectName}
                        </h1>
                        <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold ${
                            project.type === 'parent'
                                ? 'bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300'
                                : 'bg-indigo-100 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-300'
                        }`}>
                            {project.type === 'parent' ? 'Universe Hub' : 'Series Project'}
                        </span>
                    </div>

                    <div className="flex items-center gap-3 mt-1.5 text-xs text-gray-500 dark:text-gray-400">
                        <span>Target Language: <strong className="text-gray-800 dark:text-gray-200">{project.target_language || 'Default'}</strong></span>
                        <span>&middot;</span>
                        <span>{episodes.length} Episodes</span>
                        {project.parent_project && (
                            <>
                                <span>&middot;</span>
                                <span>Parent: <strong className="text-purple-600 dark:text-purple-400">{project.parent_project}</strong></span>
                            </>
                        )}
                    </div>
                </div>

                <div className="flex items-center gap-2">
                    <button
                        type="button"
                        onClick={() => setTranslationModal({ open: true, episodeName: null, isBatch: true })}
                        disabled={episodes.length === 0}
                        className="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold flex items-center gap-2 shadow-md transition-all disabled:opacity-50"
                    >
                        <Play size={14} /> Translate All Pending
                    </button>
                </div>
            </div>

            {/* Guided Pipeline Wizard Banner / Modal */}
            {pipelineWizardOpen && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-md">
                    <div className="w-full max-w-3xl">
                        <SimplePipelineWizard
                            projectName={effectiveProjectName}
                            onComplete={() => {
                                setPipelineWizardOpen(false);
                                loadData();
                            }}
                            onCancel={() => setPipelineWizardOpen(false)}
                        />
                    </div>
                </div>
            )}

            {/* Tab Navigation Navigation Bar */}
            <div className="flex items-center gap-1.5 overflow-x-auto pb-1 border-b border-gray-200 dark:border-gray-800">
                {TABS.map(tab => {
                    const Icon = tab.icon;
                    const isActive = activeTab === tab.id;
                    return (
                        <button
                            key={tab.id}
                            type="button"
                            onClick={() => setActiveTab(tab.id)}
                            className={`px-4 py-2.5 rounded-xl text-xs font-bold flex items-center gap-2 transition-all whitespace-nowrap ${
                                isActive
                                    ? 'bg-indigo-600 text-white shadow-sm'
                                    : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
                            }`}
                        >
                            <Icon size={16} />
                            <span>{tab.label}</span>
                        </button>
                    );
                })}
            </div>

            {/* Active Tab Views */}
            <div className="pt-2">
                {activeTab === 'episodes' && (
                    <>
                        {/* Quick-Access Tools Bar */}
                        <div className="bg-gradient-to-r from-slate-50 to-indigo-50/40 dark:from-gray-800 dark:to-gray-800/80 rounded-2xl p-3.5 border border-gray-200 dark:border-gray-700 shadow-sm flex flex-wrap items-center justify-between gap-3 mb-4">
                            <div className="flex items-center gap-2.5">
                                <div className="p-2 bg-indigo-100 dark:bg-indigo-950 text-indigo-600 dark:text-indigo-400 rounded-xl">
                                    <Wrench size={16} />
                                </div>
                                <div>
                                    <div className="flex items-center gap-2">
                                        <span className="text-xs font-bold text-gray-900 dark:text-white uppercase tracking-wider">
                                            Quick Tools
                                        </span>
                                        {selectedEpisodes.size > 0 && (
                                            <span className="px-2 py-0.5 bg-indigo-600 text-white rounded-full text-[10px] font-bold">
                                                {selectedEpisodes.size} selected
                                            </span>
                                        )}
                                    </div>
                                    <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-0.5">
                                        Batch subtitle extraction, automated SubtitleEdit polish &amp; library re-sync
                                    </p>
                                </div>
                            </div>

                            <div className="flex items-center gap-2 flex-wrap">
                                {/* 0. AI Agent Translate */}
                                <button
                                    type="button"
                                    onClick={() => handleBatchAgentTranslate()}
                                    disabled={preparingAgent || startingRunner}
                                    className="px-3.5 py-2 bg-purple-500/10 dark:bg-purple-500/20 hover:bg-purple-500/25 text-purple-700 dark:text-purple-300 rounded-xl text-xs font-bold flex items-center gap-1.5 border border-purple-500/30 transition-all cursor-pointer shadow-sm disabled:opacity-50"
                                    title={selectedEpisodes.size > 0 ? `Prepare AI Agent translation workspace for ${selectedEpisodes.size} selected episode(s)` : "Enqueue entire series/movie for AI Agent translation"}
                                >
                                    <Bot size={14} className={preparingAgent ? "animate-spin" : ""} />
                                    <span>{selectedEpisodes.size > 0 ? `Agent Translate (${selectedEpisodes.size})` : 'Agent Translate'}</span>
                                </button>

                                {/* 0b. Queue the episodes and start the direct agent runner */}
                                <button
                                    type="button"
                                    onClick={handleTranslateWithRunner}
                                    disabled={preparingAgent || startingRunner}
                                    className="px-3.5 py-2 bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 border border-purple-400/30 transition-all cursor-pointer shadow-sm disabled:opacity-50"
                                    title={selectedEpisodes.size > 0 ? `Queue ${selectedEpisodes.size} selected episode(s) and let the agent runner translate them` : "Queue the entire series/movie and let the agent runner translate it"}
                                >
                                    {startingRunner ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                                    <span>{selectedEpisodes.size > 0 ? `Translate with runner (${selectedEpisodes.size})` : 'Translate with runner'}</span>
                                </button>

                                {/* 1. Batch Extract Subs */}
                                <button
                                    type="button"
                                    onClick={() => handleBatchExtractEmbedded(selectedEpisodes.size > 0 ? Array.from(selectedEpisodes) : null)}
                                    className="px-3.5 py-2 bg-amber-500/10 dark:bg-amber-500/20 hover:bg-amber-500/25 text-amber-700 dark:text-amber-300 rounded-xl text-xs font-bold flex items-center gap-1.5 border border-amber-500/30 transition-all cursor-pointer shadow-sm"
                                    title={selectedEpisodes.size > 0 ? `Batch extract embedded subtitles for ${selectedEpisodes.size} selected episode(s)` : "Batch extract embedded ASS/SRT tracks from all video files"}
                                >
                                    <Layers size={14} />
                                    <span>{selectedEpisodes.size > 0 ? `Batch Extract Subs (${selectedEpisodes.size})` : 'Batch Extract Subs'}</span>
                                </button>

                                {/* 2. SubtitleEdit Polish */}
                                <button
                                    type="button"
                                    onClick={() => setFixesModal({ open: true, episodeName: null, isBatch: true })}
                                    className="px-3.5 py-2 bg-indigo-500/10 dark:bg-indigo-500/20 hover:bg-indigo-500/25 text-indigo-700 dark:text-indigo-300 rounded-xl text-xs font-bold flex items-center gap-1.5 border border-indigo-500/30 transition-all cursor-pointer shadow-sm"
                                    title={selectedEpisodes.size > 0 ? `Apply SubtitleEdit fixes to ${selectedEpisodes.size} selected episode(s)` : "Apply SubtitleEdit fixes across all translated episodes"}
                                >
                                    <Sparkles size={14} />
                                    <span>{selectedEpisodes.size > 0 ? `SubtitleEdit Polish (${selectedEpisodes.size})` : 'SubtitleEdit Polish'}</span>
                                </button>

                                {/* 3. Format Conversion / Re-sync */}
                                <button
                                    type="button"
                                    onClick={() => setSyncModalOpen(true)}
                                    className="px-3.5 py-2 bg-emerald-500/10 dark:bg-emerald-500/20 hover:bg-emerald-500/25 text-emerald-700 dark:text-emerald-300 rounded-xl text-xs font-bold flex items-center gap-1.5 border border-emerald-500/30 transition-all cursor-pointer shadow-sm"
                                    title="Scan disk and media server for format changes (ASS/SRT) and re-sync subtitle paths"
                                >
                                    <RefreshCw size={14} />
                                    <span>Format Conversion / Re-sync</span>
                                </button>
                            </div>
                        </div>

                        <EpisodesTab
                            project={project}
                            episodes={episodes}
                            subprojects={subprojects}
                            selectedEpisodes={selectedEpisodes}
                            toggleEpisode={toggleEpisode}
                            toggleMultiple={toggleMultiple}
                            selectAll={selectAll}
                            clearSelection={clearSelection}
                            invertSelection={invertSelection}
                            onTranslateEpisode={(epName) => setTranslationModal({ open: true, episodeName: epName, isBatch: false })}
                            onBatchTranslate={() => setTranslationModal({ open: true, episodeName: null, isBatch: true })}
                            onAgentTranslate={handleAgentTranslateEpisode}
                            onBatchAgentTranslate={handleBatchAgentTranslate}
                            preparingAgent={preparingAgent}
                            onApplyFixes={(epName, isBatch = false) => setFixesModal({ open: true, episodeName: epName, isBatch })}
                            onCheckMissing={(epName) => setCheckMissingModal({ open: true, episodeName: epName })}
                            onScriptFix={(epName) => setScriptFixModal({ open: true, episodeName: epName })}
                            onEmbeddedTracks={(epName) => setEmbeddedTracksModal({ open: true, episodeName: epName })}
                            onBatchExtractEmbedded={handleBatchExtractEmbedded}
                            onDeleteEpisode={handleDeleteEpisode}
                            onBatchDownload={() => handleBatchDownload(Array.from(selectedEpisodes))}
                            onBatchExportToPath={() => handleBatchExportToPath(Array.from(selectedEpisodes))}
                            onBatchDelete={async (deleteDiskFile) => {
                                const success = await handleBatchDelete(Array.from(selectedEpisodes), deleteDiskFile);
                                if (success) {
                                    clearSelection();
                                }
                            }}
                            onUpload={handleUpload}
                            projectName={effectiveProjectName}
                        />
                    </>
                )}

                {activeTab === 'context' && (
                    <ContextTab
                        project={project}
                        projectName={effectiveProjectName}
                        onAiAction={handleAiAction}
                        onReload={loadData}
                        toast={toast}
                    />
                )}

                {activeTab === 'glossary' && (

                    <GlossaryTab
                        project={project}
                        projectName={effectiveProjectName}
                        onAiAction={handleAiAction}
                        onReload={loadData}
                        toast={toast}
                    />
                )}

                {activeTab === 'profiles' && (
                    <ProfilesTab
                        projectName={effectiveProjectName}
                        projectSettings={project.settings}
                    />
                )}

                {activeTab === 'summaries' && (
                    <SummariesTab
                        projectName={effectiveProjectName}
                        projectSettings={project.settings}
                    />
                )}

                {activeTab === 'visual_guard' && (
                    <VisualGuardTab
                        projectName={effectiveProjectName}
                        episodes={episodes}
                        onReload={loadData}
                        toast={toast}
                    />
                )}

                {activeTab === 'history' && (
                    <HistoryTab
                        projectName={effectiveProjectName}
                    />
                )}

                {activeTab === 'stats' && (
                    <StatsTab
                        tokenSummary={tokenSummary}
                        episodes={episodes}
                    />
                )}
            </div>

            {/* Modals Container */}
            <ProjectSettingsModal
                isOpen={settingsModalOpen}
                onClose={() => setSettingsModalOpen(false)}
                project={project}
                settings={project.settings}
                onSave={handleSaveSettings}
                tokenSummary={tokenSummary}
            />

            <SyncOptionsModal
                isOpen={syncModalOpen}
                onClose={() => setSyncModalOpen(false)}
                projectName={effectiveProjectName}
                onSuccess={loadData}
                toast={toast}
            />

            <TranslationModal
                isOpen={translationModal.open}
                onClose={() => setTranslationModal({ open: false, episodeName: null, isBatch: false })}
                onConfirm={handleConfirmTranslation}
                episodeName={translationModal.episodeName}
                isBatch={translationModal.isBatch}
                batchCount={translationModal.isBatch ? (selectedEpisodes.size || (episodes || []).filter(e => !e.translated && e.has_source !== false && (e.line_count > 0 || e.metadata?.arr_sub_path)).length) : 1}
                defaultModel={project.settings?.translation_model}
            />


            <ApplyFixesModal
                isOpen={fixesModal.open}
                onClose={() => setFixesModal({ open: false, episodeName: null, isBatch: false })}
                projectName={effectiveProjectName}
                episodeName={fixesModal.episodeName}
                isBatch={fixesModal.isBatch}
                selectedEpisodes={Array.from(selectedEpisodes)}
                onSuccess={loadData}
                toast={toast}
            />

            <CheckMissingModal
                isOpen={checkMissingModal.open}
                onClose={() => setCheckMissingModal({ open: false, episodeName: null })}
                projectName={effectiveProjectName}
                episodeName={checkMissingModal.episodeName}
                onSuccess={loadData}
                toast={toast}
            />

            <ScriptFixModal
                isOpen={scriptFixModal.open}
                onClose={() => setScriptFixModal({ open: false, episodeName: null })}
                projectName={effectiveProjectName}
                episodeName={scriptFixModal.episodeName}
                onSuccess={loadData}
                toast={toast}
            />

            <EmbeddedTracksModal
                isOpen={embeddedTracksModal.open}
                onClose={() => setEmbeddedTracksModal({ open: false, episodeName: null })}
                projectName={effectiveProjectName}
                episodeName={embeddedTracksModal.episodeName}
                onSuccess={loadData}
                toast={toast}
            />


            <AgentTaskModal
                agentModalData={agentModalData}
                onClose={() => setAgentModalData(null)}
            />
        </div>
    );
};

export default ProjectDetail;
