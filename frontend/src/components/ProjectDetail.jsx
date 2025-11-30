import React, { useState, useEffect, useRef } from 'react';
import { useParams, Link, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { ArrowLeft, FileText, Upload, Book, Sparkles, RefreshCw, Save, Edit2, ChevronDown, ChevronUp, ChevronRight, Trash2, Settings, Check, Folder, Film, Tv, X, Languages } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import GlossaryEditor from './GlossaryEditor';
import GlossaryReviewModal from './GlossaryReviewModal';
import ContextReviewModal from './ContextReviewModal';
import TranslationModal from './TranslationModal';
import ProjectSettingsModal from './ProjectSettingsModal';
import FileSelectionModal from './FileSelectionModal';
import { useJobs } from '../context/JobContext';

const ProjectDetail = () => {
    const { projectName } = useParams();
    const { activeJobs, addJob, removeJob } = useJobs();
    const location = useLocation();
    const navigate = useNavigate();
    const handledJobsRef = useRef(new Set());

    const [project, setProject] = useState(null);
    const [episodes, setEpisodes] = useState([]);
    const [subprojects, setSubprojects] = useState([]);
    const [seasonGroups, setSeasonGroups] = useState({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [expandedSeasons, setExpandedSeasons] = useState(new Set());

    // Selection State
    const [selectedEpisodes, setSelectedEpisodes] = useState(new Set());
    const [isDownloading, setIsDownloading] = useState(false);

    // Editing States
    const [isEditingContext, setIsEditingContext] = useState(false);
    const [editedContext, setEditedContext] = useState('');
    const [isEditingGlossary, setIsEditingGlossary] = useState(false);
    const [editedGlossary, setEditedGlossary] = useState({ terms: [] });
    const [isGlossaryOpen, setIsGlossaryOpen] = useState(() => {
        const saved = localStorage.getItem(`panel_glossary_${projectName}`);
        return saved !== null ? JSON.parse(saved) : true;
    });
    const [isContextOpen, setIsContextOpen] = useState(() => {
        const saved = localStorage.getItem(`panel_context_${projectName}`);
        return saved !== null ? JSON.parse(saved) : true;
    });
    const [isSubprojectsOpen, setIsSubprojectsOpen] = useState(() => {
        const saved = localStorage.getItem(`panel_subprojects_${projectName}`);
        return saved !== null ? JSON.parse(saved) : true;
    });
    const [isSettingsOpen, setIsSettingsOpen] = useState(false);
    const [isUploading, setIsUploading] = useState(false);
    const [file, setFile] = useState(null);
    const [importModal, setImportModal] = useState({ isOpen: false, type: null });
    const [availableProjects, setAvailableProjects] = useState([]);

    // Modal States
    const [translationModal, setTranslationModal] = useState({ isOpen: false, type: 'batch', target: null });
    const [glossaryReviewModal, setGlossaryReviewModal] = useState({ isOpen: false, newTerms: [], existingTerms: [] });
    const [contextReviewModal, setContextReviewModal] = useState({ isOpen: false, newContext: '', currentContext: '' });
    const [fileSelectionModal, setFileSelectionModal] = useState({ isOpen: false, type: null, action: null });
    const [enableResearch, setEnableResearch] = useState(false);

    // Job Tracking State
    const [contextJobId, setContextJobId] = useState(null);
    const [glossaryJobId, setGlossaryJobId] = useState(null);

    const isParent = project?.type === 'parent';
    const isMovie = project?.type === 'movie';



    useEffect(() => {
        loadData();
    }, [projectName]);

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
            setEditedGlossary(projData.glossary || { terms: [] });
            setAvailableProjects(allProjRes.data.filter(p => p !== projectName));

            // Load additional data based on type
            if (projData.type === 'parent') {
                // For parent projects, find subprojects
                const allDetails = await Promise.all(allProjRes.data.map(async name => {
                    try {
                        const res = await api.getProject(name);
                        return { ...res.data, name: name };
                    } catch {
                        return null;
                    }
                }));
                const children = allDetails
                    .filter(p => p && p.parent_project === projectName);
                setSubprojects(children);
            } else {
                // For regular projects, load episodes
                const epRes = await api.getEpisodes(projectName);
                setProject(projRes.data);
                setEpisodes(epRes.data);

                // Group episodes by season
                const groups = {};
                epRes.data.forEach(ep => {
                    let season = ep.season;
                    if (!season) {
                        // Auto-detect from filename (SxxExx)
                        const match = ep.name?.match(/S(\d+)E\d+/i);
                        if (match) {
                            season = parseInt(match[1], 10);
                        } else {
                            season = "Unassigned";
                        }
                    }

                    const key = season === "Unassigned" ? "Unassigned" : `Season ${season}`;
                    if (!groups[key]) groups[key] = [];
                    groups[key].push(ep);
                });

                // Sort seasons
                const sortedGroups = {};
                Object.keys(groups).sort((a, b) => {
                    if (a === "Unassigned") return 1;
                    if (b === "Unassigned") return -1;
                    return parseInt(a.replace("Season ", "")) - parseInt(b.replace("Season ", ""));
                }).forEach(key => {
                    sortedGroups[key] = groups[key];
                });

                setSeasonGroups(sortedGroups);

                // Expand all seasons by default
                setExpandedSeasons(new Set(Object.keys(sortedGroups)));
            }
        } catch (err) {
            console.error("Failed to load project data", err);
            setError("Failed to load project. It might not exist.");
        } finally {
            setLoading(false);
        }
    };

    const handleJobComplete = (job) => {
        if (!job || !job.result) {
            if (job.type !== 'translate_episode' && job.type !== 'batch_translate') return;
        }
        const result = job.result;

        if (job.type === 'context' || job.type === 'create_context' || job.type === 'enhance_context') {
            setContextReviewModal({
                isOpen: true,
                newContext: result.context_guide,
                currentContext: project.context_guide || ''
            });
        } else if (job.type === 'glossary' || job.type === 'create_glossary' || job.type === 'enhance_glossary') {
            const existingTerms = project.glossary?.terms || [];
            const existingTermNames = new Set(existingTerms.map(t => t.term.toLowerCase()));
            const newTerms = (result.terms || []).filter(
                t => !existingTermNames.has(t.term.toLowerCase())
            );

            setGlossaryReviewModal({
                isOpen: true,
                newTerms: newTerms,
                existingTerms: existingTerms
            });
        } else if (job.type === 'scan_episode') {
            loadData();
        } else if (job.type === 'translate_episode') {
            loadData();
            const episodeName = job.metadata?.episodeName || (typeof job.description === 'string' ? job.description.replace('Translating ', '') : 'Episode');
            setTimeout(() => {
                if (window.confirm(`Translation complete for ${episodeName}. Do you want to open the editor?`)) {
                    navigate(`/project/${encodeURIComponent(projectName)}/episode/${encodeURIComponent(episodeName)}`);
                }
            }, 100);
        } else if (job.type === 'batch_translate') {
            loadData();
            setTimeout(() => {
                alert("Batch translation complete.");
            }, 100);
        }
    };

    // Watch for completed jobs
    useEffect(() => {
        if (!project) return;

        // Load previously handled jobs from localStorage
        const storageKey = `handledJobs_${projectName}`;
        const storedHandledJobs = localStorage.getItem(storageKey);
        if (storedHandledJobs && handledJobsRef.current.size === 0) {
            try {
                const jobIds = JSON.parse(storedHandledJobs);
                handledJobsRef.current = new Set(jobIds);
            } catch (e) {
                console.error('Failed to load handled jobs:', e);
            }
        }

        const projectJobs = Object.values(activeJobs).filter(
            job => ((job.metadata?.projectId === projectName) || (location.state?.highlightJobId === job.id)) && job.status === 'completed'
        );

        for (const job of projectJobs) {
            const isHighlighted = location.state?.highlightJobId === job.id;
            const isUnhandled = !handledJobsRef.current.has(job.id);

            if (job.type === 'enhance_context' || job.type === 'create_context' || job.type === 'enhance_glossary' || job.type === 'create_glossary') {
                continue;
            }

            if (isHighlighted || (isUnhandled && !job.metadata?.handled)) {
                if (!job.result && job.type !== 'translate_episode' && job.type !== 'batch_translate') {
                    continue;
                }

                handleJobComplete(job);
                handledJobsRef.current.add(job.id);

                // Persist to localStorage
                try {
                    const jobArray = Array.from(handledJobsRef.current);
                    localStorage.setItem(storageKey, JSON.stringify(jobArray));
                } catch (e) {
                    console.error('Failed to save handled jobs:', e);
                }
            }
        }
    }, [activeJobs, projectName, project, location.state, navigate]);

    const handleAIAction = async (type, action, selectedFiles = null) => {
        if (selectedFiles === null) {
            if (episodes.length > 0) {
                setFileSelectionModal({
                    isOpen: true,
                    type: type,
                    action: action
                });
                return;
            }
            selectedFiles = [];
        }

        try {
            let res;
            const settings = project.settings || {};
            const contextModel = settings.context_model || 'gemini-flash-lite-latest';
            const glossaryModel = settings.glossary_model || 'gemini-flash-lite-latest';

            if (type === 'context') {
                res = action === 'enhance'
                    ? await api.enhanceContext(projectName, contextModel)
                    : await api.createContext(projectName, contextModel);
            } else {
                if (action === 'enhance') {
                    // Auto-enable research if no files selected
                    const shouldEnableResearch = selectedFiles.length === 0 || enableResearch;
                    res = await api.enhanceGlossary(projectName, { episode_names: selectedFiles }, glossaryModel, shouldEnableResearch);
                } else {
                    res = await api.createGlossary(projectName, glossaryModel);
                }
            }

            if (res.data.job_id) {
                addJob(res.data.job_id, type, `AI Task: ${action} ${type}`, { link: `/project/${encodeURIComponent(projectName)}`, projectId: projectName });
                if (type === 'context') setContextJobId(res.data.job_id);
                else setGlossaryJobId(res.data.job_id);
            }
            setFileSelectionModal({ isOpen: false, type: null, action: null });
        } catch (err) {
            console.error(`Failed to ${action} ${type}`, err);
            alert(`Failed to ${action} ${type}`);
        }
    };

    const handleImport = async (sourceProject) => {
        try {
            const importContext = importModal.type === 'context';
            const importGlossary = importModal.type === 'glossary';
            await api.importProjectData(projectName, sourceProject, importGlossary, importContext);
            setImportModal({ isOpen: false, type: null });
            loadData();
            alert(`Successfully imported ${importModal.type} from ${sourceProject}`);
        } catch (err) {
            console.error("Failed to import data", err);
            alert("Failed to import data");
        }
    };

    const handleSaveContext = async (contextToSave = null) => {
        try {
            const context = contextToSave !== null ? contextToSave : editedContext;
            await api.updateProject(projectName, { context_guide: context });
            setIsEditingContext(false);
            setProject(prev => ({ ...prev, context_guide: context }));
            setEditedContext(context);
        } catch (err) {
            console.error("Failed to save context", err);
        }
    };

    const handleSaveGlossary = async (newGlossary) => {
        try {
            await api.updateProject(projectName, { glossary: newGlossary });
            setIsEditingGlossary(false);
            setEditedGlossary(newGlossary);
            setProject(prev => ({ ...prev, glossary: newGlossary }));
        } catch (err) {
            console.error("Failed to save glossary", err);
        }
    };

    const handleDeleteContext = async () => {
        if (!window.confirm("Are you sure you want to delete the context guide? This action cannot be undone.")) {
            return;
        }
        try {
            await api.deleteContext(projectName);
            setContextJobId(null);
            loadData();
            alert("Context guide deleted successfully");
        } catch (err) {
            console.error("Failed to delete context", err);
            alert("Failed to delete context");
        }
    };

    const handleDeleteGlossary = async () => {
        if (!window.confirm("Are you sure you want to delete the glossary? This action cannot be undone.")) {
            return;
        }
        try {
            await api.deleteGlossary(projectName);
            setGlossaryJobId(null);
            loadData();
            alert("Glossary deleted successfully");
        } catch (err) {
            console.error("Failed to delete glossary", err);
            alert("Failed to delete glossary");
        }
    };

    const handleSaveSettings = async (newSettings) => {
        try {
            await api.updateProject(projectName, { settings: newSettings });
            setProject(prev => ({ ...prev, settings: newSettings }));
        } catch (err) {
            console.error("Failed to save settings", err);
            alert("Failed to save settings");
        }
    };

    const handleSelectAll = () => {
        if (selectedEpisodes.size === episodes.length) {
            setSelectedEpisodes(new Set());
        } else {
            setSelectedEpisodes(new Set(episodes.map(ep => ep.name)));
        }
    };

    const handleSelectEpisode = (episodeName) => {
        const newSelected = new Set(selectedEpisodes);
        if (newSelected.has(episodeName)) {
            newSelected.delete(episodeName);
        } else {
            newSelected.add(episodeName);
        }
        setSelectedEpisodes(newSelected);
    };

    const handleBatchDownload = async () => {
        try {
            setIsDownloading(true);
            const episodeNames = Array.from(selectedEpisodes);
            await api.batchDownload(projectName, episodeNames);
        } catch (err) {
            console.error("Failed to download batch", err);
            alert("Failed to download batch");
        } finally {
            setIsDownloading(false);
        }
    };

    const handleBatchDelete = async () => {
        if (!window.confirm(`Are you sure you want to delete ${selectedEpisodes.size} episodes? This action cannot be undone.`)) {
            return;
        }
        try {
            const episodeNames = Array.from(selectedEpisodes);
            await Promise.all(episodeNames.map(name => api.deleteEpisode(projectName, name)));
            setSelectedEpisodes(new Set());
            loadData();
        } catch (err) {
            console.error("Failed to delete batch", err);
            alert("Failed to delete batch");
        }
    };

    const handleBatchSetSeason = async () => {
        const seasonStr = prompt("Enter Season Number (e.g., 1, 2):");
        if (!seasonStr) return;

        const season = parseInt(seasonStr, 10);
        if (isNaN(season)) {
            alert("Invalid season number");
            return;
        }

        try {
            const episodeNames = Array.from(selectedEpisodes);
            await Promise.all(episodeNames.map(name => api.updateEpisodeMetadata(projectName, name, { season })));
            setSelectedEpisodes(new Set());
            loadData();
        } catch (err) {
            console.error("Failed to set season", err);
            alert("Failed to set season");
        }
    };

    const toggleSeason = (seasonKey) => {
        const newExpanded = new Set(expandedSeasons);
        if (newExpanded.has(seasonKey)) {
            newExpanded.delete(seasonKey);
        } else {
            newExpanded.add(seasonKey);
        }
        setExpandedSeasons(newExpanded);
    };

    const handleTranslationConfirm = async (enhanceGlossary) => {
        try {
            const isBatch = translationModal.type === 'batch';
            const episodeNames = isBatch ? Array.from(selectedEpisodes) : [translationModal.target];
            const settings = project.settings || {};
            const translationModel = settings.translation_model || 'gemini-flash-latest';
            const glossaryModel = settings.glossary_model || 'gemini-flash-lite-latest';

            if (enhanceGlossary) {
                // Auto-enable research if no files selected
                const shouldEnableResearch = episodeNames.length === 0 || enableResearch;
                const res = await api.enhanceGlossary(projectName, { episode_names: episodeNames }, glossaryModel, shouldEnableResearch);
                if (res.data.job_id) {
                    addJob(res.data.job_id, 'enhance_glossary', `Enhancing Glossary (${episodeNames.length} files)`, { projectId: projectName });
                    setGlossaryJobId(res.data.job_id);
                }
                alert("Glossary enhancement started. Please wait for it to complete before translating.");
            } else {
                if (isBatch) {
                    const res = await api.batchTranslate(projectName, episodeNames, translationModel);
                    if (res.data.job_id) {
                        addJob(res.data.job_id, 'batch_translate', `Batch Translating ${episodeNames.length} episodes`, { projectId: projectName });
                    }
                } else {
                    const res = await api.translateEpisode(projectName, translationModal.target, translationModel);
                    if (res.data.job_id) {
                        addJob(res.data.job_id, 'translate_episode', `Translating ${translationModal.target}`, { projectId: projectName, episodeName: translationModal.target });
                    }
                }
            }
            setTranslationModal({ ...translationModal, isOpen: false });
        } catch (err) {
            console.error("Translation request failed", err);
            alert("Failed to start translation");
        }
    };

    // --- Render Helpers ---



    if (loading) return <div className="h-screen flex items-center justify-center text-gray-500">Loading project...</div>;
    if (error) return <div className="h-screen flex items-center justify-center text-red-500">{error}</div>;
    if (!project) {

        return null;
    }



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
                                <Link to={`/project/${encodeURIComponent(project.parent_project)}`} className="text-sm text-indigo-600 hover:underline block mb-0.5">
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
                            </div>
                        </div>
                    </div>
                    <div className="flex gap-3">
                        <button
                            onClick={() => setIsSettingsOpen(true)}
                            className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
                        >
                            <Settings size={20} />
                        </button>
                        {!isParent && (
                            <button
                                onClick={() => setIsUploading(true)}
                                className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg transition-colors text-sm font-medium shadow-sm"
                            >
                                <Upload size={18} />
                                Add {isMovie ? 'File' : 'Episode'}
                            </button>
                        )}
                    </div>
                </div>
            </header>

            <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">

                {/* Subprojects Section (Parent Only) */}
                {isParent && (
                    <section className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                        <div
                            className="p-6 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center bg-gray-50 dark:bg-gray-900/50 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900/70 transition-colors"
                            onClick={() => {
                                setIsSubprojectsOpen(!isSubprojectsOpen);
                                localStorage.setItem(`panel_subprojects_${projectName}`, JSON.stringify(!isSubprojectsOpen));
                            }}
                        >
                            <div className="flex items-center gap-3">
                                <Folder className="text-amber-500" size={24} />
                                <h2 className="text-lg font-bold text-gray-900 dark:text-white">Subprojects</h2>
                                <span className="text-sm text-gray-500">({subprojects.length})</span>
                            </div>
                            {isSubprojectsOpen ? <ChevronUp size={20} className="text-gray-400" /> : <ChevronDown size={20} className="text-gray-400" />}
                        </div>
                        <AnimatePresence>
                            {isSubprojectsOpen && (
                                <motion.div
                                    initial={{ height: 0 }}
                                    animate={{ height: 'auto' }}
                                    exit={{ height: 0 }}
                                    className="overflow-hidden"
                                >
                                    <div className="p-6">
                                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                                            {subprojects.map(sub => (
                                                <Link key={sub.name} to={`/project/${encodeURIComponent(sub.name)}`} className="block group">
                                                    <div className="bg-white dark:bg-gray-800 p-4 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 hover:shadow-md transition-all">
                                                        <div className="flex items-center gap-3">
                                                            <div className={`p-2 rounded-lg ${sub.type === 'movie' ? 'bg-purple-100 text-purple-600' : 'bg-blue-100 text-blue-600'}`}>
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
                                                <div className="col-span-full text-center py-8 text-gray-500">
                                                    No subprojects yet. Create one from the main dashboard.
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                </motion.div>
                            )}
                        </AnimatePresence>
                    </section>
                )}

                {/* Context Guide Section */}
                <section className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                    <div
                        className="p-6 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center bg-gray-50 dark:bg-gray-900/50 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900/70 transition-colors"
                        onClick={() => {
                            setIsContextOpen(!isContextOpen);
                            localStorage.setItem(`panel_context_${projectName}`, JSON.stringify(!isContextOpen));
                        }}
                    >
                        <div className="flex items-center gap-3">
                            <Book className="text-indigo-500" size={24} />
                            <h2 className="text-lg font-bold text-gray-900 dark:text-white">Context Guide</h2>
                        </div>
                        {isContextOpen ? <ChevronUp size={20} className="text-gray-400" /> : <ChevronDown size={20} className="text-gray-400" />}
                    </div>

                    {/* Action buttons - Always visible */}
                    <div className="px-6 py-3 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-900/30 flex gap-2 justify-end">
                        {(() => {
                            const job = contextJobId ? activeJobs[contextJobId] : null;

                            if (job && job.status && (job.status === 'running' || job.status === 'pending')) {
                                return (
                                    <div className="flex items-center gap-2">
                                        <span className="text-sm text-indigo-600 animate-pulse font-medium">Generating...</span>
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                setContextJobId(null);
                                            }}
                                            className="p-1 hover:bg-red-50 text-red-500 rounded"
                                        >
                                            <X size={16} />
                                        </button>
                                    </div>
                                );
                            }

                            if (job && job.status && job.status === 'completed') {
                                return (
                                    <button
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            handleJobComplete(job);
                                            setContextJobId(null);
                                        }}
                                        className="flex items-center gap-2 bg-green-600 hover:bg-green-700 text-white px-3 py-1.5 rounded-lg transition-colors text-sm font-medium animate-bounce-subtle"
                                    >
                                        <Check size={16} />
                                        Review Result
                                    </button>
                                );
                            }

                            if (!isEditingContext) {
                                return (
                                    <>
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                setImportModal({ isOpen: true, type: 'context' });
                                            }}
                                            className="flex items-center gap-2 text-gray-600 hover:text-gray-700 px-3 py-1.5 rounded-lg hover:bg-gray-100 transition-colors text-sm font-medium"
                                        >
                                            <Folder size={16} />
                                            Import
                                        </button>
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                handleAIAction('context', project.context_guide ? 'enhance' : 'create');
                                            }}
                                            className="flex items-center gap-2 text-indigo-600 hover:text-indigo-700 px-3 py-1.5 rounded-lg hover:bg-indigo-50 transition-colors text-sm font-medium"
                                        >
                                            <Sparkles size={16} />
                                            {project.context_guide ? 'Enhance' : 'Create'}
                                        </button>
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                setIsEditingContext(true);
                                                if (!isContextOpen) {
                                                    setIsContextOpen(true);
                                                    localStorage.setItem(`panel_context_${projectName}`, JSON.stringify(true));
                                                }
                                            }}
                                            className="flex items-center gap-2 text-gray-600 hover:text-gray-700 px-3 py-1.5 rounded-lg hover:bg-gray-100 transition-colors text-sm font-medium"
                                        >
                                            <Edit2 size={16} />
                                            Edit
                                        </button>
                                        {project.context_guide && (
                                            <button
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    handleDeleteContext();
                                                }}
                                                className="flex items-center gap-2 text-red-600 hover:text-red-700 px-3 py-1.5 rounded-lg hover:bg-red-50 transition-colors text-sm font-medium"
                                                title="Delete context guide"
                                            >
                                                <Trash2 size={16} />
                                            </button>
                                        )}
                                    </>
                                );
                            } else {
                                return (
                                    <button
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            handleSaveContext();
                                        }}
                                        className="flex items-center gap-2 bg-green-600 hover:bg-green-700 text-white px-4 py-1.5 rounded-lg transition-colors text-sm font-medium"
                                    >
                                        <Save size={16} />
                                        Save
                                    </button>
                                );
                            }
                        })()}
                    </div>

                    <AnimatePresence>
                        {isContextOpen && (
                            <motion.div
                                initial={{ height: 0 }}
                                animate={{ height: 'auto' }}
                                exit={{ height: 0 }}
                                className="overflow-hidden"
                            >
                                <div className="p-6">
                                    {isEditingContext ? (
                                        <textarea
                                            value={editedContext}
                                            onChange={(e) => setEditedContext(e.target.value)}
                                            placeholder="Describe the tone, style, and context for translations (e.g., 'Formal fantasy dialogue with British spelling')"
                                            className="w-full min-h-[200px] px-4 py-3 rounded-lg border border-gray-300 dark:border-gray-600 focus:ring-2 focus:ring-indigo-500 dark:bg-gray-700 dark:text-white resize-y font-mono text-sm"
                                        />
                                    ) : (
                                        <div className="prose dark:prose-invert max-w-none">
                                            {project.context_guide ? (
                                                <p className="whitespace-pre-wrap text-gray-700 dark:text-gray-300">{project.context_guide}</p>
                                            ) : (
                                                <p className="text-gray-400 italic">No context guide yet. Click "Create" to generate one with AI, or "Edit" to write manually.</p>
                                            )}
                                        </div>
                                    )}
                                </div>
                            </motion.div>
                        )}
                    </AnimatePresence>
                </section>

                {/* Glossary Section */}
                <section className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                    <div
                        className="p-6 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center bg-gray-50 dark:border-gray-900/50 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-900/70 transition-colors"
                        onClick={() => {
                            setIsGlossaryOpen(!isGlossaryOpen);
                            localStorage.setItem(`panel_glossary_${projectName}`, JSON.stringify(!isGlossaryOpen));
                        }}
                    >
                        <div className="flex items-center gap-3">
                            <Book className="text-pink-500" size={24} />
                            <h2 className="text-lg font-bold text-gray-900 dark:text-white">Glossary ({project.glossary?.terms?.length || 0} terms)</h2>
                        </div>
                        {isGlossaryOpen ? <ChevronUp size={20} className="text-gray-400" /> : <ChevronDown size={20} className="text-gray-400" />}
                    </div>

                    {/* Action buttons - Always visible */}
                    <div className="px-6 py-3 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-900/30 flex gap-2 justify-end">
                        {(() => {
                            const job = glossaryJobId ? activeJobs[glossaryJobId] : null;

                            if (job && job.status && (job.status === 'running' || job.status === 'pending')) {
                                return (
                                    <div className="flex items-center gap-2">
                                        <span className="text-sm text-indigo-600 animate-pulse font-medium">Enhancing...</span>
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                setGlossaryJobId(null);
                                            }}
                                            className="p-1 hover:bg-red-50 text-red-500 rounded"
                                        >
                                            <X size={16} />
                                        </button>
                                    </div>
                                );
                            }

                            if (job && job.status && job.status === 'completed') {
                                return (
                                    <button
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            handleJobComplete(job);
                                            setGlossaryJobId(null);
                                        }}
                                        className="fl items-center gap-2 bg-green-600 hover:bg-green-700 text-white px-3 py-1.5 rounded-lg transition-colors text-sm font-medium animate-bounce-subtle"
                                    >
                                        <Check size={16} />
                                        Review Result
                                    </button>
                                );
                            }

                            if (!isEditingGlossary) {
                                return (
                                    <>
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                setImportModal({ isOpen: true, type: 'glossary' });
                                            }}
                                            className="flex items-center gap-2 text-gray-600 hover:text-gray-700 px-3 py-1.5 rounded-lg hover:bg-gray-100 transition-colors text-sm font-medium"
                                        >
                                            <Folder size={16} />
                                            Import
                                        </button>
                                        <label className="flex items-center gap-2 text-gray-600 px-3 py-1.5 rounded-lg hover:bg-gray-100 transition-colors text-sm font-medium cursor-pointer" title="Enable web research for glossary terms (slower but more accurate)">
                                            <input
                                                type="checkbox"
                                                checked={enableResearch}
                                                onChange={(e) => {
                                                    e.stopPropagation();
                                                    setEnableResearch(e.target.checked);
                                                }}
                                                className="w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                                            />
                                            <span className="text-xs">Research</span>
                                        </label>
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                handleAIAction('glossary', 'enhance');
                                            }}
                                            className="flex items-center gap-2 text-indigo-600 hover:text-indigo-700 px-3 py-1.5 rounded-lg hover:bg-indigo-50 transition-colors text-sm font-medium"
                                        >
                                            <Sparkles size={16} />
                                            Enhance
                                        </button>
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                setIsEditingGlossary(true);
                                                if (!isGlossaryOpen) {
                                                    setIsGlossaryOpen(true);
                                                    localStorage.setItem(`panel_glossary_${projectName}`, JSON.stringify(true));
                                                }
                                            }}
                                            className="flex items-center gap-2 text-gray-600 hover:text-gray-700 px-3 py-1.5 rounded-lg hover:bg-gray-100 transition-colors text-sm font-medium"
                                        >
                                            <Edit2 size={16} />
                                            Edit
                                        </button>
                                        {project.glossary?.terms?.length > 0 && (
                                            <button
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    handleDeleteGlossary();
                                                }}
                                                className="flex items-center gap-2 text-red-600 hover:text-red-700 px-3 py-1.5 rounded-lg hover:bg-red-50 transition-colors text-sm font-medium"
                                                title="Delete glossary"
                                            >
                                                <Trash2 size={16} />
                                            </button>
                                        )}
                                    </>
                                );
                            }
                            return null;
                        })()}
                    </div>

                    <AnimatePresence>
                        {isGlossaryOpen && (
                            <motion.div
                                initial={{ height: 0 }}
                                animate={{ height: 'auto' }}
                                exit={{ height: 0 }}
                                className="overflow-hidden"
                            >
                                <GlossaryEditor
                                    glossary={isEditingGlossary ? editedGlossary : project.glossary}
                                    readOnly={!isEditingGlossary}
                                    onChange={setEditedGlossary}
                                    onSave={handleSaveGlossary}
                                    onCancel={() => setIsEditingGlossary(false)}
                                    isSaving={false}
                                    hideSaveButton={!isEditingGlossary}
                                />
                            </motion.div>
                        )}
                    </AnimatePresence>
                </section>

                {/* Episodes/Files Section (Non-Parent Only) */}
                {!isParent && (
                    <section>
                        <div className="flex justify-between items-center mb-4">
                            <h2 className="text-xl font-bold text-gray-900 dark:text-white">{isMovie ? 'Movie Files' : 'Episodes'}</h2>
                        </div>

                        {episodes.length === 0 ? (
                            <div className="text-center py-12 bg-gray-50 dark:bg-gray-800/50 rounded-xl border border-dashed border-gray-300 dark:border-gray-700">
                                <p className="text-gray-500 dark:text-gray-400 mb-4">No files uploaded yet.</p>
                                <button
                                    onClick={() => setIsUploading(true)}
                                    className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg transition-colors text-sm font-medium"
                                >
                                    Upload {isMovie ? 'File' : 'Episode'}
                                </button>
                            </div>
                        ) : (
                            <div className="grid grid-cols-1 gap-4 pb-24">
                                <div className="flex items-center gap-2 mb-2 px-4">
                                    <input
                                        type="checkbox"
                                        checked={episodes.length > 0 && selectedEpisodes.size === episodes.length}
                                        onChange={handleSelectAll}
                                        className="w-5 h-5 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                                    />
                                    <span className="text-sm text-gray-500">Select All</span>
                                </div>

                                {/* Season Grouping UI */}
                                {Object.entries(seasonGroups).map(([seasonKey, seasonEpisodes]) => (
                                    <div key={seasonKey} className="mb-4">
                                        <div
                                            className="flex items-center gap-2 mb-2 px-2 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800 rounded p-1 transition-colors"
                                            onClick={() => toggleSeason(seasonKey)}
                                        >
                                            {expandedSeasons.has(seasonKey) ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                                            <h3 className="font-bold text-gray-700 dark:text-gray-300">{seasonKey}</h3>
                                            <span className="text-xs text-gray-500 bg-gray-200 dark:bg-gray-700 px-2 py-0.5 rounded-full">{seasonEpisodes.length}</span>
                                        </div>

                                        <AnimatePresence>
                                            {expandedSeasons.has(seasonKey) && (
                                                <motion.div
                                                    key={seasonKey}
                                                    initial={{ height: 0, opacity: 0 }}
                                                    animate={{ height: 'auto', opacity: 1 }}
                                                    exit={{ height: 0, opacity: 0 }}
                                                    className="grid grid-cols-1 gap-4 pl-4 border-l-2 border-gray-200 dark:border-gray-700 ml-2"
                                                >
                                                    {seasonEpisodes.map(ep => (
                                                        <div key={ep.name || ep.id || Math.random()} className={`bg-white dark:bg-gray-800 p-4 rounded-xl shadow-sm border transition-colors flex justify-between items-center ${selectedEpisodes.has(ep.name) ? 'border-indigo-500 ring-1 ring-indigo-500' : 'border-gray-200 dark:border-gray-700'}`}>
                                                            <div className="flex items-center gap-4">
                                                                <input
                                                                    type="checkbox"
                                                                    checked={selectedEpisodes.has(ep.name)}
                                                                    onChange={() => handleSelectEpisode(ep.name)}
                                                                    className="w-5 h-5 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                                                                />
                                                                <div>
                                                                    <div className="flex items-center gap-2">
                                                                        <h3 className="font-bold text-gray-900 dark:text-white">{ep.name}</h3>
                                                                        {ep.translated && (
                                                                            <span className="flex items-center gap-1 text-xs font-medium text-green-600 bg-green-100 dark:bg-green-900/30 dark:text-green-400 px-2 py-0.5 rounded-full">
                                                                                <Check size={12} />
                                                                                Translated
                                                                            </span>
                                                                        )}
                                                                    </div>
                                                                    <p className="text-sm text-gray-500">{ep.line_count} lines</p>
                                                                </div>
                                                            </div>
                                                            <div className="flex items-center gap-3">
                                                                <button
                                                                    onClick={() => setTranslationModal({ isOpen: true, type: 'single', target: ep.name })}
                                                                    className="flex items-center gap-2 text-gray-600 hover:text-indigo-600 px-3 py-1.5 rounded-lg hover:bg-indigo-50 transition-colors text-sm font-medium"
                                                                    title="Translate Episode"
                                                                >
                                                                    <Languages size={18} />
                                                                    <span className="hidden sm:inline">Translate</span>
                                                                </button>
                                                                <Link
                                                                    to={`/project/${encodeURIComponent(projectName)}/episode/${encodeURIComponent(ep.name)}`}
                                                                    className="text-indigo-600 hover:text-indigo-700 font-medium text-sm px-3 py-1.5 rounded-lg hover:bg-indigo-50 transition-colors"
                                                                >
                                                                    Open Editor
                                                                </Link>
                                                            </div>
                                                        </div>
                                                    ))}
                                                </motion.div>
                                            )}
                                        </AnimatePresence>
                                    </div>
                                ))}
                            </div>
                        )}
                    </section>
                )}

            </main>

            {/* Sticky Batch Actions Toolbar */}
            <AnimatePresence>
                {selectedEpisodes.size > 0 && (
                    <motion.div
                        initial={{ y: 100, opacity: 0 }}
                        animate={{ y: 0, opacity: 1 }}
                        exit={{ y: 100, opacity: 0 }}
                        className="fixed bottom-6 left-1/2 transform -translate-x-1/2 bg-white dark:bg-gray-800 rounded-full shadow-2xl border border-gray-200 dark:border-gray-700 px-6 py-3 flex items-center gap-4 z-40"
                    >
                        <div className="flex items-center gap-2 border-r border-gray-200 dark:border-gray-700 pr-4">
                            <span className="font-bold text-indigo-600">{selectedEpisodes.size}</span>
                            <span className="text-sm text-gray-500">selected</span>
                        </div>
                        <div className="flex items-center gap-2">
                            <button
                                onClick={() => setTranslationModal({ isOpen: true, type: 'batch', target: null })}
                                className="p-2 hover:bg-indigo-50 text-indigo-600 rounded-full transition-colors tooltip"
                                title="Translate Selected"
                            >
                                <Languages size={20} />
                            </button>
                            <button
                                onClick={() => handleTranslationConfirm(true)}
                                className="p-2 hover:bg-purple-50 text-purple-600 rounded-full transition-colors tooltip"
                                title="Enhance Glossary First"
                            >
                                <Sparkles size={20} />
                            </button>
                            <button
                                onClick={handleBatchSetSeason}
                                className="p-2 hover:bg-green-50 text-green-600 rounded-full transition-colors tooltip"
                                title="Set Season"
                            >
                                <Tv size={20} />
                            </button>
                            <button
                                onClick={handleBatchDownload}
                                className="p-2 hover:bg-blue-50 text-blue-600 rounded-full transition-colors tooltip"
                                title="Download Selected"
                            >
                                <Upload size={20} className="rotate-180" />
                            </button>
                            <div className="w-px h-6 bg-gray-200 dark:bg-gray-700 mx-1"></div>
                            <button
                                onClick={handleBatchDelete}
                                className="p-2 hover:bg-red-50 text-red-600 rounded-full transition-colors tooltip"
                                title="Delete Selected"
                            >
                                <Trash2 size={20} />
                            </button>
                        </div>
                        <button
                            onClick={() => setSelectedEpisodes(new Set())}
                            className="ml-2 p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-full text-gray-400"
                        >
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
                            // Clean up job
                            if (glossaryJobId) {
                                removeJob(glossaryJobId);
                                setGlossaryJobId(null);
                            }
                        }}
                        onDelete={() => {
                            if (glossaryJobId) {
                                removeJob(glossaryJobId);
                                setGlossaryJobId(null);
                            }
                        }}
                    />
                )}
                {contextReviewModal.isOpen && (
                    <ContextReviewModal
                        isOpen={contextReviewModal.isOpen}
                        onClose={() => setContextReviewModal({ ...contextReviewModal, isOpen: false })}
                        newContext={contextReviewModal.newContext}
                        currentContext={contextReviewModal.currentContext}
                        onConfirm={(finalContext) => {
                            handleSaveContext(finalContext); // This needs to be adapted to accept string
                            setEditedContext(finalContext);
                            setProject(prev => ({ ...prev, context_guide: finalContext }));
                            setContextReviewModal({ ...contextReviewModal, isOpen: false });
                            // Clean up job
                            if (contextJobId) {
                                removeJob(contextJobId);
                                setContextJobId(null);
                            }
                        }}
                        onDelete={() => {
                            if (contextJobId) {
                                removeJob(contextJobId);
                                setContextJobId(null);
                            }
                        }}
                    />
                )}
                {translationModal.isOpen && (
                    <TranslationModal
                        isOpen={translationModal.isOpen}
                        onClose={() => setTranslationModal({ ...translationModal, isOpen: false })}
                        onConfirm={() => handleTranslationConfirm(false)}
                        type={translationModal.type}
                        target={translationModal.target}
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
                {fileSelectionModal.isOpen && (
                    <FileSelectionModal
                        isOpen={fileSelectionModal.isOpen}
                        onClose={() => setFileSelectionModal({ isOpen: false, type: null, action: null })}
                        episodes={episodes}
                        onConfirm={(selectedFiles) => handleAIAction(fileSelectionModal.type, fileSelectionModal.action, selectedFiles)}
                        title={`Select Episodes for ${fileSelectionModal.type === 'context' ? 'Context' : 'Glossary'} ${fileSelectionModal.action === 'create' ? 'Creation' : 'Enhancement'}`}
                    />
                )}
                {importModal.isOpen && (
                    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
                        <motion.div
                            initial={{ opacity: 0, scale: 0.95 }}
                            animate={{ opacity: 1, scale: 1 }}
                            exit={{ opacity: 0, scale: 0.95 }}
                            className="bg-white dark:bg-gray-800 rounded-xl shadow-xl max-w-md w-full overflow-hidden"
                        >
                            <div className="p-6 border-b border-gray-200 dark:border-gray-700">
                                <h3 className="text-xl font-bold text-gray-900 dark:text-white">Import {importModal.type === 'context' ? 'Context' : 'Glossary'}</h3>
                                <p className="text-sm text-gray-500 mt-1">Select a project to import from</p>
                            </div>
                            <div className="max-h-96 overflow-y-auto p-2">
                                {availableProjects.map(p => (
                                    <button
                                        key={p}
                                        onClick={() => handleImport(p)}
                                        className="w-full flex items-center gap-3 p-3 hover:bg-gray-50 dark:hover:bg-gray-700/50 rounded-lg transition-colors text-left"
                                    >
                                        <Folder size={20} className="text-blue-500" />
                                        <span className="text-gray-700 dark:text-gray-200">{p}</span>
                                    </button>
                                ))}
                            </div>
                            <div className="p-6 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50 flex justify-end">
                                <button
                                    onClick={() => setImportModal({ isOpen: false, type: null })}
                                    className="px-4 py-2 text-gray-600 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
                                >
                                    Cancel
                                </button>
                            </div>
                        </motion.div>
                    </div>
                )}
            </AnimatePresence>

            {/* Upload Modal */}
            <AnimatePresence>
                {isUploading && (
                    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
                        <motion.div
                            initial={{ opacity: 0, scale: 0.95 }}
                            animate={{ opacity: 1, scale: 1 }}
                            exit={{ opacity: 0, scale: 0.95 }}
                            className="bg-white dark:bg-gray-800 rounded-xl shadow-xl max-w-md w-full p-6"
                        >
                            <h3 className="text-xl font-bold text-gray-900 dark:text-white mb-4">Upload {isMovie ? 'File' : 'Episode'}</h3>
                            {/* Simplified Upload Form for brevity */}
                            <input type="file" onChange={(e) => setFile(e.target.files)} multiple className="block w-full mb-4 text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100" />
                            <div className="flex justify-end gap-2">
                                <button onClick={() => setIsUploading(false)} className="px-4 py-2 text-gray-600">Cancel</button>
                                <button onClick={async () => {
                                    if (!file) return;
                                    // Basic upload logic
                                    try {
                                        const files = Array.from(file);
                                        for (const f of files) {
                                            let epName = f.name || 'unnamed';
                                            const match = f.name?.match(/[Ss]\d{2}[Ee]\d{2}/);
                                            if (match) epName = match[0].toUpperCase();
                                            await api.uploadEpisode(projectName, epName, f);
                                        }
                                        setIsUploading(false);
                                        loadData();
                                    } catch (e) { alert("Upload failed"); }
                                }} className="bg-indigo-600 text-white px-4 py-2 rounded-lg">Upload</button>
                            </div>
                        </motion.div>
                    </div>
                )}
            </AnimatePresence>

        </div >
    );
};

export default ProjectDetail;
