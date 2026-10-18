import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../api';

export const useProjectDetail = ({ projectName, addJob, activeJobs, toast }) => {
    const [project, setProject] = useState(null);
    const [episodes, setEpisodes] = useState([]);
    const [subprojects, setSubprojects] = useState([]);
    const [tokenSummary, setTokenSummary] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const isMounted = useRef(true);
    const completedJobsRef = useRef(new Set());
    const aiActionInFlightRef = useRef({});
    const isAutoTranslatingRef = useRef(false);
    const isBatchExtractingRef = useRef(false);
    const isBatchTranslatingRef = useRef(false);
    const translatingEpisodesRef = useRef({});
    const isBatchDownloadingRef = useRef(false);
    const isBatchExportingRef = useRef(false);
    const isBatchDeletingRef = useRef(false);

    useEffect(() => {
        isMounted.current = true;
        return () => {
            isMounted.current = false;
        };
    }, []);

    const loadData = useCallback(async (silent = false) => {
        if (!projectName) return;
        if (!silent) {
            setLoading(true);
        }
        setError(null);
        try {
            const [projRes, tokenRes, epRes] = await Promise.all([
                api.getProject(projectName),
                api.getProjectTokenSummary(projectName).catch(() => ({ data: null })),
                api.getEpisodes(projectName).catch(() => ({ data: [] }))
            ]);

            if (!isMounted.current) return;

            const projData = projRes.data;
            setProject(projData);
            setTokenSummary(tokenRes.data);

            if (projData.type === 'parent') {
                const allProjRes = await api.getProjects();
                if (isMounted.current) {
                    setSubprojects((allProjRes.data || []).filter(p => p && p.parent_project === projectName));
                }
            } else {
                setEpisodes(epRes.data || []);
            }
        } catch (err) {
            console.error("Failed to load project data", err);
            if (isMounted.current) {
                setError("Failed to load project. It might not exist.");
            }
        } finally {
            if (isMounted.current && !silent) {
                setLoading(false);
            }
        }
    }, [projectName]);

    useEffect(() => {
        loadData(false);
    }, [loadData]);

    // Auto-reload project data when relevant background jobs complete (silent reload to prevent flicker)
    useEffect(() => {
        if (!activeJobs) return;
        let shouldReload = false;
        Object.values(activeJobs).forEach(job => {
            if (!job || !job.id) return;
            if (job.status === 'completed' && !completedJobsRef.current.has(job.id)) {
                completedJobsRef.current.add(job.id);
                if (
                    job.id.includes(projectName) ||
                    job.description?.includes(projectName) ||
                    job.project_name === projectName ||
                    [
                        'create_context', 'enhance_context', 'create_glossary',
                        'enhance_glossary', 'batch_extract_embedded', 'extract_embedded',
                        'auto_translate', 'batch_translate', 'translate_episode'
                    ].includes(job.type)
                ) {
                    shouldReload = true;
                }
            }
        });
        if (shouldReload) {
            loadData(true);
        }
    }, [activeJobs, projectName, loadData]);



    const handleSaveSettings = useCallback(async (newSettings, newParentProject) => {
        try {
            const payload = { settings: newSettings };
            if (newParentProject !== undefined) {
                payload.parent_project = newParentProject;
            }
            await api.updateProject(projectName, payload);
            if (toast?.success) toast.success("Project settings saved!");
            loadData();
        } catch (e) {
            console.error("Failed to save project settings", e);
            if (toast?.error) toast.error("Failed to save project settings.");
        }
    }, [projectName, loadData, toast]);

    const handleUpload = useCallback(async (e) => {
        const files = Array.from(e.target.files || []);
        if (files.length === 0) return;

        let successCount = 0;
        for (const file of files) {
            const epName = file.name.replace(/\.[^/.]+$/, '');
            try {
                await api.uploadEpisode(projectName, epName, file);
                successCount++;
            } catch (err) {
                console.error(`Failed to upload ${file.name}`, err);
                if (toast?.error) toast.error(`Failed to upload ${file.name}`);
            }
        }

        if (successCount > 0) {
            if (toast?.success) toast.success(`Uploaded ${successCount} episode file(s).`);
            loadData();
        }
        e.target.value = null;
    }, [projectName, loadData, toast]);

    const handleDeleteEpisode = useCallback(async (epName, deleteDiskFile) => {
        let shouldDeleteDisk;
        if (typeof deleteDiskFile === 'boolean') {
            shouldDeleteDisk = deleteDiskFile;
        } else {
            if (!window.confirm(`Delete episode "${epName}"? This will remove all subtitles for this episode.`)) return;
            shouldDeleteDisk = window.confirm("Also delete the detected .el.srt / .el.ass file from disk?");
        }
        try {
            await api.deleteEpisode(projectName, epName, shouldDeleteDisk);
            if (toast?.success) toast.success(`Deleted episode "${epName}".`);
            loadData();
        } catch (err) {
            console.error("Failed to delete episode", err);
            if (toast?.error) toast.error("Failed to delete episode.");
        }
    }, [projectName, loadData, toast]);

    const handleBatchDownload = useCallback(async (selectedList = null) => {
        if (isBatchDownloadingRef.current) {
            if (toast?.warning) toast.warning("Subtitle ZIP export is already in preparation.");
            return;
        }
        isBatchDownloadingRef.current = true;

        try {
            if (toast?.info) toast.info("Preparing subtitle ZIP export...");
            await api.batchDownload(projectName, selectedList);
            if (toast?.success) toast.success("Download started!");
        } catch (e) {
            console.error("Batch download failed", e);
            if (toast?.error) toast.error("Failed to download subtitles.");
        } finally {
            isBatchDownloadingRef.current = false;
        }
    }, [projectName, toast]);

    const handleBatchExportToPath = useCallback(async (selectedList = null) => {
        if (isBatchExportingRef.current) {
            if (toast?.warning) toast.warning("Disk export is already running.");
            return;
        }
        isBatchExportingRef.current = true;

        try {
            await api.batchExportToPath(projectName, selectedList);
            if (toast?.success) toast.success("Subtitles exported to media disk paths!");
        } catch (e) {
            console.error("Batch export to path failed", e);
            if (toast?.error) toast.error("Failed to export subtitles to disk.");
        } finally {
            isBatchExportingRef.current = false;
        }
    }, [projectName, toast]);

    const handleAutoTranslate = useCallback(async (options = {}) => {
        if (isAutoTranslatingRef.current) {
            if (toast?.warning) toast.warning("Auto-translation is already in progress.");
            return null;
        }
        isAutoTranslatingRef.current = true;

        try {
            const res = await api.autoTranslate(projectName, options);
            const jobId = res.data?.job_id;
            if (jobId && addJob) {
                const singleEp = options.episode_names?.length === 1 ? options.episode_names[0] : null;
                addJob(jobId, 'auto_translate', `Auto-translating ${projectName}`, {
                    projectId: projectName,
                    episodeName: singleEp,
                    link: `/project/${encodeURIComponent(projectName)}`
                });
            }
            if (toast?.success) toast.success("Auto-translate batch enqueued!");
            return res.data;
        } catch (e) {
            console.error("Auto translate failed", e);
            if (toast?.error) toast.error("Failed to start auto-translate.");
            return null;
        } finally {
            isAutoTranslatingRef.current = false;
        }
    }, [projectName, addJob, toast]);

    const handleBatchExtractEmbedded = useCallback(async (episodeNames = null, force = false) => {
        if (isBatchExtractingRef.current) {
            if (toast?.warning) toast.warning("Batch subtitle extraction is already in progress.");
            return null;
        }
        isBatchExtractingRef.current = true;

        try {
            const res = await api.batchExtractEmbedded(projectName, {
                episode_names: episodeNames || undefined,
                force: force,
                migrate_srt: true,
            });
            const jid = res.data?.job_id;
            if (jid && addJob) {
                addJob(jid, 'batch_extract_embedded', `Batch Extract Embedded Subs: ${projectName}`, {
                    projectId: projectName,
                    link: `/project/${encodeURIComponent(projectName)}`
                });
            }
            if (toast?.success) toast.success("Batch extraction job queued.");
            return res.data;
        } catch (err) {
            console.error("Failed to start batch extraction", err);
            const errMsg = err.response?.data?.detail || "Failed to start batch extraction.";
            if (toast?.error) toast.error(errMsg);
            return null;
        } finally {
            isBatchExtractingRef.current = false;
        }
    }, [projectName, addJob, toast]);

    const handleBatchTranslate = useCallback(async (episodesListOrOptions, maybeOptions = {}) => {
        if (isBatchTranslatingRef.current) {
            if (toast?.warning) toast.warning("Batch translation is already in progress.");
            return null;
        }
        isBatchTranslatingRef.current = true;

        try {
            let epList = [];
            let options = {};
            if (Array.isArray(episodesListOrOptions)) {
                epList = episodesListOrOptions;
                options = maybeOptions || {};
            } else if (episodesListOrOptions && typeof episodesListOrOptions === 'object') {
                options = episodesListOrOptions;
                epList = options.episode_names || options.episodes || [];
            }

            const model = options.model || options.model_name || undefined;
            const enhanceGlossary = !!(options.enhance_glossary ?? options.enhanceGlossary);
            const force = !!options.force;
            const translationType = options.translation_type || options.translationType || 'saved';

            const res = await api.batchTranslate(projectName, {
                episode_names: epList,
                model,
                enhance_glossary: enhanceGlossary,
                force,
                translation_type: translationType,
            });
            const jobId = res.data?.job_id;
            const subJobIds = res.data?.sub_jobs || res.data?.item_ids || [];
            if (jobId && addJob) {
                addJob(jobId, 'batch_translate', `Batch Translating ${projectName} (${epList.length} episodes)`, {
                    projectId: projectName,
                    episodeNames: epList,
                    sub_job_ids: subJobIds,
                    link: `/project/${encodeURIComponent(projectName)}`
                });
            }
            if (toast?.success) toast.success(`Batch translation enqueued for ${epList.length} episodes.`);
            return res.data;
        } catch (e) {
            console.error("Batch translate failed", e);
            const errMsg = e.response?.data?.detail || "Failed to start batch translation.";
            if (toast?.error) toast.error(errMsg);
            return null;
        } finally {
            isBatchTranslatingRef.current = false;
        }
    }, [projectName, addJob, toast]);

    const handleTranslateEpisode = useCallback(async (epName, optionsOrModel = {}) => {
        if (translatingEpisodesRef.current[epName]) {
            if (toast?.warning) toast.warning(`Translation for "${epName}" is already in progress.`);
            return null;
        }
        translatingEpisodesRef.current[epName] = true;

        try {
            let options = {};
            if (optionsOrModel && typeof optionsOrModel === 'object') {
                options = optionsOrModel;
            } else if (typeof optionsOrModel === 'string') {
                options = { model: optionsOrModel };
            }

            const model = options.model || options.model_name || undefined;
            const enhanceGlossary = !!(options.enhance_glossary ?? options.enhanceGlossary);
            const force = !!options.force;
            const translationType = options.translation_type || options.translationType || 'saved';

            const res = await api.translateEpisode(projectName, epName, {
                model,
                enhance_glossary: enhanceGlossary,
                force,
                translation_type: translationType,
            });
            const jobId = res.data?.job_id;
            if (jobId && addJob) {
                addJob(jobId, 'translation', `Translating ${projectName} - ${epName}`, {
                    projectId: projectName,
                    episodeName: epName,
                    link: `/project/${encodeURIComponent(projectName)}`
                });
            }
            if (toast?.success) toast.success(`Translation enqueued for ${epName}`);
            return res.data;
        } catch (e) {
            console.error(`Translation for "${epName}" failed`, e);
            const errMsg = e.response?.data?.detail || `Failed to start translation for "${epName}".`;
            if (toast?.error) toast.error(errMsg);
            return null;
        } finally {
            delete translatingEpisodesRef.current[epName];
        }
    }, [projectName, addJob, toast]);

    const handleBatchDelete = useCallback(async (selectedList, deleteDiskFile) => {
        if (!selectedList || (Array.isArray(selectedList) && selectedList.length === 0) || (selectedList.size !== undefined && selectedList.size === 0)) return false;
        if (isBatchDeletingRef.current) {
            if (toast?.warning) toast.warning("Batch deletion is already in progress.");
            return false;
        }

        const epList = Array.isArray(selectedList) ? selectedList : Array.from(selectedList);

        if (typeof deleteDiskFile !== 'boolean') {
            if (!window.confirm(`Delete subtitles for ${epList.length} selected episode(s)? This will remove translated subtitles and cannot be undone.`)) {
                return false;
            }
        }
        const shouldDeleteDisk = typeof deleteDiskFile === 'boolean'
            ? deleteDiskFile
            : window.confirm("Also delete detected .el.srt / .el.ass files from disk for selected episodes?");

        isBatchDeletingRef.current = true;

        try {
            let successCount = 0;
            let failCount = 0;

            await Promise.all(
                epList.map(async (epName) => {
                    try {
                        await api.deleteEpisode(projectName, epName, shouldDeleteDisk);
                        successCount++;
                    } catch (err) {
                        console.error(`Failed to delete episode "${epName}"`, err);
                        failCount++;
                    }
                })
            );

            if (successCount > 0) {
                if (toast?.success) toast.success(`Deleted subtitles for ${successCount} episode(s).`);
            }
            if (failCount > 0) {
                if (toast?.error) toast.error(`Failed to delete ${failCount} episode(s).`);
            }

            await loadData(true);
            return true;
        } catch (err) {
            console.error("Batch delete error", err);
            if (toast?.error) toast.error("Failed to complete batch delete.");
            return false;
        } finally {
            isBatchDeletingRef.current = false;
        }
    }, [projectName, loadData, toast]);

    const handleAiAction = useCallback(async (actionType, model = null, extraParams = {}) => {
        if (aiActionInFlightRef.current[actionType]) {
            if (toast?.warning) toast.warning(`AI task '${actionType}' is already in progress.`);
            return null;
        }
        aiActionInFlightRef.current[actionType] = true;

        const projectLink = `/project/${encodeURIComponent(projectName)}`;
        try {
            let res;
            if (actionType === 'enhance_context') {
                res = await api.enhanceContext(projectName, model);
                if (res.data?.job_id && addJob) {
                    addJob(res.data.job_id, 'enhance_context', `Enhancing Context Guide for ${projectName}`, {
                        projectId: projectName,
                        link: projectLink
                    });
                }
            } else if (actionType === 'create_context') {
                res = await api.createContext(projectName, model);
                if (res.data?.job_id && addJob) {
                    addJob(res.data.job_id, 'create_context', `Generating Context Guide for ${projectName}`, {
                        projectId: projectName,
                        link: projectLink
                    });
                }
            } else if (actionType === 'enhance_glossary') {
                res = await api.enhanceGlossary(projectName, extraParams.data || {}, model, extraParams.enableResearch || false);
                if (res.data?.job_id && addJob) {
                    addJob(res.data.job_id, 'enhance_glossary', `Enhancing Glossary for ${projectName}`, {
                        projectId: projectName,
                        link: projectLink
                    });
                }
            } else if (actionType === 'create_glossary') {
                res = await api.createGlossary(projectName, model);
                if (res.data?.job_id && addJob) {
                    addJob(res.data.job_id, 'create_glossary', `Generating Glossary for ${projectName}`, {
                        projectId: projectName,
                        link: projectLink
                    });
                }
            }
            if (toast?.success) toast.success("AI Task queued.");
            return res?.data;
        } catch (e) {
            console.error(`AI Action ${actionType} failed`, e);
            if (toast?.error) toast.error(`Failed to trigger AI ${actionType}.`);
            return null;
        } finally {
            aiActionInFlightRef.current[actionType] = false;
        }
    }, [projectName, addJob, toast]);

    return {
        project,
        setProject,
        episodes,
        setEpisodes,
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
    };
};
