import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, Link, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../api';
import {
    ArrowLeft, Upload, Book, Sparkles, Save, Edit2, Trash2,
    Settings, Check, Folder, Film, Tv, X, Languages,
    FileText, RefreshCw, Download, Play
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import GlossaryEditor from './GlossaryEditor';
import GlossaryReviewModal from './GlossaryReviewModal';
import ContextReviewModal from './ContextReviewModal';
import ProjectSettingsModal from './ProjectSettingsModal';
import PipelineStepper from './PipelineStepper';
import SimplePipelineWizard from './SimplePipelineWizard';
import { useJobs } from '../context/JobContext';
import { useToast } from '../context/ToastContext';

// --- Tab Definitions ---
const TABS = {
    EPISODES: 'episodes',
    GLOSSARY: 'glossary',
    CONTEXT: 'context',
    SUBPROJECTS: 'subprojects',
};

const ProjectDetail = () => {
    const { projectName } = useParams();
    const { activeJobs, addJob, removeJob, cancelJob } = useJobs();
    const toast = useToast();
    const location = useLocation();
    const navigate = useNavigate();
    const handledJobsRef = useRef(new Set());
    const fileInputRef = useRef(null);

    // Core State
    const [project, setProject] = useState(null);
    const [episodes, setEpisodes] = useState([]);
    const [subprojects, setSubprojects] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [activeTab, setActiveTab] = useState(TABS.EPISODES);
    const [isSimplePipelineActive, setIsSimplePipelineActive] = useState(false);

    // Selection & UI State
    const [selectedEpisodes, setSelectedEpisodes] = useState(new Set());
    const [isDownloading, setIsDownloading] = useState(false);
    const [isDragging, setIsDragging] = useState(false);
    const [isSettingsOpen, setIsSettingsOpen] = useState(false);

    // Editing State
    const [editedContext, setEditedContext] = useState('');
    const [isEditingContext, setIsEditingContext] = useState(false);

    // Modal State
    const [glossaryReviewModal, setGlossaryReviewModal] = useState({ isOpen: false, newTerms: [], existingTerms: [] });
    const [contextReviewModal, setContextReviewModal] = useState({ isOpen: false, newContext: '', currentContext: '' });

    // Job Tracking
    const [contextJobId, setContextJobId] = useState(null);
    const [glossaryJobId, setGlossaryJobId] = useState(null);
    const [pipelineJobId, setPipelineJobId] = useState(null);

    const isParent = project?.type === 'parent';
    const isMovie = project?.type === 'movie';

    // --- Data Loading ---
    useEffect(() => { loadData(); }, [projectName]);

    const loadData = async () => {
        setLoading(true);
        setError(null);
        try {
            const [projRes, allProjRes] = await Promise.all([
                api.getProject(projectName),
                api.getProjects()
            ]);
            const projData = projRes.data;
            setProject(projData);
            setEditedContext(projData.context_guide || '');

            if (projData.type === 'parent') {
                setActiveTab(TABS.SUBPROJECTS);
                const allDetails = await Promise.all(allProjRes.data.map(async name => {
                    try {
                        const res = await api.getProject(name);
                        return { ...res.data, name };
                    } catch { return null; }
                }));
                setSubprojects(allDetails.filter(p => p && p.parent_project === projectName));
            } else {
                const epRes = await api.getEpisodes(projectName);
                setEpisodes(epRes.data);
            }
        } catch (err) {
            console.error("Failed to load project data", err);
            setError("Failed to load project. It might not exist.");
        } finally {
            setLoading(false);
        }
    };

    // --- Job Completion Handling ---
    const handleJobComplete = useCallback((job) => {
        if (!job) return;
        const result = job.result;

        if (job.type === 'context' || job.type === 'create_context' || job.type === 'enhance_context') {
            if (result?.context_guide) {
                setContextReviewModal({
                    isOpen: true,
                    newContext: result.context_guide,
                    currentContext: project?.context_guide || ''
                });
            }
        } else if (job.type === 'glossary' || job.type === 'create_glossary' || job.type === 'enhance_glossary') {
            const existingTerms = project?.glossary?.terms || [];
            const existingNames = new Set(existingTerms.map(t => t.term.toLowerCase()));
            const newTerms = (result?.terms || []).filter(t => !existingNames.has(t.term.toLowerCase()));
            setGlossaryReviewModal({ isOpen: true, newTerms, existingTerms });
        } else if (job.type === 'translate_episode') {
            loadData();
            const epName = job.metadata?.episodeName || 'Episode';
            toast.success(`Translation complete for ${epName}`);
        } else if (job.type === 'batch_translate') {
            loadData();
            toast.success('Batch translation complete');
        } else if (job.type === 'pipeline') {
            loadData();
            toast.success('Pipeline complete — all episodes translated');
            setPipelineJobId(null);
        }
    }, [project, projectName, navigate, toast]);

    // Watch for completed jobs
    useEffect(() => {
        if (!project) return;

        const storageKey = `handledJobs_${projectName}`;
        if (handledJobsRef.current.size === 0) {
            try {
                const stored = localStorage.getItem(storageKey);
                if (stored) handledJobsRef.current = new Set(JSON.parse(stored));
            } catch { }
        }

        const projectJobs = Object.values(activeJobs).filter(
            job => ((job.metadata?.projectId === projectName) || (location.state?.highlightJobId === job.id)) && job.status === 'completed'
        );

        for (const job of projectJobs) {
            if (job.type === 'enhance_context' || job.type === 'create_context' || job.type === 'enhance_glossary' || job.type === 'create_glossary' || job.type === 'pipeline') continue;
            const isHighlighted = location.state?.highlightJobId === job.id;
            const isUnhandled = !handledJobsRef.current.has(job.id);
            if ((isHighlighted || isUnhandled) && (job.result || job.type === 'translate_episode' || job.type === 'batch_translate')) {
                handleJobComplete(job);
                handledJobsRef.current.add(job.id);
                try { localStorage.setItem(storageKey, JSON.stringify([...handledJobsRef.current])); } catch { }
            }
        }
    }, [activeJobs, projectName, project, location.state, handleJobComplete]);

    // --- Actions ---
    const handleAIAction = async (type, action) => {
        try {
            let res;
            const settings = project.settings || {};

            if (type === 'context') {
                const model = settings.context_model || 'gemini-flash-lite-latest';
                res = action === 'enhance'
                    ? await api.enhanceContext(projectName, model)
                    : await api.createContext(projectName, model);
            } else {
                const model = settings.glossary_model || 'gemini-flash-lite-latest';
                const selected = selectedEpisodes.size > 0 ? Array.from(selectedEpisodes) : [];
                if (action === 'enhance') {
                    res = await api.enhanceGlossary(projectName, { episode_names: selected }, model, selected.length === 0);
                } else {
                    res = await api.createGlossary(projectName, model);
                }
            }

            if (res.data.job_id) {
                addJob(res.data.job_id, type, `AI: ${action} ${type}`, { link: `/project/${encodeURIComponent(projectName)}`, projectId: projectName });
                if (type === 'context') setContextJobId(res.data.job_id);
                else setGlossaryJobId(res.data.job_id);
            }
        } catch (err) {
            console.error(`Failed to ${action} ${type}`, err);
            toast.error(`Failed to ${action} ${type}`);
        }
    };

    const handleSaveContext = async (contextToSave = null) => {
        const context = contextToSave !== null ? contextToSave : editedContext;
        try {
            await api.updateProject(projectName, { context_guide: context });
            setIsEditingContext(false);
            setProject(prev => ({ ...prev, context_guide: context }));
            setEditedContext(context);
        } catch (err) {
            console.error('Failed to save context', err);
            toast.error('Failed to save context guide');
        }
    };

    const handleSaveGlossary = async (newGlossary) => {
        try {
            await api.updateProject(projectName, { glossary: newGlossary });
            setProject(prev => ({ ...prev, glossary: newGlossary }));
        } catch (err) {
            console.error('Failed to save glossary', err);
            toast.error('Failed to save glossary');
        }
    };

    const handleDeleteContext = async () => {
        if (!window.confirm("Delete the context guide? This cannot be undone.")) return;
        try {
            await api.deleteContext(projectName);
            setContextJobId(null);
            setEditedContext('');
            setProject(prev => ({ ...prev, context_guide: '' }));
        } catch (err) {
            console.error('Failed to delete context', err);
            toast.error('Failed to delete context');
        }
    };

    const handleDeleteGlossary = async () => {
        if (!window.confirm("Delete the glossary? This cannot be undone.")) return;
        try {
            await api.deleteGlossary(projectName);
            setGlossaryJobId(null);
            setProject(prev => ({ ...prev, glossary: { terms: [] } }));
        } catch (err) {
            console.error('Failed to delete glossary', err);
            toast.error('Failed to delete glossary');
        }
    };

    const handleSaveSettings = async (newSettings) => {
        try {
            await api.updateProject(projectName, { settings: newSettings });
            setProject(prev => ({ ...prev, settings: newSettings }));
        } catch (err) {
            console.error('Failed to save settings', err);
            toast.error('Failed to save settings');
        }
    };

    // --- Episode Actions ---
    const handleSelectAll = () => {
        setSelectedEpisodes(prev =>
            prev.size === episodes.length ? new Set() : new Set(episodes.map(ep => ep.name))
        );
    };

    const handleSelectEpisode = (name) => {
        setSelectedEpisodes(prev => {
            const next = new Set(prev);
            next.has(name) ? next.delete(name) : next.add(name);
            return next;
        });
    };

    const handleTranslateEpisode = async (episodeName) => {
        try {
            const model = project.settings?.translation_model || 'gemini-flash-latest';
            const res = await api.translateEpisode(projectName, episodeName, model);
            if (res.data.job_id) {
                addJob(res.data.job_id, 'translate_episode', `Translating ${episodeName}`, { projectId: projectName, episodeName });
            }
        } catch (err) {
            console.error('Failed to start translation', err);
            toast.error('Failed to start translation');
        }
    };

    // --- Pipeline Actions ---
    const handleStartPipeline = async (mode = 'auto') => {
        try {
            const settings = project.settings || {};
            const res = await api.startPipeline(projectName, {
                mode,
                skip_context: !!project.context_guide,
                skip_glossary: project.glossary?.terms?.length > 0,
                episode_names: selectedEpisodes.size > 0 ? Array.from(selectedEpisodes) : null,
                model: settings.translation_model || 'gemini-2.5-flash',
                context_model: settings.context_model || null,
                glossary_model: settings.glossary_model || null,
                translation_model: settings.translation_model || null,
            });
            if (res.data.job_id) {
                addJob(res.data.job_id, 'pipeline', `Auto-Translate Pipeline`, { projectId: projectName });
                setPipelineJobId(res.data.job_id);
                toast.info(`Pipeline started (${mode} mode)`);
            }
        } catch (err) {
            console.error('Failed to start pipeline', err);
            toast.error('Failed to start pipeline');
        }
    };

    const handleContinuePipeline = async () => {
        if (!pipelineJobId) return;
        try {
            await api.continuePipeline(projectName, pipelineJobId);
            toast.info('Pipeline continuing...');
        } catch (err) {
            toast.error('Failed to continue pipeline');
        }
    };

    const handleCancelPipeline = async () => {
        if (!pipelineJobId) return;
        try {
            await cancelJob(pipelineJobId);
            setPipelineJobId(null);
            toast.info('Pipeline cancelled');
        } catch (err) {
            toast.error('Failed to cancel pipeline');
        }
    };

    const handleBatchTranslate = async () => {
        try {
            const model = project.settings?.translation_model || 'gemini-flash-latest';
            const names = Array.from(selectedEpisodes);
            const res = await api.batchTranslate(projectName, names, model);
            if (res.data.job_id) {
                addJob(res.data.job_id, 'batch_translate', `Batch translating ${names.length} episodes`, { projectId: projectName });
            }
        } catch (err) {
            console.error('Batch translate failed', err);
            toast.error('Failed to start batch translation');
        }
    };

    const handleBatchDownload = async () => {
        try {
            setIsDownloading(true);
            await api.batchDownload(projectName, Array.from(selectedEpisodes));
        } catch (err) {
            console.error('Download failed', err);
            toast.error('Download failed');
        } finally {
            setIsDownloading(false);
        }
    };

    const handleBatchDelete = async () => {
        if (!window.confirm(`Delete ${selectedEpisodes.size} episodes? This cannot be undone.`)) return;
        try {
            await Promise.all(Array.from(selectedEpisodes).map(name => api.deleteEpisode(projectName, name)));
            setSelectedEpisodes(new Set());
            loadData();
        } catch (err) {
            console.error('Delete failed', err);
            toast.error('Delete failed');
        }
    };

    // --- File Upload (drag & drop + click) ---
    const handleFiles = async (files) => {
        if (!files || files.length === 0) return;
        try {
            for (const f of Array.from(files)) {
                let epName = f.name || 'unnamed';
                const match = f.name?.match(/[Ss]\d{2}[Ee]\d{2}/);
                if (match) epName = match[0].toUpperCase();
                await api.uploadEpisode(projectName, epName, f);
            }
            loadData();
        } catch (e) {
            toast.error('Upload failed');
        }
    };

    const handleDrop = (e) => {
        e.preventDefault();
        setIsDragging(false);
        handleFiles(e.dataTransfer.files);
    };

    const handleDragOver = (e) => { e.preventDefault(); setIsDragging(true); };
    const handleDragLeave = () => setIsDragging(false);

    // --- Job status helpers ---
    const getJobStatus = (jobId) => {
        if (!jobId) return null;
        return activeJobs[jobId] || null;
    };

    // Get active pipeline job (if any)
    const pipelineJob = pipelineJobId ? activeJobs[pipelineJobId] : null;
    const isPipelineActive = pipelineJob && !['completed', 'failed', 'cancelled'].includes(pipelineJob.status);

    const renderJobButton = (jobId, setJobId, label) => {
        const job = getJobStatus(jobId);
        if (!job) return null;

        if (job.status === 'running' || job.status === 'pending') {
            return (
                <div className="flex items-center gap-2">
                    <span className="text-sm text-indigo-600 animate-pulse font-medium">{label}...</span>
                    <button onClick={() => setJobId(null)} className="p-1 hover:bg-red-50 text-red-500 rounded"><X size={14} /></button>
                </div>
            );
        }

        if (job.status === 'completed') {
            return (
                <button
                    onClick={() => { handleJobComplete(job); setJobId(null); }}
                    className="flex items-center gap-2 bg-green-600 hover:bg-green-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium"
                >
                    <Check size={14} /> Review Result
                </button>
            );
        }
        return null;
    };

    // --- Render ---
    if (loading) return <div className="h-screen flex items-center justify-center text-gray-500">Loading project...</div>;
    if (error) return <div className="h-screen flex items-center justify-center text-red-500">{error}</div>;
    if (!project) return null;

    const tabs = isParent
        ? [
            { id: TABS.SUBPROJECTS, label: 'Subprojects', icon: Folder, count: subprojects.length },
            { id: TABS.GLOSSARY, label: 'Glossary', icon: Book, count: project.glossary?.terms?.length || 0 },
            { id: TABS.CONTEXT, label: 'Context Guide', icon: FileText },
        ]
        : [
            { id: TABS.EPISODES, label: isMovie ? 'Files' : 'Episodes', icon: Film, count: episodes.length },
            { id: TABS.GLOSSARY, label: 'Glossary', icon: Book, count: project.glossary?.terms?.length || 0 },
            { id: TABS.CONTEXT, label: 'Context Guide', icon: FileText },
        ];

    return (
        <div className="min-h-screen pb-20">
            {/* Header */}
            <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 sticky top-0 z-10">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
                    <div className="flex items-center gap-4">
                        <Link to="/" className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">
                            <ArrowLeft size={20} />
                        </Link>
                        <div>
                            {project.parent_project && (
                                <Link to={`/project/${encodeURIComponent(project.parent_project)}`} className="text-xs text-indigo-600 hover:underline block mb-0.5">
                                    ← {project.parent_project}
                                </Link>
                            )}
                            <div className="flex items-center gap-2">
                                <h1 className="text-xl font-bold text-gray-900 dark:text-white">{project.show_name}</h1>
                                <span className={`text-xs font-medium px-2 py-0.5 rounded uppercase tracking-wide ${isParent ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400' :
                                    isMovie ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400' :
                                        'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                                    }`}>
                                    {project.type || 'show'}
                                </span>
                                {project.target_language && (
                                    <span className="text-xs text-gray-500 dark:text-gray-400">→ {project.target_language}</span>
                                )}
                            </div>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        {!isParent && (
                            <div className="relative group">
                                <button
                                    onClick={() => setIsSimplePipelineActive(true)}
                                    disabled={isPipelineActive || isSimplePipelineActive}
                                    className="flex items-center gap-2 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-700 hover:to-purple-700 disabled:opacity-50 text-white px-5 py-2.5 rounded-2xl text-sm font-bold transition-all shadow-lg shadow-indigo-500/20 hover:shadow-indigo-500/40 hover:-translate-y-0.5 active:translate-y-0"
                                >
                                    <Sparkles size={18} className="animate-pulse" />
                                    Auto-Translate
                                </button>
                                {/* Dropdown for legacy/advanced mode */}
                                <div className="absolute right-0 mt-2 w-56 bg-white dark:bg-gray-800 rounded-2xl shadow-2xl border border-gray-100 dark:border-gray-700 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-20 translate-y-1 group-hover:translate-y-0">
                                    <div className="px-4 py-2 text-xs font-semibold text-gray-400 uppercase tracking-wider">Advanced Options</div>
                                    <button
                                        onClick={() => handleStartPipeline('auto')}
                                        disabled={isPipelineActive}
                                        className="w-full text-left px-4 py-3 text-sm hover:bg-gray-50 dark:hover:bg-gray-700 flex items-center gap-2 transition-colors disabled:opacity-50"
                                    >
                                        <RefreshCw size={14} className="text-gray-400" />
                                        <span>Legacy Full Pipeline</span>
                                    </button>
                                    <button
                                        onClick={() => handleStartPipeline('step')}
                                        disabled={isPipelineActive}
                                        className="w-full text-left px-4 py-3 text-sm hover:bg-gray-50 dark:hover:bg-gray-700 border-t border-gray-100 dark:border-gray-700 flex items-center gap-2 transition-colors rounded-b-2xl disabled:opacity-50"
                                    >
                                        <RefreshCw size={14} className="text-gray-400" />
                                        <span>Legacy Step-by-Step</span>
                                    </button>
                                </div>
                            </div>
                        )}
                        <button onClick={() => setIsSettingsOpen(true)} className="p-2.5 text-gray-500 hover:text-indigo-600 dark:text-gray-400 dark:hover:text-indigo-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-xl transition-all" title="Project Settings">
                            <Settings size={22} />
                        </button>
                    </div>
                </div>
            </header>

            {/* Tabs */}
            <div className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <nav className="flex gap-1" aria-label="Tabs">
                        {tabs.map(tab => {
                            const Icon = tab.icon;
                            const isActive = activeTab === tab.id;
                            return (
                                <button
                                    key={tab.id}
                                    onClick={() => setActiveTab(tab.id)}
                                    className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${isActive
                                        ? 'border-indigo-600 text-indigo-600 dark:text-indigo-400'
                                        : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300 dark:text-gray-400 dark:hover:text-gray-200'
                                        }`}
                                >
                                    <Icon size={16} />
                                    {tab.label}
                                    {tab.count !== undefined && (
                                        <span className={`text-xs px-1.5 py-0.5 rounded-full ${isActive ? 'bg-indigo-100 text-indigo-600 dark:bg-indigo-900/50 dark:text-indigo-300' : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400'
                                            }`}>
                                            {tab.count}
                                        </span>
                                    )}
                                </button>
                            );
                        })}
                    </nav>
                </div>
            </div>

            {/* Pipeline Stepper */}
            <AnimatePresence>
                {pipelineJob && (
                    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-4">
                        <PipelineStepper
                            job={pipelineJob}
                            onContinue={handleContinuePipeline}
                            onCancel={handleCancelPipeline}
                        />
                    </div>
                )}
            </AnimatePresence>

            {/* Tab Content */}
            <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
                {isSimplePipelineActive ? (
                    <SimplePipelineWizard 
                        projectName={projectName} 
                        onComplete={() => {
                            setIsSimplePipelineActive(false);
                            loadData();
                        }}
                        onCancel={() => setIsSimplePipelineActive(false)}
                    />
                ) : (
                    <AnimatePresence mode="wait">
                    {/* ======= EPISODES TAB ======= */}
                    {activeTab === TABS.EPISODES && (
                        <motion.div key="episodes" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.15 }}>
                            {/* Upload Area */}
                            <div
                                className={`mb-6 border-2 border-dashed rounded-xl p-6 text-center transition-all cursor-pointer ${isDragging ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-900/20' : 'border-gray-300 dark:border-gray-600 hover:border-indigo-400 hover:bg-gray-50 dark:hover:bg-gray-800/50'
                                    }`}
                                onDrop={handleDrop}
                                onDragOver={handleDragOver}
                                onDragLeave={handleDragLeave}
                                onClick={() => fileInputRef.current?.click()}
                            >
                                <input ref={fileInputRef} type="file" accept=".srt" multiple onChange={(e) => handleFiles(e.target.files)} className="hidden" />
                                <Upload size={24} className={`mx-auto mb-2 ${isDragging ? 'text-indigo-500' : 'text-gray-400'}`} />
                                <p className={`text-sm font-medium ${isDragging ? 'text-indigo-600' : 'text-gray-500 dark:text-gray-400'}`}>
                                    {isDragging ? 'Drop files here' : `Drop .srt files here or click to upload`}
                                </p>
                            </div>

                            {episodes.length === 0 ? (
                                <div className="text-center py-16 text-gray-400">
                                    <Film size={48} className="mx-auto mb-4 opacity-50" />
                                    <p className="text-lg font-medium text-gray-500 dark:text-gray-400">No episodes yet</p>
                                    <p className="text-sm mt-1">Upload .srt files to get started</p>
                                </div>
                            ) : (
                                <>
                                    {/* Select All */}
                                    <div className="flex items-center gap-2 mb-3 px-1">
                                        <input
                                            type="checkbox"
                                            checked={selectedEpisodes.size === episodes.length}
                                            onChange={handleSelectAll}
                                            className="w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                                        />
                                        <span className="text-sm text-gray-500">Select All ({episodes.length})</span>
                                    </div>

                                    {/* Episode List */}
                                    <div className="space-y-2">
                                        {episodes.map(ep => (
                                            <div
                                                key={ep.name}
                                                className={`bg-white dark:bg-gray-800 p-4 rounded-xl border transition-all flex justify-between items-center ${selectedEpisodes.has(ep.name)
                                                    ? 'border-indigo-500 ring-1 ring-indigo-500 shadow-sm'
                                                    : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'
                                                    }`}
                                            >
                                                <div className="flex items-center gap-3">
                                                    <input
                                                        type="checkbox"
                                                        checked={selectedEpisodes.has(ep.name)}
                                                        onChange={() => handleSelectEpisode(ep.name)}
                                                        className="w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                                                    />
                                                    <div>
                                                        <div className="flex items-center gap-2">
                                                            <h3 className="font-semibold text-gray-900 dark:text-white">{ep.name}</h3>
                                                            {ep.season && <span className="text-xs bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 px-1.5 py-0.5 rounded">S{String(ep.season).padStart(2, '0')}</span>}
                                                            {ep.translated && (
                                                                <span className="flex items-center gap-1 text-xs font-medium text-green-600 bg-green-50 dark:bg-green-900/30 dark:text-green-400 px-2 py-0.5 rounded-full">
                                                                    <Check size={10} /> Translated
                                                                </span>
                                                            )}
                                                        </div>
                                                        <p className="text-xs text-gray-400 mt-0.5">{ep.line_count} lines</p>
                                                    </div>
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    <button
                                                        onClick={() => handleTranslateEpisode(ep.name)}
                                                        className="flex items-center gap-1.5 text-gray-500 hover:text-indigo-600 px-3 py-1.5 rounded-lg hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors text-sm font-medium"
                                                        title="Translate"
                                                    >
                                                        <Languages size={16} />
                                                        <span className="hidden sm:inline">Translate</span>
                                                    </button>
                                                    <Link
                                                        to={`/project/${encodeURIComponent(projectName)}/episode/${encodeURIComponent(ep.name)}`}
                                                        className="text-indigo-600 hover:text-indigo-700 font-medium text-sm px-3 py-1.5 rounded-lg hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors"
                                                    >
                                                        Edit
                                                    </Link>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </>
                            )}
                        </motion.div>
                    )}

                    {/* ======= GLOSSARY TAB ======= */}
                    {activeTab === TABS.GLOSSARY && (
                        <motion.div key="glossary" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.15 }}>
                            {/* Action Bar */}
                            <div className="flex items-center justify-between mb-4">
                                <div className="flex items-center gap-2">
                                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                                        Glossary
                                        <span className="text-sm font-normal text-gray-500 ml-2">({project.glossary?.terms?.length || 0} terms)</span>
                                    </h2>
                                </div>
                                <div className="flex items-center gap-2">
                                    {renderJobButton(glossaryJobId, setGlossaryJobId, 'Enhancing')}
                                    {!getJobStatus(glossaryJobId) && (
                                        <>
                                            <button
                                                onClick={() => handleAIAction('glossary', project.glossary?.terms?.length > 0 ? 'enhance' : 'create')}
                                                className="flex items-center gap-1.5 text-indigo-600 hover:text-indigo-700 px-3 py-1.5 rounded-lg hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors text-sm font-medium"
                                            >
                                                <Sparkles size={14} />
                                                {project.glossary?.terms?.length > 0 ? 'AI Enhance' : 'AI Create'}
                                            </button>
                                            {project.glossary?.terms?.length > 0 && (
                                                <button
                                                    onClick={handleDeleteGlossary}
                                                    className="p-1.5 text-red-500 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors"
                                                    title="Delete glossary"
                                                >
                                                    <Trash2 size={16} />
                                                </button>
                                            )}
                                        </>
                                    )}
                                </div>
                            </div>

                            {/* Glossary Editor - always editable */}
                            <GlossaryEditor
                                glossary={project.glossary || { terms: [] }}
                                readOnly={false}
                                onSave={handleSaveGlossary}
                                onCancel={() => { }}
                                isSaving={false}
                                hideSaveButton={false}
                            />
                        </motion.div>
                    )}

                    {/* ======= CONTEXT GUIDE TAB ======= */}
                    {activeTab === TABS.CONTEXT && (
                        <motion.div key="context" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.15 }}>
                            {/* Action Bar */}
                            <div className="flex items-center justify-between mb-4">
                                <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Context Guide</h2>
                                <div className="flex items-center gap-2">
                                    {renderJobButton(contextJobId, setContextJobId, 'Generating')}
                                    {!getJobStatus(contextJobId) && (
                                        <>
                                            <button
                                                onClick={() => handleAIAction('context', project.context_guide ? 'enhance' : 'create')}
                                                className="flex items-center gap-1.5 text-indigo-600 hover:text-indigo-700 px-3 py-1.5 rounded-lg hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors text-sm font-medium"
                                            >
                                                <Sparkles size={14} />
                                                {project.context_guide ? 'AI Enhance' : 'AI Create'}
                                            </button>
                                            {isEditingContext ? (
                                                <>
                                                    <button onClick={() => { setIsEditingContext(false); setEditedContext(project.context_guide || ''); }} className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700 rounded-lg hover:bg-gray-100 transition-colors">Cancel</button>
                                                    <button onClick={() => handleSaveContext()} className="flex items-center gap-1.5 bg-green-600 hover:bg-green-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium transition-colors">
                                                        <Save size={14} /> Save
                                                    </button>
                                                </>
                                            ) : (
                                                <>
                                                    <button onClick={() => setIsEditingContext(true)} className="flex items-center gap-1.5 text-gray-600 hover:text-gray-700 px-3 py-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-sm font-medium">
                                                        <Edit2 size={14} /> Edit
                                                    </button>
                                                    {project.context_guide && (
                                                        <button onClick={handleDeleteContext} className="p-1.5 text-red-500 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors" title="Delete context">
                                                            <Trash2 size={16} />
                                                        </button>
                                                    )}
                                                </>
                                            )}
                                        </>
                                    )}
                                </div>
                            </div>

                            {/* Context Content */}
                            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
                                {isEditingContext ? (
                                    <textarea
                                        value={editedContext}
                                        onChange={(e) => setEditedContext(e.target.value)}
                                        placeholder="Describe the tone, style, and context for translations (e.g., 'Formal fantasy dialogue with British spelling')"
                                        className="w-full min-h-[400px] px-6 py-4 border-none focus:ring-0 dark:bg-gray-800 dark:text-white resize-y font-mono text-sm leading-relaxed outline-none"
                                    />
                                ) : (
                                    <div className="px-6 py-4 min-h-[200px]">
                                        {project.context_guide ? (
                                            <p className="whitespace-pre-wrap text-gray-700 dark:text-gray-300 leading-relaxed">{project.context_guide}</p>
                                        ) : (
                                            <div className="flex flex-col items-center justify-center py-12 text-gray-400">
                                                <FileText size={48} className="mb-4 opacity-50" />
                                                <p className="text-lg font-medium text-gray-500 dark:text-gray-400">No context guide yet</p>
                                                <p className="text-sm mt-1">Click "AI Create" to generate one, or "Edit" to write manually</p>
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        </motion.div>
                    )}

                    {/* ======= SUBPROJECTS TAB (Parent Only) ======= */}
                    {activeTab === TABS.SUBPROJECTS && (
                        <motion.div key="subprojects" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.15 }}>
                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                                {subprojects.map(sub => (
                                    <Link key={sub.name} to={`/project/${encodeURIComponent(sub.name)}`} className="block group">
                                        <div className="bg-white dark:bg-gray-800 p-5 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-indigo-300 dark:hover:border-indigo-600 hover:shadow-md transition-all">
                                            <div className="flex items-center gap-3">
                                                <div className={`p-2.5 rounded-lg ${sub.type === 'movie' ? 'bg-purple-100 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400' : 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400'}`}>
                                                    {sub.type === 'movie' ? <Film size={20} /> : <Tv size={20} />}
                                                </div>
                                                <div>
                                                    <h3 className="font-bold text-gray-900 dark:text-white group-hover:text-indigo-600 transition-colors">{sub.show_name}</h3>
                                                    <span className="text-xs text-gray-500 capitalize">{sub.type}</span>
                                                </div>
                                            </div>
                                        </div>
                                    </Link>
                                ))}
                                {subprojects.length === 0 && (
                                    <div className="col-span-full text-center py-16 text-gray-400">
                                        <Folder size={48} className="mx-auto mb-4 opacity-50" />
                                        <p className="text-lg font-medium text-gray-500">No subprojects</p>
                                        <p className="text-sm mt-1">Create child projects from the main dashboard</p>
                                    </div>
                                )}
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>
                )}
            </main>

            {/* Batch Actions Bar */}
            <AnimatePresence>
                {selectedEpisodes.size > 0 && (
                    <motion.div
                        initial={{ y: 100, opacity: 0 }}
                        animate={{ y: 0, opacity: 1 }}
                        exit={{ y: 100, opacity: 0 }}
                        className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-white dark:bg-gray-800 rounded-full shadow-2xl border border-gray-200 dark:border-gray-700 px-6 py-3 flex items-center gap-4 z-40"
                    >
                        <div className="flex items-center gap-2 border-r border-gray-200 dark:border-gray-700 pr-4">
                            <span className="font-bold text-indigo-600">{selectedEpisodes.size}</span>
                            <span className="text-sm text-gray-500">selected</span>
                        </div>
                        <div className="flex items-center gap-1">
                            <button onClick={handleBatchTranslate} className="p-2 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 text-indigo-600 rounded-full transition-colors" title="Translate Selected">
                                <Languages size={20} />
                            </button>
                            <button onClick={handleBatchDownload} disabled={isDownloading} className="p-2 hover:bg-blue-50 dark:hover:bg-blue-900/20 text-blue-600 rounded-full transition-colors disabled:opacity-50" title="Download Selected">
                                <Download size={20} />
                            </button>
                            <div className="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-1" />
                            <button onClick={handleBatchDelete} className="p-2 hover:bg-red-50 dark:hover:bg-red-900/20 text-red-600 rounded-full transition-colors" title="Delete Selected">
                                <Trash2 size={18} />
                            </button>
                        </div>
                        <button onClick={() => setSelectedEpisodes(new Set())} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-full text-gray-400">
                            <X size={16} />
                        </button>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Modals */}
            <AnimatePresence>
                {glossaryReviewModal.isOpen && (
                    <GlossaryReviewModal
                        isOpen={glossaryReviewModal.isOpen}
                        onClose={() => setGlossaryReviewModal({ ...glossaryReviewModal, isOpen: false })}
                        newTerms={glossaryReviewModal.newTerms}
                        existingTerms={project.glossary?.terms || []}
                        onConfirm={(selectedTerms) => {
                            handleSaveGlossary({ terms: [...(project.glossary?.terms || []), ...selectedTerms] });
                            setGlossaryReviewModal({ ...glossaryReviewModal, isOpen: false });
                            if (glossaryJobId) { removeJob(glossaryJobId); setGlossaryJobId(null); }
                        }}
                        onDelete={() => { if (glossaryJobId) { removeJob(glossaryJobId); setGlossaryJobId(null); } }}
                    />
                )}
                {contextReviewModal.isOpen && (
                    <ContextReviewModal
                        isOpen={contextReviewModal.isOpen}
                        onClose={() => setContextReviewModal({ ...contextReviewModal, isOpen: false })}
                        newContext={contextReviewModal.newContext}
                        currentContext={contextReviewModal.currentContext}
                        onConfirm={(finalContext) => {
                            handleSaveContext(finalContext);
                            setContextReviewModal({ ...contextReviewModal, isOpen: false });
                            if (contextJobId) { removeJob(contextJobId); setContextJobId(null); }
                        }}
                        onDelete={() => { if (contextJobId) { removeJob(contextJobId); setContextJobId(null); } }}
                    />
                )}
                {isSettingsOpen && (
                    <ProjectSettingsModal
                        isOpen={isSettingsOpen}
                        onClose={() => setIsSettingsOpen(false)}
                        settings={project.settings}
                        onSave={handleSaveSettings}
                    />
                )}
            </AnimatePresence>
        </div>
    );
};

export default ProjectDetail;
