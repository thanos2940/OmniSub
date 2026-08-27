import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api } from '../api';
import EditorView from './EditorView';
import { ArrowLeft, Sparkles, Languages, RefreshCw, RotateCcw, Trash2, X, Wand2, BookOpen } from 'lucide-react';
import { useJobs } from '../context/JobContext';
import { useToast } from '../context/ToastContext';
import { motion, AnimatePresence } from 'framer-motion';
import TermHarvestModal from './TermHarvestModal';

const EpisodeView = () => {
    const { projectName, episodeName } = useParams();
    const { addJob, activeJobs } = useJobs();
    const toast = useToast();
    const [data, setData] = useState([]);
    const [project, setProject] = useState(null);
    const [glossary, setGlossary] = useState({ terms: [] });
    const [translationModalOpen, setTranslationModalOpen] = useState(false);
    const [selectedTranslationType, setSelectedTranslationType] = useState('saved');
    const [loading, setLoading] = useState(true);
    const [processing, setProcessing] = useState(false);
    const [originalFilename, setOriginalFilename] = useState(null);
    const [episodeMetadata, setEpisodeMetadata] = useState(null);
    const [dirty, setDirty] = useState(false);
    const [harvestModalOpen, setHarvestModalOpen] = useState(false);
    const dirtyRef = React.useRef(false);
    useEffect(() => { dirtyRef.current = dirty; }, [dirty]);
    const handledJobsRef = React.useRef(new Set());
    // ETag from the last successful GET (docs/PLAN_structural_polish.md, Part A)
    // — the 3s live-view poll below sends it back as If-None-Match so an
    // unchanged episode short-circuits to a 304 instead of re-transferring the
    // whole (potentially thousands-of-lines) payload.
    const etagRef = React.useRef(null);

    const [selectedLang, setSelectedLang] = useState('');

    const primaryLang = project?.target_language || "English";
    const extraLangs = project?.target_languages || [];
    const targetLanguages = Array.from(new Set([primaryLang, ...extraLangs])).filter(Boolean);

    useEffect(() => {
        if (primaryLang) {
            setSelectedLang(primaryLang);
        }
    }, [primaryLang]);

    const LANG_MAP = {
        "Greek": "el", "English": "en", "Spanish": "es", "French": "fr",
        "German": "de", "Italian": "it", "Portuguese": "pt", "Russian": "ru",
        "Japanese": "ja", "Korean": "ko", "Chinese": "zh"
    };
    const getLangCode = (langName) => LANG_MAP[langName] || "en";

    const activeLangCode = getLangCode(selectedLang || primaryLang);
    const primaryLangCode = getLangCode(primaryLang);

    const editorData = data.map(item => ({
        ...item,
        translated: item.translations?.[activeLangCode] || (activeLangCode === primaryLangCode ? item.translated : '') || ''
    }));

    const handleEditorDataUpdate = (updatedEditorData) => {
        const newData = data.map((item, idx) => {
            const updatedItem = updatedEditorData[idx];
            const translations = { ...(item.translations || {}) };
            translations[activeLangCode] = updatedItem.translated;

            const mapped = {
                ...item,
                translations,
                is_edited: updatedItem.is_edited,
                update_tm: updatedItem.update_tm,
                needs_review: updatedItem.needs_review,
                review_issues: updatedItem.review_issues,
                review_scores: updatedItem.review_scores,
            };

            if (activeLangCode === primaryLangCode) {
                mapped.translated = updatedItem.translated;
            }
            return mapped;
        });
        setData(newData);
        setDirty(true);
    };

    useEffect(() => {
        etagRef.current = null; // switching episodes — the old ETag doesn't apply
        loadData();
    }, [projectName, episodeName]);

    // Watch for completed translation jobs
    useEffect(() => {
        const jobs = Object.values(activeJobs);
        const completedTranslation = jobs.find(job =>
            job.status === 'completed' &&
            ['translate_episode', 'batch_translate', 'auto_translate', 'pipeline', 'retranslate_compare', 'retranslate_invalidated'].includes(job.type) &&
            (job.metadata?.episodeName === episodeName || job.metadata?.projectId === projectName)
        );

        if (completedTranslation && !handledJobsRef.current.has(completedTranslation.id)) {
            if (!dirtyRef.current) {
                loadData();
            } else {
                toast.info("Translation job finished. Save changes to load the updated translation.");
            }
            handledJobsRef.current.add(completedTranslation.id);
        }
    }, [activeJobs, episodeName, projectName]);

    // Live View Polling: Fetch data progressively while any translation job is running in this project
    useEffect(() => {
        let interval;
        const isTranslating = Object.values(activeJobs).some(job =>
            ['running', 'pending'].includes(job.status) &&
            ['translate_episode', 'batch_translate', 'auto_translate', 'pipeline', 'retranslate_compare', 'retranslate_invalidated'].includes(job.type) &&
            // Only poll for jobs that belong to THIS project/episode. A blanket
            // `projectId === undefined` match let an unrelated background job trigger
            // silent reloads of whatever episode happened to be open.
            (job.metadata?.projectId === projectName || job.metadata?.episodeName === episodeName)
        );

        if (isTranslating) {
            console.log("Translation job active in project, starting live-view polling...");
            // Load once immediately to catch any scenes finished before effect triggered
            // (skipped while the user has unsaved edits so polling can't clobber them)
            if (!dirtyRef.current) loadData(true);
            interval = setInterval(() => {
                if (!dirtyRef.current) loadData(true); // Silent reload
            }, 3000);
        }

        return () => {
            if (interval) clearInterval(interval);
        };
    }, [activeJobs, projectName, episodeName]);

    const loadData = async (silent = false) => {
        if (!episodeName || !projectName) {
            console.error("Missing projectName or episodeName");
            if (!silent) setLoading(false);
            return;
        }

        try {
            if (!silent) setLoading(true);
            const [epRes, projRes] = await Promise.all([
                api.getEpisode(projectName, episodeName, etagRef.current),
                api.getProject(projectName)
            ]);
            setProject(projRes.data);
            setGlossary(projRes.data.glossary || { terms: [] });
            if (epRes.status === 304) {
                // Unchanged since the last fetch — project data above may still
                // have moved, but the (expensive) episode payload didn't.
                setDirty(false);
            } else {
                etagRef.current = epRes.headers?.etag || null;
                setData(epRes.data.data);
                setOriginalFilename(epRes.data.metadata?.original_filename);
                setEpisodeMetadata(epRes.data.metadata || null);
                setDirty(false);
            }
        } catch (err) {
            console.error("Failed to load episode data", err);
        } finally {
            if (!silent) setLoading(false);
        }
    };

    const handleEnhanceGlossary = async () => {
        setProcessing(true);
        try {
            const res = await api.enhanceGlossary(projectName, { episode_names: [episodeName] });
            if (res.data?.job_id) {
                addJob(res.data.job_id, 'enhance_glossary', `AI: Enhance Glossary (${episodeName})`, { link: `/project/${encodeURIComponent(projectName)}`, projectId: projectName });
                toast.info('Enhancing glossary in the background... Review results on the project glossary tab.');
            }
        } catch (err) {
            console.error('Failed to enhance glossary', err);
            toast.error('Failed to enhance glossary');
        } finally {
            setProcessing(false);
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

    const handleTranslateClick = () => {
        setTranslationModalOpen(true);
        setSelectedTranslationType('saved');
    };

    const handleTranslate = async (type = 'saved') => {
        try {
            const settings = project?.settings || {};
            const translationModel = settings.translation_model || null;
            const res = await api.translateEpisode(projectName, episodeName, translationModel, false, false, type);
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
        setTranslationModalOpen(false);
        handleTranslate(selectedTranslationType);
    };

    const handleApplyFixes = async (track) => {
        const isSource = track === 'source';
        const label = isSource ? 'source' : `${selectedLang} translation`;
        const msg = isSource
            ? `Apply SubtitleEdit "Fix common errors" + "Split long lines" to the SOURCE subtitles?\n\n`
            + `This may change the number of lines (long lines get split into new cues) and the app re-reads `
            + `the result. Translations of unchanged lines are kept; lines created by a split are flagged for review.\n\n`
            + `Best run BEFORE translating. SubtitleEdit must be configured in Settings.`
            : `Apply SubtitleEdit "Fix common errors" + "Split long lines" to the ${selectedLang} TRANSLATION?\n\n`
            + `This may change the number of lines and the app re-reads the result, then re-exports the subtitle file.\n\n`
            + `Best run AFTER translation as a final polish. SubtitleEdit must be configured in Settings.`;
        if (!window.confirm(msg)) return;
        if (dirty && !window.confirm('You have unsaved edits that will be discarded by re-reading the fixed file. Continue?')) return;

        setProcessing(true);
        try {
            const res = await api.applyCommonFixes(projectName, episodeName, track, isSource ? null : selectedLang);
            const s = res.data?.stats || {};
            const delta = (s.after ?? 0) - (s.before ?? 0);
            const deltaMsg = delta === 0
                ? 'line count unchanged'
                : delta > 0 ? `+${delta} line${delta !== 1 ? 's' : ''}` : `${delta} line${delta !== -1 ? 's' : ''}`;
            toast.success(`Applied SubtitleEdit fixes to ${label}: ${s.before ?? '?'} → ${s.after ?? '?'} (${deltaMsg})`);
            await loadData();
        } catch (err) {
            console.error('Failed to apply SubtitleEdit fixes', err);
            toast.error(err.response?.data?.detail || 'Failed to apply SubtitleEdit fixes');
        } finally {
            setProcessing(false);
        }
    };

    const handleClear = async () => {
        if (!window.confirm(`Clear translation for ${episodeName}?`)) return;
        try {
            await api.clearTranslation(projectName, episodeName);
            toast.success(`Translation cleared`);
            loadData();
        } catch (err) {
            console.error('Failed to clear translation', err);
            toast.error('Failed to clear translation');
        }
    };

    const handleRetranslateFromScratch = async () => {
        if (!window.confirm(
            `Retranslate "${episodeName}" from scratch?\n\nThis will erase the existing translation and redo it fully with the current model and settings.`
        )) return;
        try {
            const settings = project?.settings || {};
            const translationModel = settings.translation_model || null;
            const res = await api.translateEpisode(projectName, episodeName, translationModel, false, true);
            if (res.data.job_id) {
                addJob(res.data.job_id, 'translate_episode', `Retranslating ${episodeName} from scratch`, { projectId: projectName, episodeName });
                toast.info(`Retranslation from scratch started`);
            }
        } catch (err) {
            console.error('Failed to start force retranslation', err);
            toast.error('Failed to start retranslation');
        }
    };

    const handleRetranslate = async () => {
        try {
            const settings = project?.settings || {};
            const translationModel = settings.translation_model || null;
            const res = await api.retranslateEpisode(projectName, episodeName, translationModel);
            if (res.data.job_id) {
                addJob(res.data.job_id, 'retranslate_compare', `Retranslating ${episodeName} (Review Mode)`, { projectId: projectName, episodeName });
                toast.info(`Retranslation started. You'll be notified when it's ready for review.`);
            }
        } catch (err) {
            console.error('Failed to start retranslation', err);
            toast.error('Failed to start retranslation');
        }
    };

    const handleSave = async (updatedData = null) => {
        const payload = updatedData || data;
        setProcessing(true);
        try {
            const res = await api.saveEpisode(projectName, episodeName, payload);
            const exportedPath = res.data?.exported_path;
            if (exportedPath) {
                const filename = exportedPath.split(/[\\/]/).pop();
                toast.success(`Saved & exported to media folder (${filename})`);
            } else {
                toast.success('Subtitle changes saved successfully!');
            }
            setDirty(false);
            loadData(true);
        } catch (err) {
            console.error('Failed to save episode data', err);
            toast.error('Failed to save subtitle changes');
        } finally {
            setProcessing(false);
        }
    };

    const handleUpdateGlossary = async (newGlossary, globalSave = false) => {
        try {
            await api.updateProject(projectName, { glossary: newGlossary }, globalSave);
            // Reload project data to reflect resolved inherited settings
            const projRes = await api.getProject(projectName);
            setProject(projRes.data);
            setGlossary(projRes.data.glossary || { terms: [] });
            toast.success('Glossary saved successfully!');
        } catch (err) {
            console.error('Failed to save glossary', err);
            toast.error('Failed to save glossary');
        }
    };

    const handleDeleteLines = async (indexes) => {
        setProcessing(true);
        try {
            const res = await api.deleteLines(projectName, episodeName, indexes);
            const n = res.data?.deleted ?? indexes.length;
            if (res.data?.exported_path) {
                toast.success(`Deleted ${n} line${n !== 1 ? 's' : ''} — exported subtitle updated`);
            } else {
                toast.success(`Deleted ${n} line${n !== 1 ? 's' : ''}`);
            }
            await loadData(true);
        } catch (err) {
            console.error('Failed to delete lines', err);
            toast.error('Failed to delete lines');
        } finally {
            setProcessing(false);
        }
    };

    const handleExportToPath = async () => {
        setProcessing(true);
        try {
            const res = await api.exportToPath(projectName, episodeName);
            const filename = (res.data?.path || '').split(/[\\/]/).pop();
            toast.success(filename ? `Exported ${filename} next to media file` : 'Exported next to media file');
        } catch (err) {
            console.error('Failed to export to media path', err);
            toast.error(err.response?.data?.detail || 'Failed to export to media path');
        } finally {
            setProcessing(false);
        }
    };

    const activeJob = Object.values(activeJobs).find(job =>
        job.status === 'running' &&
        job.type === 'translate_episode' &&
        job.metadata?.episodeName === episodeName
    );

    if (loading) return <div className="h-screen flex items-center justify-center text-gray-500">Loading...</div>;

    // SubtitleEdit fixes are SRT-only; disable them for ASS/SSA episodes (the backend
    // also refuses them, but hide the affordance so the intent is clear).
    const isAssFormat = ['ass', 'ssa'].includes((episodeMetadata?.original_format || episodeMetadata?.original_extension || '').toLowerCase());

    return (
        <div className="h-screen flex flex-col bg-slate-50 dark:bg-gray-900">
            {/* Header (Already updated in previous step) */}
            {/* ... */}
            <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 px-6 py-4 flex flex-col gap-2 shrink-0">
                <div className="flex justify-between items-center">
                    <div className="flex items-center gap-4">
                        <Link to={`/project/${encodeURIComponent(projectName)}`} className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">
                            <ArrowLeft size={20} />
                        </Link>
                        <div>
                            <h1 className="text-lg font-bold text-gray-900 dark:text-white">{episodeName}</h1>
                            <p className="text-sm text-gray-500 dark:text-gray-400">{project?.show_name}</p>
                        </div>
                        {targetLanguages.length > 1 && (
                            <div className="ml-4 flex items-center gap-2 bg-slate-100 dark:bg-slate-800 p-1.5 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm">
                                <span className="text-xs text-gray-400 dark:text-gray-550 font-bold px-2 uppercase tracking-wide">Language:</span>
                                <select
                                    value={selectedLang}
                                    onChange={(e) => setSelectedLang(e.target.value)}
                                    className="bg-transparent border-none outline-none text-xs font-bold text-slate-700 dark:text-slate-200 pr-8 cursor-pointer"
                                >
                                    {targetLanguages.map(lang => (
                                        <option key={lang} value={lang} className="dark:bg-slate-800">{lang}</option>
                                    ))}
                                </select>
                            </div>
                        )}
                    </div>
                    <div className="flex gap-3">
                        {editorData.some(d => d.translated) && (
                            <>
                                <button
                                    onClick={handleClear}
                                    disabled={processing || !!activeJob}
                                    className="flex items-center gap-2 px-4 py-2 text-gray-500 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors text-sm font-medium disabled:opacity-50"
                                    title="Clear all translations"
                                >
                                    <Trash2 size={18} />
                                    Clear
                                </button>
                                <button
                                    onClick={handleRetranslateFromScratch}
                                    disabled={processing || !!activeJob}
                                    className="flex items-center gap-2 px-4 py-2 text-gray-500 hover:text-orange-500 hover:bg-orange-50 dark:hover:bg-orange-900/20 rounded-lg transition-colors text-sm font-medium disabled:opacity-50"
                                    title="Retranslate from scratch (clears and redoes the translation)"
                                >
                                    <RotateCcw size={18} />
                                    From Scratch
                                </button>
                                <button
                                    onClick={handleRetranslate}
                                    disabled={processing || !!activeJob}
                                    className="flex items-center gap-2 px-4 py-2 text-gray-500 hover:text-purple-600 hover:bg-purple-50 dark:hover:bg-purple-900/20 rounded-lg transition-colors text-sm font-medium disabled:opacity-50"
                                    title="Retranslate and compare"
                                >
                                    <RefreshCw size={18} />
                                    Retranslate
                                </button>
                                <button
                                    onClick={() => handleApplyFixes('target')}
                                    disabled={processing || !!activeJob || isAssFormat}
                                    className="flex items-center gap-2 px-4 py-2 text-gray-500 hover:text-teal-600 hover:bg-teal-50 dark:hover:bg-teal-900/20 rounded-lg transition-colors text-sm font-medium disabled:opacity-50"
                                    title={isAssFormat ? "SubtitleEdit fixes aren't available for ASS/SSA subtitles (they would corrupt styling/karaoke)." : "Run SubtitleEdit 'Fix common errors' + 'Split long lines' on the current translation (re-reads the result and re-exports; line count may change). Best as a final polish."}
                                >
                                    <Wand2 size={18} />
                                    Fix Translation
                                </button>
                            </>
                        )}
                        <button
                            onClick={() => handleApplyFixes('source')}
                            disabled={processing || !!activeJob || isAssFormat}
                            className="flex items-center gap-2 px-4 py-2 text-gray-500 hover:text-teal-600 hover:bg-teal-50 dark:hover:bg-teal-900/20 rounded-lg transition-colors text-sm font-medium disabled:opacity-50"
                            title={isAssFormat ? "SubtitleEdit fixes aren't available for ASS/SSA subtitles (they would corrupt styling/karaoke)." : "Run SubtitleEdit 'Fix common errors' + 'Split long lines' on the source subtitles (re-reads the result; line count may change). Best before translating."}
                        >
                            <Wand2 size={18} />
                            Fix Source
                        </button>
                        <button
                            onClick={() => setHarvestModalOpen(true)}
                            disabled={processing || !!activeJob}
                            className="flex items-center gap-2 px-3 py-2 bg-gradient-to-r from-purple-500 to-indigo-600 hover:from-purple-600 hover:to-indigo-700 text-white rounded-lg transition-colors text-sm font-medium disabled:opacity-50 shadow-sm"
                            title="Harvest named entities and key terms from this episode"
                        >
                            <Sparkles size={16} />
                            Harvest Terms
                        </button>
                        <button
                            onClick={handleEnhanceGlossary}
                            disabled={processing || !!activeJob}
                            className="flex items-center gap-2 px-4 py-2 bg-purple-100 text-purple-700 rounded-lg hover:bg-purple-200 transition-colors text-sm font-medium disabled:opacity-50"
                        >
                            <Sparkles size={18} />
                            Enhance Glossary
                        </button>
                        <button
                            onClick={handleTranslateClick}
                            disabled={processing || !!activeJob}
                            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors text-sm font-medium disabled:opacity-50 shadow-lg shadow-indigo-500/20"
                        >
                            <Languages size={18} />
                            Translate
                        </button>
                    </div>
                </div>

                {/* Inline Progress Bar */}
                {activeJob && (
                    <div className="mt-2 space-y-1">
                        <div className="flex justify-between text-[10px] font-bold uppercase tracking-tight text-indigo-500">
                            <span>{activeJob.message}</span>
                            <span>{Math.round(activeJob.progress)}%</span>
                        </div>
                        <div className="w-full bg-indigo-100 dark:bg-indigo-900/30 rounded-full h-1.5 overflow-hidden">
                            <div
                                className="bg-indigo-600 h-full transition-all duration-700 ease-out shadow-[0_0_10px_rgba(79,70,229,0.5)]"
                                style={{ width: `${activeJob.progress}%` }}
                            />
                        </div>
                    </div>
                )}
            </header>

            {/* Editor */}
            <div className="flex-1 overflow-hidden">
                <EditorView
                    data={editorData}
                    glossary={glossary}
                    project={project}
                    projectName={projectName}
                    episodeName={episodeName}
                    activeLanguage={selectedLang || primaryLang}
                    originalFilename={originalFilename}
                    onDataUpdate={handleEditorDataUpdate}
                    onSave={handleSave}
                    onUpdateGlossary={handleUpdateGlossary}
                    onDeleteLines={handleDeleteLines}
                    onExportToPath={handleExportToPath}
                    hasMediaPath={!!(episodeMetadata?.arr_media_path || episodeMetadata?.bazarr_media_path)}
                    dirty={dirty}
                    isLoading={!!activeJob || processing}
                    onUpdateTermsInScenes={handleUpdateTermsInScenes}
                />
            </div>

            {/* Translation Options Modal */}
            <AnimatePresence>
                {translationModalOpen && (
                    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                        {/* Backdrop */}
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            onClick={() => setTranslationModalOpen(false)}
                            className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm"
                        />

                        {/* Modal Box */}
                        <motion.div
                            initial={{ opacity: 0, scale: 0.95, y: 10 }}
                            animate={{ opacity: 1, scale: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.95, y: 10 }}
                            transition={{ type: 'spring', duration: 0.3 }}
                            className="relative bg-white dark:bg-gray-800 rounded-3xl shadow-2xl border border-gray-100 dark:border-gray-700 max-w-lg w-full overflow-hidden z-10"
                        >
                            {/* Header */}
                            <div className="px-6 py-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-855/50">
                                <h3 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                    <Languages className="text-indigo-600 dark:text-indigo-400" />
                                    Translate "{episodeName}"
                                </h3>
                                <button
                                    onClick={() => setTranslationModalOpen(false)}
                                    className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-750 transition-colors"
                                >
                                    <X size={18} />
                                </button>
                            </div>

                            {/* Body */}
                            <div className="p-6 space-y-4">
                                <p className="text-sm text-gray-500 dark:text-gray-400">
                                    Choose how you would like to translate this subtitle:
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
                                    onClick={() => setTranslationModalOpen(false)}
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
            </AnimatePresence>

            <TermHarvestModal
                isOpen={harvestModalOpen}
                onClose={() => setHarvestModalOpen(false)}
                projectName={projectName}
                parentProjectName={project?.parent_project}
                episodeName={episodeName}
                onTermsAdded={async (newTerms, directToUniverse) => {
                    if (!directToUniverse) {
                        const current = glossary?.terms || [];
                        const merged = [...newTerms, ...current];
                        await api.updateProject(projectName, { glossary: { terms: merged } });
                        fetchData();
                    }
                }}
            />
        </div>
    );
};

export default EpisodeView;
