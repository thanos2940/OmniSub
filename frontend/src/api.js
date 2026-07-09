import axios from 'axios';

// Resolve the API base at runtime:
//  1. VITE_API_URL build-time override, else
//  2. relative ('') in production so the app talks to its own origin (reverse proxy), else
//  3. localhost:8000 in dev.
const API_URL =
    import.meta.env.VITE_API_URL ??
    (import.meta.env.DEV ? 'http://localhost:8000' : '');

export const api = {
    // Projects
    getProjects: () => axios.get(`${API_URL}/projects`),
    createProject: (name, targetLanguage, parentProject = null, type = 'show') => axios.post(`${API_URL}/projects`, { name, target_language: targetLanguage, parent_project: parentProject, type }),
    getProject: (name) => axios.get(`${API_URL}/projects/${encodeURIComponent(name)}`),
    getProjectTokenSummary: (name) => axios.get(`${API_URL}/projects/${encodeURIComponent(name)}/token-summary`),
    testTranslation: (name, data) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/test-translation`, data),
    updateProject: (name, data, globalSave = false) => axios.put(`${API_URL}/projects/${encodeURIComponent(name)}`, data, { params: { global_save: globalSave } }),
    syncProject: (projectName) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/sync`),
    importProjectData: (projectName, sourceProject, importGlossary = true, importContext = true) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/import`, { source_project: sourceProject, import_glossary: importGlossary, import_context: importContext }),
    getSyncCandidates: (project) => axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/sync-candidates`),
    syncImport: (project, data) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/sync-import`, data),
    checkProjectMissing: (name) => axios.get(`${API_URL}/projects/${encodeURIComponent(name)}/check-missing`),
    translateProjectMissing: (name) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/translate-missing`),

    // AI Enhancement
    enhanceContext: (name, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/context/enhance`, null, { params: { model } }),
    createContext: (name, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/context/create`, null, { params: { model } }),
    deleteContext: (name) => axios.delete(`${API_URL}/projects/${encodeURIComponent(name)}/context`),
    enhanceGlossary: (name, data, model, enableResearch = false) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/glossary/enhance`, data, { params: { model, enable_research: enableResearch } }),
    createGlossary: (name, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/glossary/create`, null, { params: { model } }),
    deleteGlossary: (name) => axios.delete(`${API_URL}/projects/${encodeURIComponent(name)}/glossary`),

    // Episodes
    getEpisodes: (projectName) => axios.get(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes`),
    getEpisode: (projectName, episodeName) => axios.get(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}`),
    saveEpisode: (projectName, episodeName, data) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/save`, { data }),
    updateEpisodeMetadata: (projectName, episodeName, metadata) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/metadata`, { metadata }),
    deleteEpisode: (projectName, episodeName) => axios.delete(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}`),
    deleteLines: (projectName, episodeName, indexes) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/delete-lines`, { indexes }),
    applyCommonFixes: (projectName, episodeName, track = 'source', lang = null) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/apply-fixes`, { track, lang }),
    batchApplyCommonFixes: (projectName, episodeNames, track = 'source', lang = null) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/batch-apply-fixes`, { episode_names: episodeNames, track, lang }),
    browseExecutable: () => axios.post(`${API_URL}/settings/browse-executable`),

    // Actions
    uploadEpisode: (projectName, episodeName, file) => {
        const formData = new FormData();
        formData.append('file', file);
        return axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/upload`, formData, {
            headers: { 'Content-Type': 'multipart/form-data' }
        });
    },
    scanEpisode: (projectName, episodeName, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/scan`, { model }),
    translateEpisode: (projectName, episodeName, model, enhanceGlossary = false, force = false, translationType = 'saved') => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/translate`, { model, enhance_glossary: enhanceGlossary, force, translation_type: translationType }),
    clearTranslation: (projectName, episodeName) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/clear`),
    retranslateEpisode: (projectName, episodeName, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/retranslate`, { model }),
    mergeTranslation: (projectName, episodeName, selectedLines) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/merge`, { selected_lines: selectedLines }),

    // Batch Operations
    batchTranslate: (projectName, episodeNames, model, enhanceGlossary = false, force = false, translationType = 'saved') => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/batch-translate`, { episode_names: episodeNames, model, enhance_glossary: enhanceGlossary, force, translation_type: translationType }),
    batchDownload: async (projectName, episodeNames = null) => {
        const response = await axios.post(
            `${API_URL}/projects/${encodeURIComponent(projectName)}/batch-download`,
            { episodes: episodeNames },
            { responseType: 'blob' }
        );

        const url = window.URL.createObjectURL(new Blob([response.data]));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `${projectName}_export.zip`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    },

    exportToPath: (projectName, episodeName) =>
        axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/export-to-path`),

    batchExportToPath: (projectName, episodeNames = null) =>
        axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/batch-export-to-path`, { episodes: episodeNames }),

    // Jobs
    getJob: (jobId) => axios.get(`${API_URL}/jobs/${jobId}`),
    cancelJob: (jobId) => axios.post(`${API_URL}/jobs/${jobId}/cancel`),

    // Simplified Pipeline
    startSimplePipeline: (name, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/simple-pipeline/start`, { model }),
    confirmPipelineContext: (name, jobId, contextGuide) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/simple-pipeline/${jobId}/confirm-context`, { context_guide: contextGuide }),
    confirmPipelineGlossary: (name, jobId, glossary) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/simple-pipeline/${jobId}/confirm-glossary`, { glossary }),
    
    // Pipeline
    startPipeline: (projectName, options = {}) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/pipeline/start`, options),
    continuePipeline: (projectName, jobId) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/pipeline/${jobId}/continue`),

    // Downloads
    downloadEpisode: async (projectName, episodeName) => {
        const response = await axios.get(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/download`, { responseType: 'blob' });
        // Prefer the server-provided filename (correct language tag AND extension, e.g.
        // .el.ass for ASS sources); a blob URL drops Content-Disposition so we parse it.
        let filename = `${episodeName}.srt`;
        const cd = response.headers['content-disposition'] || response.headers['Content-Disposition'];
        if (cd) {
            const m = /filename\*?=(?:UTF-8'')?"?([^\";]+)"?/i.exec(cd);
            if (m && m[1]) {
                try { filename = decodeURIComponent(m[1]); } catch { filename = m[1]; }
            }
        }
        const url = window.URL.createObjectURL(new Blob([response.data]));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', filename);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    },

    // Global Settings
    getSettings: () => axios.get(`${API_URL}/settings`),
    updateSettings: (settings) => axios.post(`${API_URL}/settings`, settings),

    // API Key Management
    getApiKeyStatus: () => axios.get(`${API_URL}/api/config/api-key`),
    setApiKey: (apiKey) => axios.post(`${API_URL}/api/config/api-key`, { api_key: apiKey }),
    deleteApiKey: () => axios.delete(`${API_URL}/api/config/api-key`),

    // Model Registry (unified: Gemini + local discovery)
    fetchAllModels: (baseUrl = null) => axios.get(`${API_URL}/api/models`, { params: { base_url: baseUrl } }),
    // Legacy local-only endpoint (kept for compatibility)
    fetchLocalModels: (baseUrl = null) => axios.get(`${API_URL}/api/config/models/local`, { params: { base_url: baseUrl } }),

    // Auto-translate (fire-and-forget, no review gates)
    autoTranslate: (projectName, options = {}) =>
        axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/auto-translate`, options),

    // Rate Limiting
    getRateLimitStats: () => axios.get(`${API_URL}/rate-limit/stats`),
    updateRateLimitConfig: (config) => axios.post(`${API_URL}/rate-limit/configure`, config),
    estimateBatchTranslate: (projectName, episodeNames) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/batch-translate/estimate`, { episode_names: episodeNames }),

    // Translation Memory
    getTmStats: (project) => axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/tm/stats`),
    clearTm: (project) => axios.delete(`${API_URL}/projects/${encodeURIComponent(project)}/tm`),
    // /edit-stats returns { total_records, user_edited_records, edit_ratio }
    getEditStats: (project) => axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/edit-stats`),

    // Character Profiles
    getCharacters: (project) => axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/characters`),
    updateCharacter: (project, name, data, globalSave = false) => axios.put(`${API_URL}/projects/${encodeURIComponent(project)}/characters/${encodeURIComponent(name)}`, data, { params: { global_save: globalSave } }),
    deleteCharacter: (project, name) => axios.delete(`${API_URL}/projects/${encodeURIComponent(project)}/characters/${encodeURIComponent(name)}`),
    // Returns { job_id } — use getJob() to poll for completion
    generateCharacters: (project, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/characters/generate`, null, { params: { model } }),

    // Review Queue
    // GET returns { items: [...], count: N }
    getReviewQueue: (project) => axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/review-queue`),
    getGlobalReviewQueue: () => axios.get(`${API_URL}/projects/review-queue`),
    // POST /review-queue/resolve?episode_name=X&line_index=N&translated_text=...
    resolveReviewItem: (project, episode, index, translatedText) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/review-queue/resolve`, null, { params: { episode_name: episode, line_index: index, ...(translatedText != null ? { translated_text: translatedText } : {}) } }),
    resolveAllReviewItems: (project) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/review-queue/resolve-all`),
    // POST /review-queue/save-line?episode_name=X&line_index=N&translated_text=...
    saveReviewLine: (project, episode, index, translatedText) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/review-queue/save-line`, null, { params: { episode_name: episode, line_index: index, translated_text: translatedText } }),

    // Episode Summaries
    // GET /summaries returns { episode_name: summary_text, ... }
    getSummaries: (project) => axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/summaries`),
    // PUT /summaries/{episode} expects { summary: "text" }
    updateSummary: (project, episode, summary) => axios.put(`${API_URL}/projects/${encodeURIComponent(project)}/summaries/${encodeURIComponent(episode)}`, { summary }),
    deleteSummary: (project, episode) => axios.delete(`${API_URL}/projects/${encodeURIComponent(project)}/summaries/${encodeURIComponent(episode)}`),
    // POST /summaries/{episode}/generate?model=X — returns { job_id }
    generateSummary: (project, episode, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/summaries/${encodeURIComponent(episode)}/generate`, null, { params: { model } }),

    // Arr Integration (Sonarr & Radarr)
    getArrStatus: () => axios.get(`${API_URL}/integrations/arr/status`),
    testSonarr: (settings) => axios.post(`${API_URL}/integrations/sonarr/test`, settings),
    testRadarr: (settings) => axios.post(`${API_URL}/integrations/radarr/test`, settings),
    testPath: (remotePath, pathMappings) => axios.post(`${API_URL}/integrations/arr/test-path`, { remote_path: remotePath, path_mappings: pathMappings }),
    syncSonarr: () => axios.post(`${API_URL}/integrations/sonarr/sync`),
    syncRadarr: () => axios.post(`${API_URL}/integrations/radarr/sync`),
    syncArrAll: () => axios.post(`${API_URL}/integrations/arr/sync`),

    // Arr Library & Sync
    getArrLibrary: () => axios.get(`${API_URL}/integrations/arr/library`),
    disableLibraryEntry: (project) => axios.post(`${API_URL}/integrations/arr/library/${encodeURIComponent(project)}/disable`),
    enableLibraryEntry: (project) => axios.post(`${API_URL}/integrations/arr/library/${encodeURIComponent(project)}/enable`),
    
    // Background Queue
    getQueue: () => axios.get(`${API_URL}/api/queue`),
    retryQueueItem: (itemId) => axios.post(`${API_URL}/api/queue/${itemId}/retry`),
    cancelQueueItem: (itemId) => axios.post(`${API_URL}/api/queue/${itemId}/cancel`),
    prioritizeQueueItem: (itemId) => axios.post(`${API_URL}/api/queue/${itemId}/prioritize`),
    getQueueItemLogs: (itemId) => axios.get(`${API_URL}/api/queue/${itemId}/logs`),
    enqueueMissing: (project) => axios.post(`${API_URL}/integrations/arr/library/${encodeURIComponent(project)}/enqueue-missing`),
    enqueueAllMissing: () => axios.post(`${API_URL}/api/queue/enqueue-missing`),
    pauseQueue: () => axios.post(`${API_URL}/api/queue/pause`),
    resumeQueue: () => axios.post(`${API_URL}/api/queue/resume`),
    checkDailyLimit: () => axios.post(`${API_URL}/api/queue/check-daily-limit`),
    enqueueEpisodes: (project, episodes) => axios.post(`${API_URL}/api/queue/enqueue`, { project, episodes }),
    clearQueueHistory: () => axios.delete(`${API_URL}/api/queue/clear`),

    // Dashboard (Plan 08) & Health (Plan 16)
    getDashboard: () => axios.get(`${API_URL}/api/dashboard`),
    getFullHealth: () => axios.get(`${API_URL}/api/health/full`),

    // Consistency (Plan 12)
    getConsistency: (project) => axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/consistency`),
    fixConsistency: (project, terms) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/consistency/fix`, { terms }),

    // Blacklist (Plan 14)
    getBlacklist: (project) => axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/blacklist`),
    addBlacklist: (project, episode, lineIndex, badTarget, reason) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/episodes/${encodeURIComponent(episode)}/blacklist`, { line_index: lineIndex, bad_target: badTarget, reason }),
    clearBlacklist: (project) => axios.delete(`${API_URL}/projects/${encodeURIComponent(project)}/blacklist`),

    // Source subtitle replace/search (Plan 17)
    uploadSourceSub: (project, episode, file) => {
        const fd = new FormData();
        fd.append('file', file);
        return axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/episodes/${encodeURIComponent(episode)}/source/upload`, fd, { headers: { 'Content-Type': 'multipart/form-data' } });
    },
    
    // Feedback and Conformance (Plan 28/29)
    getFeedbackSuggestions: (project, minFreq = 2) => axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/feedback/suggestions`, { params: { min_freq: minFreq } }),
    acceptFeedbackSuggestion: (project, data) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/feedback/suggestions/accept`, data),
    measureConformance: (project, text, timecode) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/conformance/measure`, { text, timecode }),
    blacklistAndRetry: (project, episode, data) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/episodes/${encodeURIComponent(episode)}/blacklist-retry`, data),
};

