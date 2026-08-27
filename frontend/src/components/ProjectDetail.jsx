import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, Link, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../api';
import {
    ArrowLeft, Upload, Book, Sparkles, Save, Edit2, Trash2,
    Settings, Check, Folder, Film, Tv, X, Languages, Zap,
    FileText, RefreshCw, RotateCcw, Download, Play, Database, ShieldAlert, Search, FolderOutput,
    ChevronDown, ChevronRight, Globe, Wand2, SpellCheck, LayoutGrid, List, Wrench
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import GlossaryEditor from './GlossaryEditor';
import GlossaryReviewModal from './GlossaryReviewModal';
import ContextReviewModal from './ContextReviewModal';
import ProjectSettingsModal from './ProjectSettingsModal';
import PipelineStepper from './PipelineStepper';
import SimplePipelineWizard from './SimplePipelineWizard';
import TranslationSandbox from './TranslationSandbox';
import TranslationComparisonModal from './TranslationComparisonModal';
import JobProgressWidget from './JobProgressWidget';
import { useJobs } from '../context/JobContext';
import { useToast } from '../context/ToastContext';
import TMStatsPanel from './TMStatsPanel';
import ReviewQueuePanel from './ReviewQueuePanel';
import CharacterProfilesPanel from './CharacterProfilesPanel';
import EpisodeSummariesPanel from './EpisodeSummariesPanel';
import SyncWizard from './SyncWizard';
import EmbeddedTracksModal from './EmbeddedTracksModal';
import SyncOptionsModal from './SyncOptionsModal';

// --- Tab Definitions ---
const TABS = {
    EPISODES: 'episodes',
    GLOSSARY: 'glossary',
    CONTEXT: 'context',
    SUBPROJECTS: 'subprojects',
    SANDBOX: 'sandbox',
    ADVANCED: 'advanced',
    SYNC: 'sync',
};

const ProjectDetail = () => {
    const { projectName } = useParams();
    const { activeJobs, addJob, removeJob, cancelJob } = useJobs();
    const toast = useToast();
    const location = useLocation();
    const navigate = useNavigate();
    const handledJobsRef = useRef(new Set());
    const fileInputRef = useRef(null);
    // Synchronous in-flight latches so a rapid double-click can't enqueue the same
    // job twice in the window before the request resolves and state updates.
    const translateInFlightRef = useRef(false);
    const aiActionInFlightRef = useRef({});

    // Core State
    const [project, setProject] = useState(null);
    const [episodes, setEpisodes] = useState([]);
    const [subprojects, setSubprojects] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [activeTab, setActiveTab] = useState(TABS.EPISODES);
    const [isSimplePipelineActive, setIsSimplePipelineActive] = useState(false);

    // Episode search and filter
    const [episodeSearchQuery, setEpisodeSearchQuery] = useState('');
    const [episodeFilterStatus, setEpisodeFilterStatus] = useState('all'); // all, translated, untranslated, failed
    const [collapsedSeasons, setCollapsedSeasons] = useState(new Set());

    // Selection & UI State
    const [selectedEpisodes, setSelectedEpisodes] = useState(new Set());
    const [viewMode, setViewMode] = useState(() => localStorage.getItem('omnisub_view_mode') || 'cards');
    const lastSelectedEpRef = useRef(null);
    const [isDownloading, setIsDownloading] = useState(false);
    const [isDragging, setIsDragging] = useState(false);
    const [isSettingsOpen, setIsSettingsOpen] = useState(false);
    const [tokenSummary, setTokenSummary] = useState(null);
    const [syncModalOpen, setSyncModalOpen] = useState(false);

    // Editing State
    const [editedContext, setEditedContext] = useState('');
    const [isEditingContext, setIsEditingContext] = useState(false);

    // Modal State
    const [glossaryReviewModal, setGlossaryReviewModal] = useState({ isOpen: false, newTerms: [], existingTerms: [] });
    const [contextReviewModal, setContextReviewModal] = useState({ isOpen: false, newContext: '', currentContext: '' });
    const [comparisonModal, setComparisonModal] = useState({ isOpen: false, episodeName: '', currentLines: [], newTranslations: {} });
    const [translationModal, setTranslationModal] = useState({ isOpen: false, episodeName: null, isBatch: false });
    const [selectedTranslationType, setSelectedTranslationType] = useState('saved');
    const [applyFixesModal, setApplyFixesModal] = useState({ isOpen: false });
    const [applyFixesTrack, setApplyFixesTrack] = useState('both');
    const [checkMissingModal, setCheckMissingModal] = useState({ isOpen: false, results: null, loading: false });
    const [scriptFixModal, setScriptFixModal] = useState({ isOpen: false, results: null, loading: false });
    const [embeddedTracksModal, setEmbeddedTracksModal] = useState({ isOpen: false, episodeName: null });

    // Sync State
    const [syncingProject, setSyncingProject] = useState(false);

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
            const [projRes, allProjRes, tokenRes] = await Promise.all([
                api.getProject(projectName),
                api.getProjects(),
                api.getProjectTokenSummary(projectName)
            ]);
            const projData = projRes.data;
            setProject(projData);
            setEditedContext(projData.context_guide || '');
            setTokenSummary(tokenRes.data);

            if (projData.type === 'parent') {
                setActiveTab(TABS.SUBPROJECTS);
                // `GET /projects` already returns full metadata objects (with .name and
                // .parent_project), so filter them directly. (The previous code passed
                // each object to api.getProject() as if it were a name string, which
                // 404'd for every project and always yielded an empty list.)
                setSubprojects(allProjRes.data.filter(p => p && p.parent_project === projectName));
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
            const newTerms = result?.terms || [];
            setGlossaryReviewModal({ isOpen: true, newTerms, existingTerms });
        } else if (job.type === 'translate_episode') {
            loadData();
            const epName = job.metadata?.episodeName || 'Episode';
            toast.success(`Translation complete for ${epName}`);
        } else if (job.type === 'batch_translate') {
            loadData();
            toast.success('Batch translation complete');
        } else if (job.type === 'translate_missing') {
            loadData();
            toast.success('Missing lines translation repair complete!');
        } else if (job.type === 'apply_fixes') {
            loadData();
            const ok = result?.ok ?? 0;
            const total = result?.total ?? ok;
            toast.success(`SubtitleEdit fixes applied to ${ok}/${total} episode(s)`);
        } else if (job.type === 'project_sync') {
            loadData();
            toast.success(`Sync complete for ${project?.show_name || 'project'}`);
        } else if (job.type === 'pipeline') {
            loadData();
            toast.success('Pipeline complete — all episodes translated');
            setPipelineJobId(null);
        } else if (job.type === 'retranslate_compare') {
            const epName = job.result?.episode_name;
            const newTranslations = job.result?.new_translations;
            if (epName && newTranslations) {
                api.getEpisode(projectName, epName).then(res => {
                    setComparisonModal({
                        isOpen: true,
                        episodeName: epName,
                        currentLines: res.data.data,
                        newTranslations
                    });
                }).catch(err => {
                    console.error("Failed to load episode for comparison", err);
                    toast.error("Failed to load comparison view");
                });
            }
        } else if (job.type === 'batch_extract_embedded') {
            loadData();
            const extracted = result?.extracted ?? 0;
            toast.success(`Embedded ASS extraction complete: ${extracted} episode(s) extracted`);
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
            if ((isHighlighted || isUnhandled) && (job.result || job.type === 'translate_episode' || job.type === 'batch_translate' || job.type === 'translate_missing' || job.type === 'batch_extract_embedded')) {
                handleJobComplete(job);
                handledJobsRef.current.add(job.id);
                try { localStorage.setItem(storageKey, JSON.stringify([...handledJobsRef.current])); } catch { }
            }
        }
    }, [activeJobs, projectName, project, location.state, handleJobComplete]);

    // --- Actions ---
    const handleAIAction = async (type, action) => {
        // Block double-submit: an already-running job for this type (state) or a
        // request still in flight (ref, covers the same-tick window).
        if (aiActionInFlightRef.current[type] || (type === 'context' ? contextJobId : glossaryJobId)) return;
        aiActionInFlightRef.current[type] = true;
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
        } finally {
            aiActionInFlightRef.current[type] = false;
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

    const handleSaveGlossary = async (newGlossary, globalSave = false, suppressedTerms = null) => {
        try {
            const payload = { glossary: newGlossary };
            if (suppressedTerms !== null) {
                payload.suppressed_terms = suppressedTerms;
            }
            const res = await api.updateProject(projectName, payload, globalSave);
            const projRes = await api.getProject(projectName);
            setProject(projRes.data);
            toast.success(globalSave ? 'Glossary updated globally!' : 'Glossary updated locally!');

            if (res.data && res.data.job_id) {
                addJob(res.data.job_id, 'retranslate_invalidated', `Updating scenes with altered glossary terms...`, { link: `/project/${encodeURIComponent(projectName)}`, projectId: projectName });
            }
        } catch (err) {
            console.error('Failed to save glossary', err);
            toast.error('Failed to save glossary');
        }
    };

    const handleUpdateTermsInScenes = async (selectedTermsArray) => {
        try {
            const res = await api.fixConsistency(projectName, selectedTermsArray);
            if (res.data?.job_id) {
                addJob(res.data.job_id, 'retranslate_invalidated', `Updating scenes with specific glossary terms...`, { link: `/project/${encodeURIComponent(projectName)}`, projectId: projectName });
                toast.info(`Updating scenes with selected terms...`);
            }
        } catch (err) {
            console.error('Failed to update scenes', err);
            toast.error('Failed to update scenes');
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

    const handleSaveSettings = async (newSettings, newParentProject) => {
        try {
            const updatePayload = { settings: newSettings };
            if (newParentProject !== undefined) {
                updatePayload.parent_project = newParentProject;
            }
            await api.updateProject(projectName, updatePayload);
            setProject(prev => ({
                ...prev,
                settings: newSettings,
                parent_project: newParentProject !== undefined ? newParentProject : prev.parent_project
            }));
        } catch (err) {
            console.error('Failed to save settings', err);
            toast.error('Failed to save settings');
        }
    };

    // --- Episode Actions ---
    const handleExportSelectedToPath = async () => {
        const names = Array.from(selectedEpisodes);
        if (names.length === 0) return;
        try {
            const res = await api.batchExportToPath(projectName, names);
            const exported = res.data?.exported || [];
            const failed = res.data?.failed || [];
            if (exported.length > 0) {
                toast.success(`Exported ${exported.length} subtitle${exported.length !== 1 ? 's' : ''} to media folders${failed.length ? ` — ${failed.length} skipped` : ''}`);
            } else {
                const reason = failed[0]?.reason ? ` (${failed[0].reason})` : '';
                toast.error(`Nothing exported — ${failed.length} episode${failed.length !== 1 ? 's' : ''} skipped${reason}`);
            }
            loadData();
        } catch (err) {
            console.error('Failed to export selected episodes', err);
            toast.error('Failed to export selected episodes');
        }
    };

    const handleTranslateEpisodeClick = (episodeName) => {
        setTranslationModal({
            isOpen: true,
            episodeName,
            isBatch: false
        });
        setSelectedTranslationType('saved');
    };

    const handleTranslateEpisode = async (episodeName, type = 'saved') => {
        try {
            const model = project.settings?.translation_model || null;
            const res = await api.translateEpisode(projectName, episodeName, model, false, false, type);
            if (res.data.job_id) {
                addJob(res.data.job_id, 'translate_episode', `Translating ${episodeName}`, { projectId: projectName, episodeName });
                toast.info(`Translation enqueued (${type === 'saved' ? 'Saved' : 'Original'})`);
            }
        } catch (err) {
            console.error('Failed to start translation', err);
            toast.error('Failed to start translation');
        }
    };

    const handleConfirmTranslation = () => {
        setTranslationModal(prev => ({ ...prev, isOpen: false }));
        if (translationModal.isBatch) {
            handleBatchTranslate(selectedTranslationType);
        } else {
            handleTranslateEpisode(translationModal.episodeName, selectedTranslationType);
        }
    };

    const handleSyncProject = (options = {}) => {
        const cleanOptions = (options && typeof options === 'object' && !options.nativeEvent && !options._reactName && !options.target)
            ? options
            : {};
        handleStartProjectSync(cleanOptions);
    };

    const handleStartProjectSync = async (options = {}) => {
        const cleanOptions = (options && typeof options === 'object' && !options.nativeEvent && !options._reactName && !options.target)
            ? options
            : {};
        setSyncingProject(true);
        try {
            const res = await api.syncProject(projectName, cleanOptions);
            if (res.data?.job_id) {
                addJob(res.data.job_id, 'project_sync', `Syncing project ${project?.show_name || projectName}`, { projectId: projectName });
                toast.info("Project sync started in the background");
            } else {
                toast.success("Project synced successfully!");
                loadData();
            }
        } catch (err) {
            console.error("Failed to sync project", err);
            toast.error("Failed to sync project");
        } finally {
            setSyncingProject(false);
        }
    };

    const handleCheckMissing = async () => {
        setCheckMissingModal({ isOpen: true, results: null, loading: true });
        try {
            const res = await api.checkProjectMissing(projectName);
            setCheckMissingModal({ isOpen: true, results: res.data, loading: false });
        } catch (err) {
            console.error("Failed to check missing translations", err);
            toast.error("Failed to check missing translations");
            setCheckMissingModal({ isOpen: false, results: null, loading: false });
        }
    };

    const handleFixMissing = async () => {
        setCheckMissingModal(prev => ({ ...prev, isOpen: false }));
        try {
            const res = await api.translateProjectMissing(projectName);
            if (res.data?.job_id) {
                addJob(res.data.job_id, 'translate_missing', `Translating missing lines with context`, { projectId: projectName });
                toast.info("Started translating missing lines in the background...");
            }
        } catch (err) {
            console.error("Failed to translate missing lines", err);
            toast.error("Failed to start translating missing lines");
        }
    };

    const handleScanScriptFix = async () => {
        setScriptFixModal({ isOpen: true, results: null, loading: true });
        try {
            const res = await api.scanScriptGuard(projectName);
            setScriptFixModal({ isOpen: true, results: res.data, loading: false });
        } catch (err) {
            console.error('Failed to scan for wrong-alphabet text', err);
            toast.error(err?.response?.data?.detail || 'Failed to scan translations');
            setScriptFixModal({ isOpen: false, results: null, loading: false });
        }
    };

    const handleRunScriptFix = async (useLlm) => {
        setScriptFixModal(prev => ({ ...prev, isOpen: false }));
        try {
            const res = await api.repairScriptGuard(projectName, { useLlm });
            if (res.data?.job_id) {
                addJob(res.data.job_id, 'script_repair', 'Fixing wrong-alphabet characters', { projectId: projectName });
                toast.info(useLlm ? 'Fixing wrong-alphabet text in batched requests…'
                    : 'Applying free look-alike fixes…');
            }
        } catch (err) {
            console.error('Failed to start script repair', err);
            toast.error(err?.response?.data?.detail || 'Failed to start the fix');
        }
    };

    const handleClearTranslation = async (episodeName) => {
        if (!window.confirm(`Clear translation for ${episodeName}?`)) return;
        try {
            await api.clearTranslation(projectName, episodeName);
            toast.success(`Translation cleared for ${episodeName}`);
            loadData();
        } catch (err) {
            console.error('Failed to clear translation', err);
            toast.error('Failed to clear translation');
        }
    };

    const handleRetranslateFromScratch = async (episodeName) => {
        if (!window.confirm(
            `Retranslate "${episodeName}" from scratch?\n\nThis will erase the existing translation and redo it fully with the current model and settings.`
        )) return;
        try {
            const model = project.settings?.translation_model || null;
            const res = await api.translateEpisode(projectName, episodeName, model, false, true);
            if (res.data.job_id) {
                addJob(res.data.job_id, 'translate_episode', `Retranslating ${episodeName} from scratch`, { projectId: projectName, episodeName });
                toast.info(`Retranslation started for ${episodeName}`);
            }
        } catch (err) {
            console.error('Failed to start force retranslation', err);
            toast.error('Failed to start retranslation');
        }
    };

    const handleRetranslateEpisode = async (episodeName) => {
        try {
            const model = project.settings?.translation_model || null;
            const res = await api.retranslateEpisode(projectName, episodeName, model);
            if (res.data.job_id) {
                addJob(res.data.job_id, 'retranslate_compare', `Retranslating ${episodeName} (Review Mode)`, { projectId: projectName, episodeName });
                toast.info(`Retranslation started for ${episodeName}. You'll be able to review and merge changes once complete.`);
            }
        } catch (err) {
            console.error('Failed to start retranslation', err);
            toast.error('Failed to start retranslation');
        }
    };

    const handleMergeComparison = async (selectedLines) => {
        try {
            await api.mergeTranslation(projectName, comparisonModal.episodeName, selectedLines);
            toast.success(`Merged ${Object.keys(selectedLines).length} lines for ${comparisonModal.episodeName}`);
            loadData();
        } catch (err) {
            console.error('Failed to merge translations', err);
            toast.error('Failed to merge translations');
        }
    };

    // --- Pipeline Actions ---
    const handleStartPipeline = async (mode = 'auto') => {
        try {
            const settings = project.settings || {};

            // Default to 'try to create if missing' (skip=false)
            // The backend smart logic will skip if they already exist.
            const skip_context = false;
            const skip_glossary = false;

            const res = await api.startPipeline(projectName, {
                mode,
                skip_context,
                skip_glossary,
                episode_names: selectedEpisodes.size > 0 ? Array.from(selectedEpisodes) : null,
                translation_model: settings.translation_model || null,
                context_model: settings.context_model || null,
                glossary_model: settings.glossary_model || null,
                model: settings.translation_model || null, // fallback for legacy
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

    const handleStartFullIngest = async () => {
        try {
            const res = await api.startFullIngestPipeline(projectName);
            if (res.data.job_id) {
                addJob(res.data.job_id, 'full_ingest_pipeline', `Full Ingest: ${project?.show_name || projectName}`, { projectId: projectName });
                toast.info('Full Ingest Pipeline started: demuxing ASS, aligning context & queueing translation!');
            }
        } catch (err) {
            console.error('Failed to start full ingest pipeline', err);
            toast.error('Failed to start full ingest pipeline');
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

    const handleBatchTranslateClick = () => {
        if (selectedEpisodes.size === 0) return;
        setTranslationModal({
            isOpen: true,
            episodeName: null,
            isBatch: true
        });
        setSelectedTranslationType('saved');
    };

    const handleBatchTranslate = async (type = 'saved') => {
        if (translateInFlightRef.current) return;  // guard against double-submit
        translateInFlightRef.current = true;
        try {
            const names = Array.from(selectedEpisodes);

            // Pre-flight check
            const estimateRes = await api.estimateBatchTranslate(projectName, names);
            const est = estimateRes.data;

            if (est.exceeds_daily) {
                const proceed = window.confirm(
                    `Warning: This batch will likely exceed your daily API limit.\n\n` +
                    `Estimated Requests: ~${est.total_requests}\n` +
                    `Daily Limit: ${est.daily_limit}\n` +
                    `Current Usage: ${est.current_daily_count}\n\n` +
                    `The job will process as many as possible and then pause. Proceed?`
                );
                if (!proceed) return;
            }

            const model = project.settings?.translation_model || null;
            const res = await api.batchTranslate(projectName, names, model, false, false, type);
            if (res.data.job_id) {
                addJob(res.data.job_id, 'batch_translate', `Batch translating ${names.length} episodes`, { projectId: projectName });
                toast.info(`Batch translation enqueued (${type === 'saved' ? 'Saved' : 'Original'})`);
            }
        } catch (err) {
            console.error('Batch translate failed', err);
            toast.error('Failed to start batch translation');
        } finally {
            translateInFlightRef.current = false;
        }
    };

    const handleAutoTranslate = async () => {
        if (translateInFlightRef.current) return;  // guard against double-submit
        translateInFlightRef.current = true;
        try {
            const episodeNames = selectedEpisodes.size > 0 ? Array.from(selectedEpisodes) : episodes.map(e => e.name);

            // Pre-flight check
            const estimateRes = await api.estimateBatchTranslate(projectName, episodeNames);
            const est = estimateRes.data;

            if (est.exceeds_daily) {
                const proceed = window.confirm(
                    `Warning: This batch will likely exceed your daily API limit.\n\n` +
                    `Estimated Requests: ~${est.total_requests}\n` +
                    `Daily Limit: ${est.daily_limit}\n` +
                    `Current Usage: ${est.current_daily_count}\n\n` +
                    `The job will process as many as possible and then pause. Proceed?`
                );
                if (!proceed) return;
            }

            const res = await api.autoTranslate(projectName, {
                translation_model: project.settings?.translation_model || null,
                context_model: project.settings?.context_model || null,
                glossary_model: project.settings?.glossary_model || null,
                model: project.settings?.translation_model || null, // fallback for legacy
                episode_names: selectedEpisodes.size > 0 ? Array.from(selectedEpisodes) : null,
                skip_context: false, // Smart skip handled by backend
                skip_glossary: false,
                enhance_glossary: true,
            });
            if (res.data.job_id) {
                const label = selectedEpisodes.size > 0
                    ? `Auto-translating ${selectedEpisodes.size} episode(s)`
                    : `Auto-translating all episodes`;
                addJob(res.data.job_id, 'auto_translate', label, { projectId: projectName });
                toast.info('Auto-translate started');
            }
        } catch (err) {
            console.error('Auto-translate failed', err);
            toast.error('Failed to start auto-translate');
        } finally {
            translateInFlightRef.current = false;
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

    const handleExportToPath = async (episodeName) => {
        try {
            await api.exportToPath(projectName, episodeName);
            toast.success(`Exported "${episodeName}" to original media path`);
        } catch (err) {
            const msg = err?.response?.data?.detail || 'Export failed';
            console.error('Export to path failed', err);
            toast.error(msg);
        }
    };

    const handleBatchExportToPath = async () => {
        try {
            const names = selectedEpisodes.size > 0 ? Array.from(selectedEpisodes) : null;
            const res = await api.batchExportToPath(projectName, names);
            const { exported, failed } = res.data;
            if (exported.length > 0) toast.success(`Exported ${exported.length} episode(s) to media path`);
            if (failed.length > 0) toast.warning(`${failed.length} episode(s) could not be exported (no media path or untranslated)`);
        } catch (err) {
            console.error('Batch export to path failed', err);
            toast.error('Batch export failed');
        }
    };

    const handleBatchExtractEmbedded = async () => {
        try {
            const selected = selectedEpisodes.size > 0 ? Array.from(selectedEpisodes) : null;
            const res = await api.batchExtractEmbedded(projectName, { episode_names: selected, force: false, migrate_srt: true });
            if (res.data?.job_id) {
                const countLabel = selected ? `${selected.length} selected` : 'all eligible';
                addJob(res.data.job_id, 'batch_extract_embedded', `Extracting embedded ASS tracks (${countLabel})`, { projectId: projectName });
                toast.info(`Started batch embedded ASS extraction for ${countLabel} episode(s)...`);
            }
        } catch (err) {
            console.error('Failed to start batch extraction', err);
            toast.error(err.response?.data?.detail || 'Failed to start batch extraction');
        }
    };

    const handleConfirmApplyFixes = async () => {
        const names = Array.from(selectedEpisodes);
        if (names.length === 0) return;
        setApplyFixesModal({ isOpen: false });
        try {
            const res = await api.batchApplyCommonFixes(projectName, names, applyFixesTrack);
            if (res.data?.job_id) {
                const trackLabel = applyFixesTrack === 'both' ? 'source + translation'
                    : applyFixesTrack === 'source' ? 'source' : 'translation';
                addJob(res.data.job_id, 'apply_fixes', `Applying SubtitleEdit fixes (${trackLabel}) to ${names.length} episode(s)`, { projectId: projectName });
                toast.info(`Applying SubtitleEdit fixes to ${names.length} episode(s)…`);
                setSelectedEpisodes(new Set());
            }
        } catch (err) {
            console.error('Failed to start batch apply-fixes', err);
            toast.error(err?.response?.data?.detail || 'Failed to apply SubtitleEdit fixes');
        }
    };

    const handleBatchDelete = async () => {
        if (!window.confirm(`Delete the translation for ${selectedEpisodes.size} episode(s)? The original subtitles stay intact; only the translations and their exported files are removed.`)) return;
        try {
            await Promise.all(Array.from(selectedEpisodes).map(name => api.deleteEpisode(projectName, name)));
            setSelectedEpisodes(new Set());
            loadData();
            toast.success('Translations deleted');
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

    // Any in-flight batch/auto/episode translate job for THIS project — used to
    // disable the translate buttons so the same work can't be enqueued twice.
    const hasActiveTranslateJob = Object.values(activeJobs).some(j =>
        ['running', 'pending'].includes(j.status) &&
        ['auto_translate', 'batch_translate', 'translate_episode', 'translate_missing'].includes(j.type) &&
        j.metadata?.projectId === projectName
    );

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
    // Filter episodes based on query and status filter
    const filteredEpisodes = episodes.filter(ep => {
        const nameMatch = ep.name.toLowerCase().includes(episodeSearchQuery.toLowerCase());
        if (!nameMatch) return false;

        if (episodeFilterStatus === 'all') return true;
        const isSynced = !!(ep.metadata?.arr_has_target || ep.metadata?.bazarr_has_target);
        const isAss = ['ass', 'ssa'].includes((ep.metadata?.original_format || ep.metadata?.original_extension || '').toLowerCase());
        if (episodeFilterStatus === 'translated') return ep.translated;
        if (episodeFilterStatus === 'untranslated') return !ep.translated;
        if (episodeFilterStatus === 'ass') return isAss;
        if (episodeFilterStatus === 'srt') return !isAss;
        if (episodeFilterStatus === 'failed') return ep.metadata?.translation_failed;
        if (episodeFilterStatus === 'synced') return isSynced;
        if (episodeFilterStatus === 'unsynced') return !isSynced;
        return true;
    });

    // Group episodes by season (Sonarr-style): Season 1..N first, Specials (S00) last.
    const getEpisodeSeason = (ep) => {
        if (ep.season !== null && ep.season !== undefined) return Number(ep.season);
        const m = /^S(\d+)E\d+/i.exec(ep.name);
        return m ? parseInt(m[1], 10) : null;
    };
    const seasonGroups = (() => {
        const groups = new Map();
        for (const ep of filteredEpisodes) {
            const s = getEpisodeSeason(ep);
            if (!groups.has(s)) groups.set(s, []);
            groups.get(s).push(ep);
        }
        const order = [...groups.keys()].sort((a, b) => {
            if (a === null) return 1;          // unparseable last
            if (b === null) return -1;
            if (a === 0) return 1;             // Specials after regular seasons
            if (b === 0) return -1;
            return a - b;
        });
        return order.map(s => ({ season: s, episodes: groups.get(s) }));
    })();
    const isGroupedBySeason = !isMovie && seasonGroups.some(g => g.season !== null) && seasonGroups.length > 0;
    const seasonLabel = (s) => (s === null ? 'Other' : s === 0 ? 'Specials' : `Season ${s}`);
    const toggleSeasonCollapsed = (s) => setCollapsedSeasons(prev => {
        const next = new Set(prev);
        if (next.has(s)) next.delete(s); else next.add(s);
        return next;
    });
    const handleSelectEpisode = (epName, e = null) => {
        const shiftKey = e?.shiftKey;
        setSelectedEpisodes(prev => {
            const next = new Set(prev);
            if (shiftKey && lastSelectedEpRef.current && lastSelectedEpRef.current !== epName) {
                const epNames = filteredEpisodes.map(ep => ep.name);
                const fromIdx = epNames.indexOf(lastSelectedEpRef.current);
                const toIdx = epNames.indexOf(epName);
                if (fromIdx !== -1 && toIdx !== -1) {
                    const [start, end] = fromIdx < toIdx ? [fromIdx, toIdx] : [toIdx, fromIdx];
                    for (let i = start; i <= end; i++) {
                        next.add(epNames[i]);
                    }
                }
            } else {
                if (next.has(epName)) next.delete(epName);
                else next.add(epName);
            }
            lastSelectedEpRef.current = epName;
            return next;
        });
    };

    const handleSelectAll = () => {
        setSelectedEpisodes(prev => {
            const allSelected = filteredEpisodes.length > 0 && filteredEpisodes.every(ep => prev.has(ep.name));
            if (allSelected) return new Set();
            return new Set(filteredEpisodes.map(ep => ep.name));
        });
    };

    const renderEpisodeTable = (episodeList) => {
        const allSelected = episodeList.length > 0 && episodeList.every(ep => selectedEpisodes.has(ep.name));
        return (
            <div className="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm">
                <table className="w-full text-left text-sm border-collapse">
                    <thead>
                        <tr className="bg-gray-50/80 dark:bg-gray-750/80 border-b border-gray-200 dark:border-gray-700 text-xs font-semibold text-gray-500 uppercase tracking-wider">
                            <th className="py-3 px-4 w-10">
                                <input
                                    type="checkbox"
                                    checked={allSelected}
                                    onChange={() => {
                                        setSelectedEpisodes(prev => {
                                            const next = new Set(prev);
                                            if (allSelected) episodeList.forEach(ep => next.delete(ep.name));
                                            else episodeList.forEach(ep => next.add(ep.name));
                                            return next;
                                        });
                                    }}
                                    className="w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 cursor-pointer"
                                />
                            </th>
                            <th className="py-3 px-4">Episode</th>
                            <th className="py-3 px-4">Format</th>
                            <th className="py-3 px-4">Lines</th>
                            <th className="py-3 px-4">Status</th>
                            <th className="py-3 px-4">Sync</th>
                            <th className="py-3 px-4 text-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100 dark:divide-gray-700/60">
                        {episodeList.map((ep) => {
                            const isSelected = selectedEpisodes.has(ep.name);
                            const isAss = ['ass', 'ssa'].includes((ep.metadata?.original_format || ep.metadata?.original_extension || '').toLowerCase());
                            const isSibling = !!ep.metadata?.arr_secondary_of;
                            const isSynced = !!(ep.metadata?.arr_has_target || ep.metadata?.bazarr_has_target);
                            return (
                                <tr
                                    key={ep.name}
                                    onClick={(e) => {
                                        if (e.target.tagName !== 'BUTTON' && e.target.tagName !== 'A' && e.target.tagName !== 'INPUT' && e.target.tagName !== 'SVG' && e.target.tagName !== 'PATH') {
                                            handleSelectEpisode(ep.name, e);
                                        }
                                    }}
                                    className={`hover:bg-indigo-50/40 dark:hover:bg-indigo-950/20 transition-colors cursor-pointer ${
                                        isSelected ? 'bg-indigo-50/60 dark:bg-indigo-950/30' : ''
                                    }`}
                                >
                                    <td className="py-3 px-4" onClick={(e) => e.stopPropagation()}>
                                        <input
                                            type="checkbox"
                                            checked={isSelected}
                                            onChange={(e) => handleSelectEpisode(ep.name, e.nativeEvent)}
                                            className="w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 cursor-pointer"
                                        />
                                    </td>
                                    <td className="py-3 px-4">
                                        <div className="flex items-center gap-2">
                                            <span className="font-semibold text-gray-900 dark:text-white">{ep.name}</span>
                                            {ep.metadata?.episode_title && (
                                                <span className="text-xs text-gray-400 dark:text-gray-500 truncate max-w-xs" title={ep.metadata.episode_title}>
                                                    {ep.metadata.episode_title}
                                                </span>
                                            )}
                                        </div>
                                    </td>
                                    <td className="py-3 px-4">
                                        <div className="flex items-center gap-1.5 flex-wrap">
                                            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-tight ${
                                                isAss ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300' : 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
                                            }`}>
                                                {isAss ? 'ASS' : 'SRT'}
                                            </span>
                                            {ep.metadata?.embedded_extracted && (
                                                <span className="text-[10px] font-bold text-purple-700 bg-purple-100 dark:bg-purple-900/40 dark:text-purple-300 px-1.5 py-0.5 rounded-full uppercase tracking-tight flex items-center gap-0.5">
                                                    <Film size={9} /> #{ep.metadata?.embedded_track?.index ?? ''}
                                                </span>
                                            )}
                                            {isSibling && (
                                                <span className="text-[9px] font-bold text-indigo-600 bg-indigo-50 dark:bg-indigo-900/30 dark:text-indigo-300 px-1 py-0.2 rounded">
                                                    Sibling
                                                </span>
                                            )}
                                        </div>
                                    </td>
                                    <td className="py-3 px-4 font-mono text-xs text-gray-500 dark:text-gray-400">
                                        {ep.line_count}
                                    </td>
                                    <td className="py-3 px-4">
                                        {ep.translated ? (
                                            <span className="inline-flex items-center gap-1 text-xs font-semibold text-green-700 dark:text-green-400 bg-green-50 dark:bg-green-900/30 px-2 py-0.5 rounded-full">
                                                <Check size={11} /> Done
                                            </span>
                                        ) : ep.metadata?.translation_failed ? (
                                            <span className="inline-flex items-center gap-1 text-xs font-semibold text-rose-600 bg-rose-50 dark:bg-rose-900/30 px-2 py-0.5 rounded-full" title={ep.metadata?.translation_error}>
                                                <ShieldAlert size={11} /> Failed
                                            </span>
                                        ) : (
                                            <span className="inline-flex items-center gap-1 text-xs font-medium text-gray-500 bg-gray-100 dark:bg-gray-700 px-2 py-0.5 rounded-full">
                                                Pending
                                            </span>
                                        )}
                                    </td>
                                    <td className="py-3 px-4">
                                        {isSynced ? (
                                            <span className="inline-flex items-center gap-1 text-[10px] font-bold text-green-700 bg-green-100 dark:bg-green-900/40 dark:text-green-400 px-2 py-0.5 rounded-full uppercase tracking-tight">
                                                <Check size={10} /> Synced
                                            </span>
                                        ) : (
                                            <span className="text-[11px] text-gray-400 dark:text-gray-500">—</span>
                                        )}
                                    </td>
                                    <td className="py-3 px-4 text-right" onClick={(e) => e.stopPropagation()}>
                                        <div className="flex items-center justify-end gap-1.5">
                                            {(ep.metadata?.arr_media_path || ep.metadata?.bazarr_media_path) && (
                                                <button
                                                    onClick={() => setEmbeddedTracksModal({ isOpen: true, episodeName: ep.name })}
                                                    className="p-1 text-purple-600 hover:bg-purple-50 dark:text-purple-400 dark:hover:bg-purple-900/30 rounded-md transition-colors"
                                                    title="Inspect & extract embedded tracks"
                                                >
                                                    <Film size={14} />
                                                </button>
                                            )}
                                            <button
                                                onClick={() => handleTranslateEpisodeClick(ep.name)}
                                                className="px-2 py-1 text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-900/30 rounded-md transition-colors"
                                            >
                                                {ep.translated ? 'Retranslate' : 'Translate'}
                                            </button>
                                            {ep.translated && (ep.metadata?.arr_media_path || ep.metadata?.bazarr_media_path) && (
                                                <button
                                                    onClick={() => handleExportToPath(ep.name)}
                                                    className="p-1 text-emerald-600 hover:bg-emerald-50 dark:text-emerald-400 dark:hover:bg-emerald-900/30 rounded-md transition-colors"
                                                    title="Export subtitle to media directory"
                                                >
                                                    <FolderOutput size={14} />
                                                </button>
                                            )}
                                            <Link
                                                to={`/project/${encodeURIComponent(projectName)}/episode/${encodeURIComponent(ep.name)}`}
                                                className="px-2 py-1 text-xs font-semibold text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-md transition-colors"
                                            >
                                                Edit
                                            </Link>
                                        </div>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        );
    };

    const renderEpisodeRow = (ep) => {
        const isSibling = !!ep.metadata?.arr_secondary_of;
        return (
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
                        onChange={(e) => handleSelectEpisode(ep.name, e.nativeEvent)}
                        className="w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 cursor-pointer"
                    />
                    <div>
                        <div className="flex items-center gap-2">
                            <h3 className="font-semibold text-gray-900 dark:text-white">{ep.name}</h3>
                            {isSibling && ep.metadata?.arr_source_format && (
                                <span className="text-[10px] font-bold text-indigo-700 bg-indigo-50 dark:bg-indigo-900/30 dark:text-indigo-300 px-2 py-0.5 rounded-full uppercase tracking-tight">
                                    {ep.metadata.arr_source_format.toUpperCase()}
                                </span>
                            )}
                            {!isSibling && ['ass', 'ssa'].includes((ep.metadata?.original_format || ep.metadata?.original_extension || '').toLowerCase()) && (
                                <span
                                    className="text-[10px] font-bold text-purple-700 bg-purple-50 dark:bg-purple-900/30 dark:text-purple-300 px-2 py-0.5 rounded-full uppercase tracking-tight"
                                    title="Advanced SubStation Alpha — styles, positioning and karaoke are preserved; SubtitleEdit fixes and line-wrapping are skipped for this format"
                                >
                                    {(ep.metadata?.original_format || ep.metadata?.original_extension).toUpperCase()}
                                </span>
                            )}
                            {!isSibling && ep.metadata?.arr_alt_formats && ep.metadata.arr_alt_formats.length > 0 && (
                                <span className="flex items-center gap-1 text-[10px] font-semibold text-gray-500 dark:text-gray-400 bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded uppercase tracking-tight border border-gray-200 dark:border-gray-600" title="Alternative formats available">
                                    +{ep.metadata.arr_alt_formats.map(f => f.toUpperCase()).join(', +')}
                                </span>
                            )}
                            {ep.metadata?.episode_title && (
                                <span className="text-sm text-gray-500 dark:text-gray-400 truncate max-w-[260px]">{ep.metadata.episode_title}</span>
                            )}
                            {ep.metadata?.embedded_extracted && (
                                <span
                                    className="flex items-center gap-1 text-[10px] font-bold text-purple-700 bg-purple-100 dark:bg-purple-900/40 dark:text-purple-300 px-2 py-0.5 rounded-full uppercase tracking-tight"
                                    title={`Extracted from embedded stream #${ep.metadata?.embedded_track?.index ?? '?'}: ${ep.metadata?.embedded_track?.title || 'Dialogue'}`}
                                >
                                    <Film size={10} /> Muxed #{ep.metadata?.embedded_track?.index ?? ''}
                                </span>
                            )}
                            {ep.translated && (
                                <span className="flex items-center gap-1 text-xs font-medium text-green-600 bg-green-50 dark:bg-green-900/30 dark:text-green-400 px-2 py-0.5 rounded-full">
                                    <Check size={10} /> Translated
                                </span>
                            )}
                            {(ep.metadata?.arr_has_target || ep.metadata?.bazarr_has_target) && (
                                <span className="flex items-center gap-1 text-[10px] font-bold text-green-700 bg-green-100 dark:bg-green-900/40 dark:text-green-400 px-2 py-0.5 rounded-full uppercase tracking-tight">
                                    <Check size={10} /> Synced
                                </span>
                            )}
                            {ep.metadata?.translation_failed && (
                                <span
                                    className="flex items-center gap-1 text-[10px] font-bold text-rose-600 bg-rose-50 dark:bg-rose-900/30 dark:text-rose-400 px-2 py-0.5 rounded-full cursor-help uppercase tracking-tight"
                                    title={ep.metadata?.translation_error || "Translation failed"}
                                >
                                    <ShieldAlert size={10} /> Failed
                                </span>
                            )}
                        </div>
                        <p className="text-xs text-gray-400 mt-0.5">{ep.line_count} lines</p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    {(ep.metadata?.arr_media_path || ep.metadata?.bazarr_media_path) && (
                        <button
                            onClick={() => setEmbeddedTracksModal({ isOpen: true, episodeName: ep.name })}
                            className="p-1.5 text-purple-600 hover:text-purple-700 hover:bg-purple-50 dark:text-purple-400 dark:hover:bg-purple-900/30 rounded-lg transition-colors"
                            title="Inspect & extract embedded subtitle tracks from media container"
                        >
                            <Film size={16} />
                        </button>
                    )}
                    <button
                        onClick={() => handleTranslateEpisodeClick(ep.name)}
                        className="flex items-center gap-1.5 text-gray-500 hover:text-indigo-600 px-3 py-1.5 rounded-lg hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors text-sm font-medium"
                        title="Translate"
                    >
                        <Languages size={16} />
                        <span className="hidden sm:inline">Translate</span>
                    </button>
                    {ep.translated && (
                        <div className="flex items-center gap-1">
                            <button
                                onClick={() => handleRetranslateEpisode(ep.name)}
                                className="p-1.5 text-gray-400 hover:text-purple-600 hover:bg-purple-50 dark:hover:bg-purple-900/20 rounded-lg transition-colors"
                                title="Retranslate & Compare"
                            >
                                <RefreshCw size={16} />
                            </button>
                            <button
                                onClick={() => handleRetranslateFromScratch(ep.name)}
                                className="p-1.5 text-gray-400 hover:text-orange-500 hover:bg-orange-50 dark:hover:bg-orange-900/20 rounded-lg transition-colors"
                                title="Retranslate from Scratch (clears existing translation)"
                            >
                                <RotateCcw size={16} />
                            </button>
                            {(ep.metadata?.arr_media_path || ep.metadata?.bazarr_media_path) && (
                                <button
                                    onClick={() => handleExportToPath(ep.name)}
                                    className="p-1.5 text-gray-400 hover:text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/20 rounded-lg transition-colors"
                                    title="Export to original media path"
                                >
                                    <FolderOutput size={16} />
                                </button>
                            )}
                            <button
                                onClick={() => handleClearTranslation(ep.name)}
                                className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors"
                                title="Clear Translation"
                            >
                                <Trash2 size={16} />
                            </button>
                        </div>
                    )}
                    <Link
                        to={`/project/${encodeURIComponent(projectName)}/episode/${encodeURIComponent(ep.name)}`}
                        className="text-indigo-600 hover:text-indigo-700 font-medium text-sm px-3 py-1.5 rounded-lg hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors"
                    >
                        Edit
                    </Link>
                </div>
            </div>
        );
    };

    const arrangeEpisodes = (list) => {
        if (!list) return { bases: [], siblingsByBase: {}, standaloneSiblings: [] };
        const bases = [];
        const siblingsByBase = {};
        const standaloneSiblings = [];

        const baseNames = new Set();
        for (const ep of list) {
            if (!ep.metadata?.arr_secondary_of) {
                baseNames.add(ep.name);
            }
        }

        for (const ep of list) {
            const parentName = ep.metadata?.arr_secondary_of;
            if (!parentName) {
                bases.push(ep);
            } else {
                if (baseNames.has(parentName)) {
                    if (!siblingsByBase[parentName]) {
                        siblingsByBase[parentName] = [];
                    }
                    siblingsByBase[parentName].push(ep);
                } else {
                    standaloneSiblings.push(ep);
                }
            }
        }

        return { bases, siblingsByBase, standaloneSiblings };
    };

    const renderEpisodesList = (list) => {
        if (!list) return null;
        if (viewMode === 'table') {
            return renderEpisodeTable(list);
        }
        const { bases, siblingsByBase, standaloneSiblings } = arrangeEpisodes(list);

        return (
            <>
                {bases.map(baseEp => (
                    <div key={baseEp.name} className="space-y-2">
                        {renderEpisodeRow(baseEp)}
                        {siblingsByBase[baseEp.name]?.map(sibEp => (
                            <div key={sibEp.name} className="pl-6 border-l-2 border-indigo-100 dark:border-indigo-900/50">
                                {renderEpisodeRow(sibEp)}
                            </div>
                        ))}
                    </div>
                ))}
                {standaloneSiblings.map(sibEp => (
                    <div key={sibEp.name}>
                        {renderEpisodeRow(sibEp)}
                    </div>
                ))}
            </>
        );
    };

    if (!project) return null;

    const tabs = isParent
        ? [
            { id: TABS.SUBPROJECTS, label: 'Subprojects', icon: Folder, count: subprojects.length },
            { id: TABS.GLOSSARY, label: 'Glossary', icon: Book, count: project.glossary?.terms?.length || 0 },
            { id: TABS.CONTEXT, label: 'Context Guide', icon: FileText },
            { id: TABS.SYNC, label: 'Sync Wizard', icon: Globe },
            { id: TABS.ADVANCED, label: 'Advanced', icon: Database },
        ]
        : [
            { id: TABS.EPISODES, label: isMovie ? 'Files' : 'Episodes', icon: Film, count: episodes.length },
            { id: TABS.GLOSSARY, label: 'Glossary', icon: Book, count: project.glossary?.terms?.length || 0 },
            { id: TABS.CONTEXT, label: 'Context Guide', icon: FileText },
            { id: TABS.SANDBOX, label: 'Sandbox', icon: Zap },
            { id: TABS.ADVANCED, label: 'Advanced', icon: Database },
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
                                {(project.arr_total_episodes || project.bazarr_total_episodes) && (
                                    <div className="flex items-center gap-2 mt-1">
                                        <span className="text-[10px] font-bold px-2 py-0.5 bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400 rounded-full flex items-center gap-1 uppercase tracking-tight">
                                            <Check size={10} /> {project.arr_translated_episodes ?? project.bazarr_translated_episodes}/{project.arr_total_episodes ?? project.bazarr_total_episodes} Synced
                                        </span>
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        {!isParent && (
                            <div className="relative group">
                                <button
                                    onClick={handleAutoTranslate}
                                    disabled={isPipelineActive || isSimplePipelineActive || hasActiveTranslateJob || episodes.length === 0}
                                    className="flex items-center gap-2 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-700 hover:to-purple-700 disabled:opacity-50 text-white px-4 py-2.5 rounded-xl text-sm font-bold transition-all shadow-md shadow-indigo-500/20 hover:shadow-indigo-500/30 hover:-translate-y-0.5 active:translate-y-0"
                                    title={hasActiveTranslateJob ? 'A translation job is already running for this project' : (selectedEpisodes.size > 0 ? `Auto-translate ${selectedEpisodes.size} selected episode(s)` : 'Auto-translate all episodes')}
                                >
                                    <Sparkles size={16} />
                                    <span>{selectedEpisodes.size > 0 ? `Translate (${selectedEpisodes.size})` : 'Translate All'}</span>
                                    <ChevronDown size={14} className="opacity-80" />
                                </button>
                                {/* Dropdown for Translate & Pipeline modes */}
                                <div className="absolute right-0 mt-2 w-64 bg-white dark:bg-gray-800 rounded-2xl shadow-2xl border border-gray-100 dark:border-gray-700 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-30 translate-y-1 group-hover:translate-y-0 overflow-hidden">
                                    <div className="px-4 py-2 text-xs font-semibold text-gray-400 uppercase tracking-wider bg-gray-50/50 dark:bg-gray-750/50 border-b border-gray-100 dark:border-gray-700">
                                        Translate & Pipelines
                                    </div>
                                    <button
                                        onClick={handleAutoTranslate}
                                        disabled={isPipelineActive || isSimplePipelineActive || hasActiveTranslateJob || episodes.length === 0}
                                        className="w-full text-left px-4 py-2.5 text-sm hover:bg-indigo-50/50 dark:hover:bg-indigo-950/30 flex items-center gap-2.5 transition-colors disabled:opacity-50"
                                    >
                                        <Zap size={15} className="text-amber-500" />
                                        <div>
                                            <div className="font-semibold text-gray-900 dark:text-gray-100">Auto-Translate (Fast)</div>
                                            <div className="text-[11px] text-gray-400">Direct LLM queue execution</div>
                                        </div>
                                    </button>
                                    <button
                                        onClick={() => setIsSimplePipelineActive(true)}
                                        disabled={isPipelineActive || isSimplePipelineActive}
                                        className="w-full text-left px-4 py-2.5 text-sm hover:bg-indigo-50/50 dark:hover:bg-indigo-950/30 flex items-center gap-2.5 transition-colors disabled:opacity-50"
                                    >
                                        <Sparkles size={15} className="text-indigo-500" />
                                        <div>
                                            <div className="font-semibold text-gray-900 dark:text-gray-100">Guided Pipeline Wizard</div>
                                            <div className="text-[11px] text-gray-400">Review context & glossary first</div>
                                        </div>
                                    </button>
                                    <button
                                        onClick={() => handleStartPipeline('step')}
                                        disabled={isPipelineActive}
                                        className="w-full text-left px-4 py-2.5 text-sm hover:bg-indigo-50/50 dark:hover:bg-indigo-950/30 flex items-center gap-2.5 transition-colors border-t border-gray-100 dark:border-gray-700/60 disabled:opacity-50"
                                    >
                                        <RefreshCw size={15} className="text-purple-500" />
                                        <div>
                                            <div className="font-semibold text-gray-900 dark:text-gray-100">Step-by-Step Pipeline</div>
                                            <div className="text-[11px] text-gray-400">Manual approval per stage</div>
                                        </div>
                                    </button>
                                    <button
                                        onClick={handleStartFullIngest}
                                        disabled={isPipelineActive || isSimplePipelineActive || hasActiveTranslateJob || episodes.length === 0}
                                        className="w-full text-left px-4 py-2.5 text-sm hover:bg-indigo-50/50 dark:hover:bg-indigo-950/30 flex items-center gap-2.5 transition-colors border-t border-gray-100 dark:border-gray-700/60 disabled:opacity-50"
                                    >
                                        <Zap size={15} className="text-amber-500" />
                                        <div>
                                            <div className="font-semibold text-gray-900 dark:text-gray-100">Full Ingest Pipeline (1-Click)</div>
                                            <div className="text-[11px] text-gray-400">Demux ASS → Context → Glossary → Translate</div>
                                        </div>
                                    </button>
                                    <button
                                        onClick={() => setActiveTab(TABS.SANDBOX)}
                                        className="w-full text-left px-4 py-2.5 text-sm hover:bg-indigo-50/50 dark:hover:bg-indigo-950/30 flex items-center gap-2.5 transition-colors border-t border-gray-100 dark:border-gray-700/60"
                                    >
                                        <Play size={15} className="text-emerald-500" />
                                        <div>
                                            <div className="font-semibold text-gray-900 dark:text-gray-100">Translation Sandbox</div>
                                            <div className="text-[11px] text-gray-400">Test prompt & models live</div>
                                        </div>
                                    </button>
                                </div>
                            </div>
                        )}

                        {/* Media & Sync Hub */}
                        <div className="relative group">
                            <button
                                className="flex items-center gap-1.5 text-gray-700 dark:text-gray-200 hover:text-indigo-600 dark:hover:text-indigo-400 px-3 py-2.5 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700/60 transition-all font-medium text-sm shadow-sm"
                                title="Sync with Sonarr/Radarr/Bazarr and media tools"
                            >
                                <Film size={16} className="text-purple-500" />
                                <span className="hidden sm:inline">Media & Sync</span>
                                <ChevronDown size={14} className="opacity-70" />
                            </button>
                            <div className="absolute right-0 mt-2 w-60 bg-white dark:bg-gray-800 rounded-2xl shadow-2xl border border-gray-100 dark:border-gray-700 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-30 translate-y-1 group-hover:translate-y-0 overflow-hidden">
                                <div className="px-4 py-2 text-xs font-semibold text-gray-400 uppercase tracking-wider bg-gray-50/50 dark:bg-gray-750/50 border-b border-gray-100 dark:border-gray-700">
                                    Media Sync Hub
                                </div>
                                {(project.arr_source || project.bazarr_source) && (
                                    <button
                                        onClick={() => handleSyncProject({ extract_embedded_ass: true, scan_ass: true })}
                                        disabled={syncingProject}
                                        className="w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50 dark:hover:bg-gray-700/70 flex items-center gap-2.5 transition-colors disabled:opacity-50"
                                    >
                                        <RefreshCw size={15} className={`text-blue-500 ${syncingProject ? 'animate-spin' : ''}`} />
                                        <div>
                                            <div className="font-semibold text-gray-900 dark:text-gray-100">Sync with {isMovie ? 'Radarr' : 'Sonarr'}/Bazarr</div>
                                            <div className="text-[11px] text-gray-400">Import new episodes and extract embedded subtitles</div>
                                        </div>
                                    </button>
                                )}
                                <button
                                    onClick={handleBatchExtractEmbedded}
                                    className="w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50 dark:hover:bg-gray-700/70 flex items-center gap-2.5 transition-colors border-t border-gray-100 dark:border-gray-700/60"
                                >
                                    <Film size={15} className="text-purple-500" />
                                    <div>
                                        <div className="font-semibold text-gray-900 dark:text-gray-100">Extract Embedded Subtitles</div>
                                        <div className="text-[11px] text-gray-400">Probe and demux video containers (.ass & .srt)</div>
                                    </div>
                                </button>
                                <button
                                    onClick={handleExportSelectedToPath}
                                    disabled={selectedEpisodes.size === 0 && !episodes.some(e => e.translated)}
                                    className="w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50 dark:hover:bg-gray-700/70 flex items-center gap-2.5 transition-colors border-t border-gray-100 dark:border-gray-700/60 disabled:opacity-50"
                                >
                                    <FolderOutput size={15} className="text-emerald-500" />
                                    <div>
                                        <div className="font-semibold text-gray-900 dark:text-gray-100">Export to Media Disk</div>
                                        <div className="text-[11px] text-gray-400">Save sidecar subtitles to files</div>
                                    </div>
                                </button>
                            </div>
                        </div>

                        {/* Quality & Tools Hub */}
                        <div className="relative group">
                            <button
                                className="flex items-center gap-1.5 text-gray-700 dark:text-gray-200 hover:text-indigo-600 dark:hover:text-indigo-400 px-3 py-2.5 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700/60 transition-all font-medium text-sm shadow-sm"
                                title="Quality assurance, text sanitization, and SubtitleEdit fixes"
                            >
                                <Wrench size={16} className="text-amber-500" />
                                <span className="hidden sm:inline">Quality & Tools</span>
                                <ChevronDown size={14} className="opacity-70" />
                            </button>
                            <div className="absolute right-0 mt-2 w-64 bg-white dark:bg-gray-800 rounded-2xl shadow-2xl border border-gray-100 dark:border-gray-700 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-30 translate-y-1 group-hover:translate-y-0 overflow-hidden">
                                <div className="px-4 py-2 text-xs font-semibold text-gray-400 uppercase tracking-wider bg-gray-50/50 dark:bg-gray-750/50 border-b border-gray-100 dark:border-gray-700">
                                    Quality Control
                                </div>
                                <button
                                    onClick={handleCheckMissing}
                                    className="w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50 dark:hover:bg-gray-700/70 flex items-center gap-2.5 transition-colors"
                                >
                                    <Search size={15} className="text-indigo-500" />
                                    <div>
                                        <div className="font-semibold text-gray-900 dark:text-gray-100">Check Missing Line Gaps</div>
                                        <div className="text-[11px] text-gray-400">Audit untranslated dialogue cues</div>
                                    </div>
                                </button>
                                <button
                                    onClick={handleScanScriptFix}
                                    className="w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50 dark:hover:bg-gray-700/70 flex items-center gap-2.5 transition-colors border-t border-gray-100 dark:border-gray-700/60"
                                >
                                    <SpellCheck size={15} className="text-rose-500" />
                                    <div>
                                        <div className="font-semibold text-gray-900 dark:text-gray-100">Sanitize Script Characters</div>
                                        <div className="text-[11px] text-gray-400">Fix Greek/Latin homoglyphs</div>
                                    </div>
                                </button>
                                <button
                                    onClick={() => setApplyFixesModal({ isOpen: true })}
                                    className="w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50 dark:hover:bg-gray-700/70 flex items-center gap-2.5 transition-colors border-t border-gray-100 dark:border-gray-700/60"
                                >
                                    <Wand2 size={15} className="text-purple-500" />
                                    <div>
                                        <div className="font-semibold text-gray-900 dark:text-gray-100">SubtitleEdit Auto-Fixes</div>
                                        <div className="text-[11px] text-gray-400">Run common fixes & line-splits</div>
                                    </div>
                                </button>
                                <button
                                    onClick={() => setSyncModalOpen(true)}
                                    className="w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50 dark:hover:bg-gray-700/70 flex items-center gap-2.5 transition-colors border-t border-gray-100 dark:border-gray-700/60"
                                >
                                    <RefreshCw size={15} className="text-blue-500" />
                                    <div>
                                        <div className="font-semibold text-gray-900 dark:text-gray-100">Sync with Media Files</div>
                                        <div className="text-[11px] text-gray-400">Scan for new episodes & subtitles</div>
                                    </div>
                                </button>
                            </div>
                        </div>

                        {/* Settings Button */}
                        <button
                            onClick={() => setIsSettingsOpen(true)}
                            className="p-2.5 text-gray-500 hover:text-indigo-600 dark:text-gray-400 dark:hover:text-indigo-400 border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700/60 rounded-xl transition-all shadow-sm"
                            title="Project Settings"
                        >
                            <Settings size={20} />
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
                                    <input ref={fileInputRef} type="file" accept=".srt,.ass,.ssa" multiple onChange={(e) => handleFiles(e.target.files)} className="hidden" />
                                    <Upload size={24} className={`mx-auto mb-2 ${isDragging ? 'text-indigo-500' : 'text-gray-400'}`} />
                                    <p className={`text-sm font-medium ${isDragging ? 'text-indigo-600' : 'text-gray-500 dark:text-gray-400'}`}>
                                        {isDragging ? 'Drop files here' : `Drop subtitle files (.srt, .ass, .ssa) here or click to upload`}
                                    </p>
                                </div>

                                {episodes.length === 0 ? (
                                    <div className="text-center py-16 text-gray-400">
                                        <Film size={48} className="mx-auto mb-4 opacity-50" />
                                        <p className="text-lg font-medium text-gray-500 dark:text-gray-400">No episodes yet</p>
                                        <p className="text-sm mt-1">Upload subtitle files to get started</p>
                                    </div>
                                ) : (
                                    <>
                                        {/* Search, Filter & View Switcher Bar */}
                                        <div className="flex flex-col lg:flex-row items-stretch lg:items-center gap-3 mb-4">
                                            <div className="relative flex-1 min-w-[200px]">
                                                <Search className="absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
                                                <input
                                                    type="text"
                                                    placeholder="Search episodes by name or title..."
                                                    value={episodeSearchQuery}
                                                    onChange={(e) => setEpisodeSearchQuery(e.target.value)}
                                                    className="w-full pl-9 pr-4 py-2 text-sm bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500"
                                                />
                                            </div>
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <div className="flex gap-1 bg-gray-100/80 dark:bg-gray-800/90 p-1 rounded-xl border border-gray-200 dark:border-gray-700">
                                                    {[
                                                        { id: 'all', label: 'All' },
                                                        { id: 'untranslated', label: 'Pending' },
                                                        { id: 'translated', label: 'Translated' },
                                                        { id: 'ass', label: 'ASS' },
                                                        { id: 'srt', label: 'SRT' },
                                                        { id: 'synced', label: 'Synced' },
                                                        { id: 'failed', label: 'Failed' },
                                                    ].map(btn => (
                                                        <button
                                                            key={btn.id}
                                                            onClick={() => setEpisodeFilterStatus(btn.id)}
                                                            className={`px-2.5 py-1 text-xs font-semibold rounded-lg transition-all ${episodeFilterStatus === btn.id
                                                                    ? 'bg-indigo-600 text-white shadow-sm'
                                                                    : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
                                                                }`}
                                                        >
                                                            {btn.label}
                                                        </button>
                                                    ))}
                                                </div>

                                                {/* Card / Table View Toggle */}
                                                <div className="flex items-center bg-gray-100/80 dark:bg-gray-800/90 p-1 rounded-xl border border-gray-200 dark:border-gray-700">
                                                    <button
                                                        onClick={() => { setViewMode('cards'); localStorage.setItem('omnisub_view_mode', 'cards'); }}
                                                        className={`p-1.5 rounded-lg transition-all ${viewMode === 'cards' ? 'bg-white dark:bg-gray-700 text-indigo-600 dark:text-indigo-400 shadow-sm' : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'}`}
                                                        title="Card View"
                                                    >
                                                        <LayoutGrid size={15} />
                                                    </button>
                                                    <button
                                                        onClick={() => { setViewMode('table'); localStorage.setItem('omnisub_view_mode', 'table'); }}
                                                        className={`p-1.5 rounded-lg transition-all ${viewMode === 'table' ? 'bg-white dark:bg-gray-700 text-indigo-600 dark:text-indigo-400 shadow-sm' : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'}`}
                                                        title="Dense Table View"
                                                    >
                                                        <List size={15} />
                                                    </button>
                                                </div>
                                            </div>
                                        </div>

                                        {/* Select All + selection actions */}
                                        <div className="flex flex-wrap items-center gap-2 mb-3 px-1 min-h-[34px]">
                                            <input
                                                type="checkbox"
                                                checked={filteredEpisodes.length > 0 && filteredEpisodes.every(ep => selectedEpisodes.has(ep.name))}
                                                onChange={handleSelectAll}
                                                className="w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                                            />
                                            <span className="text-sm text-gray-500">Select All ({filteredEpisodes.length})</span>
                                            {selectedEpisodes.size > 0 && (
                                                <div className="flex items-center gap-2 ml-auto">
                                                    <span className="text-xs font-semibold text-indigo-600">{selectedEpisodes.size} selected</span>
                                                    <button
                                                        onClick={() => { setSelectedTranslationType('saved'); setTranslationModal({ isOpen: true, episodeName: null, isBatch: true }); }}
                                                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-indigo-700 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 dark:bg-indigo-900/30 dark:text-indigo-300 dark:border-indigo-800 rounded-lg transition-colors"
                                                        title="Translate the selected episodes"
                                                    >
                                                        <Languages size={13} />
                                                        Translate
                                                    </button>
                                                    <button
                                                        onClick={handleExportSelectedToPath}
                                                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-emerald-700 bg-emerald-50 hover:bg-emerald-100 border border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-800 rounded-lg transition-colors"
                                                        title="Write translated subtitles next to their media files"
                                                    >
                                                        <FolderOutput size={13} />
                                                        Export to Disk
                                                    </button>
                                                    <button
                                                        onClick={handleBatchExtractEmbedded}
                                                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-purple-700 bg-purple-50 hover:bg-purple-100 border border-purple-200 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-800 rounded-lg transition-colors"
                                                        title="Scan video files and extract embedded ASS tracks for selected episodes"
                                                    >
                                                        <Film size={13} />
                                                        Extract ASS
                                                    </button>
                                                    <button
                                                        onClick={() => setSelectedEpisodes(new Set())}
                                                        className="px-3 py-1.5 text-xs font-medium text-gray-500 hover:text-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
                                                    >
                                                        Clear
                                                    </button>
                                                </div>
                                            )}
                                        </div>

                                        {filteredEpisodes.length === 0 ? (
                                            <div className="text-center py-12 text-gray-400 border border-dashed border-gray-200 dark:border-gray-700 rounded-xl">
                                                <Search size={36} className="mx-auto mb-3 opacity-30" />
                                                <p className="font-medium text-gray-500 dark:text-gray-400">No matching episodes</p>
                                                <p className="text-xs mt-1">Try adjusting your filters or search query</p>
                                            </div>
                                        ) : isGroupedBySeason ? (
                                            /* Episode List grouped by season (Sonarr-style) */
                                            <div className="space-y-4">
                                                {seasonGroups.map(group => {
                                                    const collapsed = collapsedSeasons.has(group.season);
                                                    const allSelected = group.episodes.length > 0 && group.episodes.every(ep => selectedEpisodes.has(ep.name));
                                                    const translatedCount = group.episodes.filter(ep => ep.translated).length;
                                                    const syncedCount = group.episodes.filter(ep => ep.metadata?.arr_has_target || ep.metadata?.bazarr_has_target).length;
                                                    return (
                                                        <div key={String(group.season)}>
                                                            <div className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 dark:bg-gray-800/70 border border-gray-200 dark:border-gray-700 rounded-xl">
                                                                <input
                                                                    type="checkbox"
                                                                    checked={allSelected}
                                                                    onChange={() => handleSelectSeason(group)}
                                                                    onClick={(e) => e.stopPropagation()}
                                                                    className="w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                                                                    title="Select season"
                                                                />
                                                                <button
                                                                    onClick={() => toggleSeasonCollapsed(group.season)}
                                                                    className="flex items-center gap-2 flex-1 text-left"
                                                                >
                                                                    {collapsed ? <ChevronRight size={16} className="text-gray-400" /> : <ChevronDown size={16} className="text-gray-400" />}
                                                                    <span className="font-semibold text-gray-900 dark:text-white">{seasonLabel(group.season)}</span>
                                                                    <span className="text-xs text-gray-500 dark:text-gray-400">
                                                                        {group.episodes.length} episode{group.episodes.length !== 1 ? 's' : ''}
                                                                    </span>
                                                                </button>
                                                                <div className="flex items-center gap-2">
                                                                    {translatedCount > 0 && (
                                                                        <span className="text-xs font-medium text-green-600 bg-green-50 dark:bg-green-900/30 dark:text-green-400 px-2 py-0.5 rounded-full">
                                                                            {translatedCount}/{group.episodes.length} translated
                                                                        </span>
                                                                    )}
                                                                    {syncedCount > 0 && (
                                                                        <span className="text-[10px] font-bold text-green-700 bg-green-100 dark:bg-green-900/40 dark:text-green-400 px-2 py-0.5 rounded-full uppercase tracking-tight">
                                                                            {syncedCount} synced
                                                                        </span>
                                                                    )}
                                                                </div>
                                                            </div>
                                                            {!collapsed && (
                                                                <div className="space-y-2 mt-2">
                                                                    {renderEpisodesList(group.episodes)}
                                                                </div>
                                                            )}
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        ) : (
                                            /* Flat Episode List (movies / unparseable names) */
                                            <div className="space-y-2">
                                                {renderEpisodesList(filteredEpisodes)}
                                            </div>
                                        )}
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
                                    onUpdateTermsInScenes={handleUpdateTermsInScenes}
                                    projectName={projectName}
                                    parentProject={project.parent_project}
                                    suppressedTerms={project.suppressed_terms || []}
                                />
                            </motion.div>
                        )}

                        {/* ======= CONTEXT GUIDE TAB ======= */}
                        {activeTab === TABS.CONTEXT && (
                            <motion.div key="context" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.15 }}>
                                {/* Action Bar */}
                                <div className="flex items-center justify-between mb-4">
                                    <div className="flex items-center gap-2">
                                        <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Context Guide</h2>
                                        {tokenSummary && (
                                            <span className="px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 text-[10px] font-bold uppercase tracking-wider">
                                                Est. {tokenSummary.context_tokens} Tokens
                                            </span>
                                        )}
                                    </div>
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

                        {/* ======= SANDBOX TAB ======= */}
                        {activeTab === TABS.SANDBOX && (
                            <motion.div key="sandbox" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.15 }}>
                                <TranslationSandbox
                                    project={project}
                                    projectName={projectName}
                                    onSettingsChange={(updatedSettings) => {
                                        setProject(prev => ({ ...prev, settings: { ...(prev.settings || {}), ...updatedSettings } }));
                                    }}
                                />
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

                        {/* ======= ADVANCED TAB ======= */}
                        {activeTab === TABS.ADVANCED && (
                            <motion.div key="advanced" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.15 }}>
                                <div className="space-y-6">
                                    <TMStatsPanel projectName={projectName} />
                                    <ReviewQueuePanel projectName={projectName} />
                                    <CharacterProfilesPanel projectName={projectName} projectSettings={project.settings} />
                                    <EpisodeSummariesPanel projectName={projectName} projectSettings={project.settings} />
                                </div>
                            </motion.div>
                        )}

                        {/* ======= SYNC TAB ======= */}
                        {activeTab === TABS.SYNC && (
                            <motion.div key="sync" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.15 }}>
                                <SyncWizard projectName={projectName} onImported={loadData} />
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
                            <button onClick={handleBatchTranslateClick} disabled={hasActiveTranslateJob} className="p-2 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 text-indigo-600 rounded-full transition-colors disabled:opacity-50" title={hasActiveTranslateJob ? 'A translation job is already running for this project' : 'Translate Selected'}>
                                <Languages size={20} />
                            </button>
                            <button onClick={handleBatchDownload} disabled={isDownloading} className="p-2 hover:bg-blue-50 dark:hover:bg-blue-900/20 text-blue-600 rounded-full transition-colors disabled:opacity-50" title="Download Selected (ZIP)">
                                <Download size={20} />
                            </button>
                            <button onClick={handleBatchExportToPath} className="p-2 hover:bg-emerald-50 dark:hover:bg-emerald-900/20 text-emerald-600 rounded-full transition-colors" title="Export Selected to Original Path">
                                <FolderOutput size={20} />
                            </button>
                            <button onClick={handleBatchExtractEmbedded} className="p-2 hover:bg-purple-50 dark:hover:bg-purple-900/20 text-purple-600 rounded-full transition-colors" title="Extract Embedded ASS from Media for Selected Episodes">
                                <Film size={20} />
                            </button>
                            <button onClick={() => setApplyFixesModal({ isOpen: true })} className="p-2 hover:bg-teal-50 dark:hover:bg-teal-900/20 text-teal-600 rounded-full transition-colors" title="Apply SubtitleEdit common fixes (source / translation / both)">
                                <Wand2 size={20} />
                            </button>
                            <div className="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-1" />
                            <button onClick={handleBatchDelete} className="p-2 hover:bg-red-50 dark:hover:bg-red-900/20 text-red-600 rounded-full transition-colors" title="Delete translation (keeps original)">
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
                        onConfirm={(addedTerms, updatedTerms = []) => {
                            let existing = project.glossary?.terms || [];
                            if (updatedTerms.length > 0) {
                                const updatedNames = new Set(updatedTerms.map(t => (t.original_term_name || t.term || '').toLowerCase()));
                                existing = existing.filter(t => !updatedNames.has((t.term || '').toLowerCase()));
                            }
                            // Drop the synthetic merge marker so it isn't persisted into the glossary.
                            const cleanedUpdated = updatedTerms.map(({ original_term_name, ...rest }) => rest);
                            handleSaveGlossary({ terms: [...existing, ...cleanedUpdated, ...addedTerms] });
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
                        project={project}
                        onSave={handleSaveSettings}
                        tokenSummary={tokenSummary}
                    />
                )}
                {translationModal.isOpen && (
                    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                        {/* Backdrop */}
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            onClick={() => setTranslationModal(prev => ({ ...prev, isOpen: false }))}
                            className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm"
                        />

                        {/* Modal Box */}
                        <motion.div
                            initial={{ opacity: 0, scale: 0.95, y: 10 }}
                            animate={{ opacity: 1, scale: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.95, y: 10 }}
                            transition={{ type: 'spring', duration: 0.3 }}
                            className="relative bg-white dark:bg-gray-800 rounded-3xl shadow-2xl border border-gray-100 dark:border-gray-700 max-w-lg w-full overflow-hidden z-10 animate-fade-in"
                        >
                            {/* Header */}
                            <div className="px-6 py-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-850/50">
                                <h3 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                    <Languages className="text-indigo-600 dark:text-indigo-400" />
                                    {translationModal.isBatch
                                        ? `Batch Translate Subtitles`
                                        : `Translate "${translationModal.episodeName}"`
                                    }
                                </h3>
                                <button
                                    onClick={() => setTranslationModal(prev => ({ ...prev, isOpen: false }))}
                                    className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-750 transition-colors"
                                >
                                    <X size={18} />
                                </button>
                            </div>

                            {/* Body */}
                            <div className="p-6 space-y-4">
                                <p className="text-sm text-gray-500 dark:text-gray-400">
                                    Choose how you would like to translate the selected subtitles:
                                </p>

                                <div className="space-y-3">
                                    {/* Option 1: Saved Translation */}
                                    <label
                                        className={`flex items-start gap-4 p-4 rounded-2xl border cursor-pointer transition-all ${selectedTranslationType === 'saved'
                                                ? 'border-indigo-600 bg-indigo-50/30 dark:border-indigo-500 dark:bg-indigo-950/10'
                                                : 'border-gray-200 hover:border-gray-300 dark:border-gray-700 dark:hover:border-gray-600'
                                            }`}
                                    >
                                        <input
                                            type="radio"
                                            name="translationType"
                                            value="saved"
                                            checked={selectedTranslationType === 'saved'}
                                            onChange={() => setSelectedTranslationType('saved')}
                                            className="mt-1 h-4 w-4 text-indigo-600 border-gray-300 focus:ring-indigo-500"
                                        />
                                        <div>
                                            <span className="block font-bold text-gray-950 dark:text-white text-sm">
                                                Saved Translation (Recommended)
                                            </span>
                                            <span className="block text-xs text-gray-500 dark:text-gray-400 mt-1">
                                                Reuses existing translations saved in Omnisub's database. If subtitle files on disk were deleted or missing, this quickly re-exports them in seconds without API costs.
                                            </span>
                                        </div>
                                    </label>

                                    {/* Option 2: Original Translation */}
                                    <label
                                        className={`flex items-start gap-4 p-4 rounded-2xl border cursor-pointer transition-all ${selectedTranslationType === 'original'
                                                ? 'border-indigo-600 bg-indigo-50/30 dark:border-indigo-500 dark:bg-indigo-950/10'
                                                : 'border-gray-200 hover:border-gray-300 dark:border-gray-700 dark:hover:border-gray-600'
                                            }`}
                                    >
                                        <input
                                            type="radio"
                                            name="translationType"
                                            value="original"
                                            checked={selectedTranslationType === 'original'}
                                            onChange={() => setSelectedTranslationType('original')}
                                            className="mt-1 h-4 w-4 text-indigo-600 border-gray-300 focus:ring-indigo-500"
                                        />
                                        <div>
                                            <span className="block font-bold text-gray-950 dark:text-white text-sm">
                                                Original Translation (From Scratch)
                                            </span>
                                            <span className="block text-xs text-gray-500 dark:text-gray-400 mt-1">
                                                Translates the subtitles anew using the LLM. This ignores any cached translations, clears existing text and checkpoints, and runs the full API translation pipeline.
                                            </span>
                                        </div>
                                    </label>
                                </div>
                            </div>

                            {/* Footer */}
                            <div className="px-6 py-4 bg-gray-50/50 dark:bg-gray-805/50 border-t border-gray-100 dark:border-gray-700 flex justify-end gap-3">
                                <button
                                    onClick={() => setTranslationModal(prev => ({ ...prev, isOpen: false }))}
                                    className="px-4 py-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 rounded-xl text-sm font-medium text-gray-700 dark:text-gray-300 transition-colors"
                                >
                                    Cancel
                                </button>
                                <button
                                    onClick={handleConfirmTranslation}
                                    className="px-5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-sm font-bold shadow-lg shadow-indigo-600/20 hover:shadow-indigo-600/30 transition-all"
                                >
                                    Start Translation
                                </button>
                            </div>
                        </motion.div>
                    </div>
                )}
                {applyFixesModal.isOpen && (
                    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            onClick={() => setApplyFixesModal({ isOpen: false })}
                            className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm"
                        />
                        <motion.div
                            initial={{ opacity: 0, scale: 0.95, y: 10 }}
                            animate={{ opacity: 1, scale: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.95, y: 10 }}
                            transition={{ type: 'spring', duration: 0.3 }}
                            className="relative bg-white dark:bg-gray-800 rounded-3xl shadow-2xl border border-gray-100 dark:border-gray-700 max-w-lg w-full overflow-hidden z-10"
                        >
                            <div className="px-6 py-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-850/50">
                                <h3 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                    <Wand2 className="text-teal-600 dark:text-teal-400" />
                                    Apply SubtitleEdit Fixes ({selectedEpisodes.size})
                                </h3>
                                <button
                                    onClick={() => setApplyFixesModal({ isOpen: false })}
                                    className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-750 transition-colors"
                                >
                                    <X size={18} />
                                </button>
                            </div>

                            <div className="p-6 space-y-4">
                                <p className="text-sm text-gray-500 dark:text-gray-400">
                                    Run “Fix common errors” + “Split long lines” on the selected episodes. This may change
                                    the line count (long lines get split) and the result is re-read into Omnisub.
                                </p>
                                <div className="space-y-3">
                                    {[
                                        { id: 'source', title: `Source (${project?.source_language || 'English'})`, desc: 'Clean the imported subtitles. Best before translating. Existing translations are kept for unchanged lines; split-off lines are flagged for review.' },
                                        { id: 'target', title: `Translation (${project?.target_language || 'target'})`, desc: 'Clean the translated subtitles and re-export. Best as a final polish. Episodes with no translation yet are skipped.' },
                                        { id: 'both', title: 'Both', desc: 'Apply to the source first, then the translation.' },
                                    ].map(opt => (
                                        <label
                                            key={opt.id}
                                            className={`flex items-start gap-4 p-4 rounded-2xl border cursor-pointer transition-all ${applyFixesTrack === opt.id
                                                    ? 'border-teal-600 bg-teal-50/30 dark:border-teal-500 dark:bg-teal-950/10'
                                                    : 'border-gray-200 hover:border-gray-300 dark:border-gray-700 dark:hover:border-gray-600'
                                                }`}
                                        >
                                            <input
                                                type="radio"
                                                name="applyFixesTrack"
                                                value={opt.id}
                                                checked={applyFixesTrack === opt.id}
                                                onChange={() => setApplyFixesTrack(opt.id)}
                                                className="mt-1 h-4 w-4 text-teal-600 border-gray-300 focus:ring-teal-500"
                                            />
                                            <div>
                                                <span className="block font-bold text-gray-950 dark:text-white text-sm">{opt.title}</span>
                                                <span className="block text-xs text-gray-500 dark:text-gray-400 mt-1">{opt.desc}</span>
                                            </div>
                                        </label>
                                    ))}
                                </div>
                                <p className="text-xs text-gray-400 dark:text-gray-500">
                                    Requires SubtitleEdit to be configured in Settings.
                                </p>
                            </div>

                            <div className="px-6 py-4 bg-gray-50/50 dark:bg-gray-805/50 border-t border-gray-100 dark:border-gray-700 flex justify-end gap-3">
                                <button
                                    onClick={() => setApplyFixesModal({ isOpen: false })}
                                    className="px-4 py-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 rounded-xl text-sm font-medium text-gray-700 dark:text-gray-300 transition-colors"
                                >
                                    Cancel
                                </button>
                                <button
                                    onClick={handleConfirmApplyFixes}
                                    className="px-5 py-2 bg-teal-600 hover:bg-teal-700 text-white rounded-xl text-sm font-bold shadow-lg shadow-teal-600/20 hover:shadow-teal-600/30 transition-all"
                                >
                                    Apply Fixes
                                </button>
                            </div>
                        </motion.div>
                    </div>
                )}
                {checkMissingModal.isOpen && (
                    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            onClick={() => setCheckMissingModal({ isOpen: false, results: null, loading: false })}
                            className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm"
                        />
                        <motion.div
                            initial={{ opacity: 0, scale: 0.95, y: 10 }}
                            animate={{ opacity: 1, scale: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.95, y: 10 }}
                            transition={{ type: 'spring', duration: 0.3 }}
                            className="relative bg-white dark:bg-gray-800 rounded-3xl shadow-2xl border border-gray-150 dark:border-gray-700 max-w-2xl w-full overflow-hidden z-10 flex flex-col max-h-[85vh]"
                        >
                            <div className="px-6 py-4 border-b border-gray-150 dark:border-gray-750 flex justify-between items-center bg-gray-50/50 dark:bg-gray-850/50">
                                <h3 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                    <Search className="text-indigo-600 dark:text-indigo-400" />
                                    Project Gaps / Missing Translations
                                </h3>
                                <button
                                    onClick={() => setCheckMissingModal({ isOpen: false, results: null, loading: false })}
                                    className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-750 transition-colors"
                                >
                                    <X size={18} />
                                </button>
                            </div>

                            <div className="p-6 overflow-y-auto flex-1 space-y-4">
                                {checkMissingModal.loading ? (
                                    <div className="flex flex-col items-center justify-center py-12 space-y-3">
                                        <RefreshCw className="w-8 h-8 text-indigo-600 dark:text-indigo-400 animate-spin" />
                                        <p className="text-sm font-medium text-gray-500 dark:text-gray-400">Scanning project episodes for missing translations...</p>
                                    </div>
                                ) : checkMissingModal.results ? (
                                    checkMissingModal.results.total_missing === 0 ? (
                                        <div className="flex flex-col items-center justify-center py-10 text-center space-y-2">
                                            <div className="w-12 h-12 bg-green-50 dark:bg-green-950/30 text-green-600 dark:text-green-400 rounded-full flex items-center justify-center">
                                                <Check className="w-6 h-6" />
                                            </div>
                                            <h4 className="text-base font-bold text-gray-900 dark:text-white">All caught up!</h4>
                                            <p className="text-sm text-gray-500 dark:text-gray-400 max-w-sm">No missing translations found across all episodes in this project.</p>
                                        </div>
                                    ) : (
                                        <div className="space-y-4">
                                            <div className="bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-900/50 rounded-2xl p-4 flex gap-3 text-amber-800 dark:text-amber-300">
                                                <ShieldAlert className="w-5 h-5 shrink-0 mt-0.5" />
                                                <div>
                                                    <div className="font-bold text-sm">Action Needed</div>
                                                    <div className="text-xs mt-0.5">Found {checkMissingModal.results.total_missing} untranslated line(s) across {checkMissingModal.results.results.length} episode(s). These lines will be translated automatically with context on next translate run, or you can resolve them manually.</div>
                                                </div>
                                            </div>
                                            <div className="space-y-6">
                                                {checkMissingModal.results.results.map((epResult, idx) => (
                                                    <div key={idx} className="border border-gray-150 dark:border-gray-700 rounded-2xl overflow-hidden">
                                                        <div className="px-4 py-2.5 bg-slate-50 dark:bg-slate-900/50 border-b border-gray-150 dark:border-gray-700 flex justify-between items-center">
                                                            <span className="font-bold text-sm text-gray-800 dark:text-gray-200">{epResult.episode_name}</span>
                                                            <span className="text-xs bg-amber-100 dark:bg-amber-950/60 text-amber-800 dark:text-amber-300 px-2 py-0.5 rounded-full font-semibold">{epResult.missing_count} line(s) missing</span>
                                                        </div>
                                                        <div className="divide-y divide-gray-100 dark:divide-gray-700 max-h-60 overflow-y-auto">
                                                            {epResult.lines.map((line, lIdx) => (
                                                                <div key={lIdx} className="p-3 text-xs flex gap-3 hover:bg-slate-50/50 dark:hover:bg-gray-750/30">
                                                                    <div className="text-gray-400 shrink-0 font-mono">#{line.line_index + 1}</div>
                                                                    <div className="text-gray-400 shrink-0 font-mono">{line.timecode}</div>
                                                                    <div className="text-gray-800 dark:text-gray-200 flex-1 italic">"{line.original}"</div>
                                                                    <div className="text-[10px] text-gray-400 font-semibold uppercase">{line.lang_code}</div>
                                                                </div>
                                                            ))}
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )
                                ) : null}
                            </div>

                            <div className="px-6 py-4 bg-gray-50/50 dark:bg-gray-805/50 border-t border-gray-100 dark:border-gray-700 flex justify-end gap-3">
                                {checkMissingModal.results?.total_missing > 0 && (
                                    <button
                                        onClick={handleFixMissing}
                                        className="px-5 py-2 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-700 hover:to-teal-700 text-white rounded-xl text-sm font-bold shadow-lg shadow-emerald-600/20 hover:shadow-emerald-600/30 transition-all flex items-center gap-1.5"
                                    >
                                        <Sparkles size={16} />
                                        Fix Gaps
                                    </button>
                                )}
                                <button
                                    onClick={() => setCheckMissingModal({ isOpen: false, results: null, loading: false })}
                                    className="px-5 py-2 bg-white dark:bg-gray-800 border border-gray-250 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 rounded-xl text-sm font-bold text-gray-700 dark:text-gray-300 transition-colors"
                                >
                                    Close
                                </button>
                            </div>
                        </motion.div>
                    </div>
                )}
                {scriptFixModal.isOpen && (
                    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            onClick={() => setScriptFixModal({ isOpen: false, results: null, loading: false })}
                            className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm"
                        />
                        <motion.div
                            initial={{ opacity: 0, scale: 0.95, y: 10 }}
                            animate={{ opacity: 1, scale: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.95, y: 10 }}
                            transition={{ type: 'spring', duration: 0.3 }}
                            className="relative bg-white dark:bg-gray-800 rounded-3xl shadow-2xl border border-gray-150 dark:border-gray-700 max-w-2xl w-full overflow-hidden z-10 flex flex-col max-h-[85vh]"
                        >
                            <div className="px-6 py-4 border-b border-gray-150 dark:border-gray-750 flex justify-between items-center bg-gray-50/50 dark:bg-gray-850/50">
                                <h3 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                    <SpellCheck className="text-indigo-600 dark:text-indigo-400" />
                                    Wrong-Alphabet Characters
                                </h3>
                                <button
                                    onClick={() => setScriptFixModal({ isOpen: false, results: null, loading: false })}
                                    className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-750 transition-colors"
                                >
                                    <X size={18} />
                                </button>
                            </div>

                            <div className="p-6 overflow-y-auto flex-1 space-y-4">
                                {scriptFixModal.loading ? (
                                    <div className="flex flex-col items-center justify-center py-12 space-y-3">
                                        <RefreshCw className="w-8 h-8 text-indigo-600 dark:text-indigo-400 animate-spin" />
                                        <p className="text-sm font-medium text-gray-500 dark:text-gray-400">Scanning translations for characters from other alphabets...</p>
                                    </div>
                                ) : scriptFixModal.results ? (
                                    (scriptFixModal.results.chars_fixed === 0 && scriptFixModal.results.lines_flagged === 0) ? (
                                        <div className="flex flex-col items-center justify-center py-10 text-center space-y-2">
                                            <div className="w-12 h-12 bg-green-50 dark:bg-green-950/30 text-green-600 dark:text-green-400 rounded-full flex items-center justify-center">
                                                <Check className="w-6 h-6" />
                                            </div>
                                            <h4 className="text-base font-bold text-gray-900 dark:text-white">Nothing to fix</h4>
                                            <p className="text-sm text-gray-500 dark:text-gray-400 max-w-sm">Every translation in this show uses the target alphabet.</p>
                                        </div>
                                    ) : (
                                        <div className="space-y-4">
                                            <div className="grid grid-cols-2 gap-3">
                                                <div className="rounded-2xl border border-emerald-200 dark:border-emerald-900/50 bg-emerald-50 dark:bg-emerald-950/20 p-4">
                                                    <div className="text-2xl font-bold text-emerald-700 dark:text-emerald-400">{scriptFixModal.results.chars_fixed}</div>
                                                    <div className="text-xs font-semibold text-emerald-800 dark:text-emerald-300 mt-0.5">look-alike characters</div>
                                                    <div className="text-xs text-emerald-700/70 dark:text-emerald-400/70 mt-1">Fixed for free — no model request.</div>
                                                </div>
                                                <div className="rounded-2xl border border-amber-200 dark:border-amber-900/50 bg-amber-50 dark:bg-amber-950/20 p-4">
                                                    <div className="text-2xl font-bold text-amber-700 dark:text-amber-400">{scriptFixModal.results.lines_flagged}</div>
                                                    <div className="text-xs font-semibold text-amber-800 dark:text-amber-300 mt-0.5">lines need the model</div>
                                                    <div className="text-xs text-amber-700/70 dark:text-amber-400/70 mt-1">
                                                        Sent together in ~{scriptFixModal.results.estimated_requests} request{scriptFixModal.results.estimated_requests === 1 ? '' : 's'} for the whole show.
                                                    </div>
                                                </div>
                                            </div>

                                            {scriptFixModal.results.samples?.length > 0 && (
                                                <div className="border border-gray-150 dark:border-gray-700 rounded-2xl overflow-hidden">
                                                    <div className="px-4 py-2.5 bg-slate-50 dark:bg-slate-900/50 border-b border-gray-150 dark:border-gray-700 text-xs font-bold text-gray-600 dark:text-gray-300 uppercase tracking-wide">
                                                        Examples
                                                    </div>
                                                    <div className="divide-y divide-gray-100 dark:divide-gray-700 max-h-64 overflow-y-auto">
                                                        {scriptFixModal.results.samples.map((s, i) => (
                                                            <div key={i} className="p-3 text-xs space-y-1">
                                                                <div className="text-gray-400 font-mono">{s.episode}</div>
                                                                <div className="text-gray-800 dark:text-gray-200">{s.current}</div>
                                                                <div className="text-gray-400 italic">{s.source}</div>
                                                            </div>
                                                        ))}
                                                    </div>
                                                </div>
                                            )}

                                            <p className="text-xs text-gray-500 dark:text-gray-400">
                                                Affects {scriptFixModal.results.episodes_affected} episode(s). Changed episodes are saved and re-exported next to their media. Anything the model can't clean stays flagged in the review queue.
                                            </p>
                                        </div>
                                    )
                                ) : null}
                            </div>

                            <div className="px-6 py-4 bg-gray-50/50 dark:bg-gray-805/50 border-t border-gray-100 dark:border-gray-700 flex justify-end gap-3">
                                {scriptFixModal.results?.chars_fixed > 0 && scriptFixModal.results?.lines_flagged > 0 && (
                                    <button
                                        onClick={() => handleRunScriptFix(false)}
                                        className="px-4 py-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 rounded-xl text-sm font-medium text-gray-700 dark:text-gray-300 transition-colors"
                                        title="Apply only the free look-alike fixes and flag the rest for review"
                                    >
                                        Free fixes only
                                    </button>
                                )}
                                {(scriptFixModal.results?.chars_fixed > 0 || scriptFixModal.results?.lines_flagged > 0) && (
                                    <button
                                        onClick={() => handleRunScriptFix(scriptFixModal.results.lines_flagged > 0)}
                                        className="px-5 py-2 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-700 hover:to-purple-700 text-white rounded-xl text-sm font-bold shadow-lg shadow-indigo-600/20 hover:shadow-indigo-600/30 transition-all flex items-center gap-1.5"
                                    >
                                        <Sparkles size={16} />
                                        {scriptFixModal.results.lines_flagged > 0
                                            ? `Fix All (~${scriptFixModal.results.estimated_requests} request${scriptFixModal.results.estimated_requests === 1 ? '' : 's'})`
                                            : 'Fix All'}
                                    </button>
                                )}
                                <button
                                    onClick={() => setScriptFixModal({ isOpen: false, results: null, loading: false })}
                                    className="px-5 py-2 bg-white dark:bg-gray-800 border border-gray-250 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 rounded-xl text-sm font-bold text-gray-700 dark:text-gray-300 transition-colors"
                                >
                                    Close
                                </button>
                            </div>
                        </motion.div>
                    </div>
                )}
            </AnimatePresence>
            <TranslationComparisonModal
                isOpen={comparisonModal.isOpen}
                onClose={() => setComparisonModal(prev => ({ ...prev, isOpen: false }))}
                projectName={projectName}
                episodeName={comparisonModal.episodeName}
                currentLines={comparisonModal.currentLines}
                newTranslations={comparisonModal.newTranslations}
                onApply={handleMergeComparison}
            />

            <EmbeddedTracksModal
                isOpen={embeddedTracksModal.isOpen}
                onClose={() => setEmbeddedTracksModal({ isOpen: false, episodeName: null })}
                projectName={projectName}
                episodeName={embeddedTracksModal.episodeName}
                onExtractionComplete={() => loadData()}
            />

            <SyncOptionsModal
                isOpen={syncModalOpen}
                onClose={() => setSyncModalOpen(false)}
                onConfirm={handleStartProjectSync}
                isProjectSpecific={true}
                projectName={project?.show_name || projectName}
            />

            <JobProgressWidget />
        </div>
    );
};

export default ProjectDetail;
