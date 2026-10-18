import axios from 'axios';

// Resolve the API base at runtime:
//  1. VITE_API_URL build-time override, else
//  2. relative ('') in production so the app talks to its own origin (reverse proxy), else
//  3. localhost:8000 in dev.
const API_URL =
    import.meta.env.VITE_API_URL ??
    (import.meta.env.DEV ? 'http://localhost:8000' : '');

// Auth (docs/PLAN_auth_security.md): every call in this file goes through the
// single global `axios` instance (verified — no other file imports axios
// directly), so registering interceptors here once covers the whole app.
// There's no dedicated `client` instance on purpose: the existing ~90 call
// sites below stay untouched.
export const AUTH_STORAGE_KEY = 'omnisub_api_key';

axios.interceptors.request.use((config) => {
    const key = localStorage.getItem(AUTH_STORAGE_KEY);
    if (key) {
        config.headers = config.headers || {};
        config.headers['X-Api-Key'] = key;
    }
    return config;
});

axios.interceptors.response.use(
    (response) => response,
    (error) => {
        if (error?.response?.status === 401) {
            window.dispatchEvent(new CustomEvent('omnisub:unauthorized'));
        }
        return Promise.reject(error);
    }
);

export const apiClient = {
    get: (url, config) => axios.get(url.startsWith('http') ? url : `${API_URL}${url.startsWith('/') ? url : '/' + url}`, config),
    post: (url, data, config) => axios.post(url.startsWith('http') ? url : `${API_URL}${url.startsWith('/') ? url : '/' + url}`, data, config),
    put: (url, data, config) => axios.put(url.startsWith('http') ? url : `${API_URL}${url.startsWith('/') ? url : '/' + url}`, data, config),
    delete: (url, config) => axios.delete(url.startsWith('http') ? url : `${API_URL}${url.startsWith('/') ? url : '/' + url}`, config),
    postponeQueueItem: (itemId) => apiClient.post(`/queue/${encodeURIComponent(itemId)}/postpone`),
    continueQueueItem: (itemId, boostPriority = true) => apiClient.post(`/queue/${encodeURIComponent(itemId)}/continue`, null, { params: { boost_priority: boostPriority } }),
    stopQueueItem: (itemId) => apiClient.post(`/queue/${encodeURIComponent(itemId)}/stop`),
};

export const api = {
    // Auth
    getAuthStatus: () => axios.get(`${API_URL}/api/auth/status`),
    login: (username, password) => axios.post(`${API_URL}/api/auth/login`, { username, password }),
    setCredentials: (username, password, regenerateApiKey = false) =>
        axios.post(`${API_URL}/api/auth/credentials`, { username, password, regenerate_api_key: regenerateApiKey }),
    disableAuth: () => axios.post(`${API_URL}/api/auth/disable`),

    // Setup wizard (docs/PLAN_onboarding_wizard.md)
    getSetupStatus: () => axios.get(`${API_URL}/api/setup/status`),

    // Projects
    getProjects: () => axios.get(`${API_URL}/projects`),
    createProject: (name, targetLanguage, parentProject = null, type = 'show') => axios.post(`${API_URL}/projects`, { name, target_language: targetLanguage, parent_project: parentProject, type }),
    getProject: (name) => axios.get(`${API_URL}/projects/${encodeURIComponent(name)}`),
    getProjectTokenSummary: (name) => axios.get(`${API_URL}/projects/${encodeURIComponent(name)}/token-summary`),
    testTranslation: (name, data) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/test-translation`, data),
    updateProject: (name, data, globalSave = false) => axios.put(`${API_URL}/projects/${encodeURIComponent(name)}`, data, { params: { global_save: globalSave } }),
    updateProjectPriority: (name, priority) => axios.put(`${API_URL}/projects/${encodeURIComponent(name)}/priority`, { priority }),
    bulkUpdateProjectPriority: (projects, priority) => axios.post(`${API_URL}/projects/bulk-priority`, { projects, priority }),
    updateGlossary: (projectName, glossary, globalSave = false, suppressedTerms = null) => {
        const payload = { glossary };
        if (suppressedTerms !== null) {
            payload.suppressed_glossary_terms = suppressedTerms;
        }
        return axios.put(`${API_URL}/projects/${encodeURIComponent(projectName)}`, payload, { params: { global_save: globalSave } });
    },
    syncProject: (projectName, options = {}) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/sync`, options),
    syncProjectDisk: (projectName, options = {}) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/sync-disk`, options),
    renameProjectSubtitles: (projectName, apply = true) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/rename-subtitles`, null, { params: { apply } }),
    promoteTerm: (projectName, termData) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/promote-term`, termData),
    promoteTermsBatch: (projectName, terms) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/promote-terms-batch`, { terms }),
    suppressTermsBatch: (projectName, terms) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/suppress-terms-batch`, { terms }),
    revertTermsBatch: (projectName, terms) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/revert-terms-batch`, { terms }),
    harvestEpisodeTerms: (projectName, episodeName, model = null) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/harvest-terms`, { model }),
    harvestProjectTerms: (projectName, episodeNames = null, model = null) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/harvest-terms`, { episode_names: episodeNames, model }),
    checkProjectMissing: (name) => axios.get(`${API_URL}/projects/${encodeURIComponent(name)}/check-missing`),
    translateProjectMissing: (name) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/translate-missing`),
    checkMissingLines: async (projectName, episodeName = null) => {
        const res = await axios.get(`${API_URL}/projects/${encodeURIComponent(projectName)}/check-missing`);
        if (episodeName && res.data?.results) {
            const epMatch = res.data.results.find(r => r.episode_name === episodeName);
            if (epMatch) {
                return {
                    data: {
                        ...epMatch,
                        missing_indices: (epMatch.lines || []).map(l => l.line_index),
                        total_lines: epMatch.lines ? epMatch.lines.length : 0,
                    }
                };
            }
            return { data: { missing_count: 0, missing_indices: [], total_lines: 0 } };
        }
        return res;
    },
    translateMissingLines: (projectName) =>
        axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/translate-missing`),

    // AI Enhancement
    enhanceContext: (name, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/context/enhance`, null, { params: { model } }),
    createContext: (name, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/context/create`, null, { params: { model } }),
    deleteContext: (name) => axios.delete(`${API_URL}/projects/${encodeURIComponent(name)}/context`),
    enhanceGlossary: (name, data, model, enableResearch = false) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/glossary/enhance`, data, { params: { model, enable_research: enableResearch } }),
    createGlossary: (name, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/glossary/create`, null, { params: { model } }),
    deleteGlossary: (name) => axios.delete(`${API_URL}/projects/${encodeURIComponent(name)}/glossary`),

    // Episodes
    getEpisodes: (projectName) => axios.get(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes`),
    // `etag` is optional — pass the value of a previous response's ETag header
    // to let an unchanged episode short-circuit to a 304 (EpisodeView.jsx's
    // live-view poll does this; see docs/PLAN_structural_polish.md Part A).
    getEpisode: (projectName, episodeName, etag = null) => axios.get(
        `${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}`,
        {
            headers: etag ? { 'If-None-Match': etag } : {},
            validateStatus: (status) => (status >= 200 && status < 300) || status === 304,
        }
    ),
    saveEpisode: (projectName, episodeName, data) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/save`, { data }),
    updateEpisodeMetadata: (projectName, episodeName, metadata) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/metadata`, { metadata }),
    deleteEpisode: (projectName, episodeName, deleteDiskFile = false) => apiClient.delete(`/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/translation`, { params: { delete_disk_file: deleteDiskFile } }),
    deleteEpisodeTranslation: (projectName, episodeName, deleteDiskFile = false) => apiClient.delete(`/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/translation`, { params: { delete_disk_file: deleteDiskFile } }),
    deleteLines: (projectName, episodeName, indexes) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/delete-lines`, { indexes }),
    applyCommonFixes: (projectName, episodeName, track = 'source', lang = null) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/apply-fixes`, { track, lang }),
    applyEpisodeFixes: (projectName, episodeName, options = {}) => {
        const track = (typeof options === 'string' ? options : (options.target_track || options.track)) || 'source';
        const lang = (typeof options === 'object' ? options.lang : null) || null;
        return axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/apply-fixes`, { track, lang });
    },
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
    translateEpisode: (projectName, episodeName, modelOrOptions, enhanceGlossary = false, force = false, translationType = 'saved') => {
        let payload;
        if (modelOrOptions && typeof modelOrOptions === 'object') {
            payload = {
                model: modelOrOptions.model || modelOrOptions.model_name,
                enhance_glossary: modelOrOptions.enhance_glossary ?? modelOrOptions.enhanceGlossary ?? false,
                force: modelOrOptions.force ?? false,
                translation_type: modelOrOptions.translation_type ?? modelOrOptions.translationType ?? 'saved',
                target_language_name: modelOrOptions.target_language_name,
                lang: modelOrOptions.lang,
            };
        } else {
            payload = {
                model: modelOrOptions,
                enhance_glossary: enhanceGlossary,
                force,
                translation_type: translationType,
            };
        }
        return axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/translate`, payload);
    },
    clearTranslation: (projectName, episodeName, deleteDiskFile = false) => apiClient.delete(`/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/translation`, { params: { delete_disk_file: deleteDiskFile } }),
    retranslateEpisode: (projectName, episodeName, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/retranslate`, { model }),
    mergeTranslation: (projectName, episodeName, selectedLines) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/merge`, { selected_lines: selectedLines }),

    // Batch Operations
    batchTranslate: (projectName, episodeNamesOrOptions, model, enhanceGlossary = false, force = false, translationType = 'saved') => {
        let payload;
        if (episodeNamesOrOptions && typeof episodeNamesOrOptions === 'object' && !Array.isArray(episodeNamesOrOptions)) {
            payload = {
                episode_names: episodeNamesOrOptions.episode_names || episodeNamesOrOptions.episodes || [],
                model: episodeNamesOrOptions.model || episodeNamesOrOptions.model_name || model,
                enhance_glossary: episodeNamesOrOptions.enhance_glossary ?? episodeNamesOrOptions.enhanceGlossary ?? enhanceGlossary,
                force: episodeNamesOrOptions.force ?? force,
                translation_type: episodeNamesOrOptions.translation_type ?? episodeNamesOrOptions.translationType ?? translationType,
            };
        } else {
            payload = {
                episode_names: episodeNamesOrOptions,
                model,
                enhance_glossary: enhanceGlossary,
                force,
                translation_type: translationType,
            };
        }
        return axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/batch-translate`, payload);
    },
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
    startFullIngestPipeline: (projectName) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/pipeline/full-ingest`),
    continuePipeline: (projectName, jobId) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/pipeline/${jobId}/continue`),

    // Downloads
    downloadEpisode: async (projectName, episodeName) => {
        const response = await axios.get(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/download`, { responseType: 'blob' });
        // Prefer the server-provided filename (correct language tag AND extension, e.g.
        // .el.ass for ASS sources); a blob URL drops Content-Disposition so we parse it.
        let filename = `${episodeName}.srt`;
        const cd = response.headers['content-disposition'] || response.headers['Content-Disposition'];
        if (cd) {
            const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
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
    getSettings: () => axios.get(`${API_URL}/api/settings`),
    updateSettings: (settings) => axios.post(`${API_URL}/api/settings`, settings),

    // API Key Management
    getApiKeyStatus: () => axios.get(`${API_URL}/api/config/api-key`),
    setApiKey: (apiKey) => axios.post(`${API_URL}/api/config/api-key`, { api_key: apiKey }),
    deleteApiKey: () => axios.delete(`${API_URL}/api/config/api-key`),
    testGeminiKey: (apiKey = null) => axios.post(`${API_URL}/api/config/test-gemini-key`, apiKey ? { api_key: apiKey } : {}),

    // Model Registry (Google Gemini)
    fetchAllModels: () => axios.get(`${API_URL}/api/models`),

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
    getReviewQueue: (project, episode = null) => {
        const params = episode ? { episode } : {};
        return axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/review-queue`, { params });
    },
    getGlobalReviewQueue: (params = {}) => axios.get(`${API_URL}/projects/review-queue`, { params }),
    // Lightweight badge count — no flagged-line data, just the number.
    getGlobalReviewCount: () => axios.get(`${API_URL}/projects/review-queue/count`),
    // POST /review-queue/resolve?episode_name=X&line_index=N&translated_text=...
    resolveReviewItem: (project, episode, index, translatedText) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/review-queue/resolve`, null, { params: { episode_name: episode, line_index: index, ...(translatedText != null ? { translated_text: translatedText } : {}) } }),
    resolveAllReviewItems: (project) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/review-queue/resolve-all`),
    // POST /review-queue/save-line?episode_name=X&line_index=N&translated_text=...
    saveReviewLine: (project, episode, index, translatedText) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/review-queue/save-line`, null, { params: { episode_name: episode, line_index: index, translated_text: translatedText } }),
    // Wrong-alphabet contamination. The scan is a read-only preview; the repair runs as
    // a job — free look-alike fixes first, then the rest in batched requests (~60 lines
    // per call across the whole project, not one call per episode).
    scanScriptGuard: (project) => axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/script-guard/scan`),
    scanScript: (project) => axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/script-guard/scan`),
    repairScriptGuard: (project, { model = null, useLlm = true, exportFiles = true } = {}) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/script-guard/repair`, { model, use_llm: useLlm, export: exportFiles }),
    fixScript: (project, episode = null, options = {}) => {
        const opts = (episode && typeof episode === 'object') ? episode : options;
        const payload = typeof opts === 'object' && opts !== null ? opts : {};
        return axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/script-guard/repair`, payload);
    },

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
    syncSonarr: (options = {}) => axios.post(`${API_URL}/integrations/sonarr/sync`, options),
    syncRadarr: (options = {}) => axios.post(`${API_URL}/integrations/radarr/sync`, options),
    syncArrAll: (options = {}) => axios.post(`${API_URL}/integrations/arr/sync`, options),
    syncMediaLibrary: (options = {}) => axios.post(`${API_URL}/integrations/arr/sync`, options),

    // Arr Library & Sync
    getArrLibrary: () => axios.get(`${API_URL}/integrations/arr/library`),
    disableLibraryEntry: (project) => axios.post(`${API_URL}/integrations/arr/library/${encodeURIComponent(project)}/disable`),
    enableLibraryEntry: (project) => axios.post(`${API_URL}/integrations/arr/library/${encodeURIComponent(project)}/enable`),
    
    // Background Queue & Sync
    getQueue: (reconcile = false) => axios.get(`${API_URL}/api/queue`, { params: { reconcile } }),
    syncDisk: (options = {}) => axios.post(`${API_URL}/api/sync/disk`, null, { params: options }),
    getEvents: (since = 0) => axios.get(`${API_URL}/api/events/poll`, { params: { since } }),
    retryQueueItem: (itemId) => axios.post(`${API_URL}/api/queue/${itemId}/retry`),
    cancelQueueItem: (itemId) => axios.post(`${API_URL}/api/queue/${itemId}/cancel`),
    postponeQueueItem: (itemId) => apiClient.post(`/queue/${encodeURIComponent(itemId)}/postpone`),
    continueQueueItem: (itemId, boostPriority = true) => apiClient.post(`/queue/${encodeURIComponent(itemId)}/continue`, null, { params: { boost_priority: boostPriority } }),
    stopQueueItem: (itemId) => apiClient.post(`/queue/${encodeURIComponent(itemId)}/stop`),
    cancelAllQueueItems: (status = 'pending') => axios.post(`${API_URL}/api/queue/cancel-all`, null, { params: { status } }),
    deleteQueueItem: (itemId) => axios.delete(`${API_URL}/api/queue/${itemId}`),
    prioritizeQueueItem: (itemId) => axios.post(`${API_URL}/api/queue/${itemId}/prioritize`),
    getQueueItemLogs: (itemId) => axios.get(`${API_URL}/api/queue/${itemId}/logs`),
    enqueueMissing: (project) => axios.post(`${API_URL}/integrations/arr/library/${encodeURIComponent(project)}/enqueue-missing`),
    enqueueAllMissing: () => axios.post(`${API_URL}/api/queue/enqueue-missing`),
    pauseQueue: () => axios.post(`${API_URL}/api/queue/pause`),
    resumeQueue: () => axios.post(`${API_URL}/api/queue/resume`),
    checkDailyLimit: () => axios.post(`${API_URL}/api/queue/check-daily-limit`),
    enqueueEpisodes: (project, episodes) => axios.post(`${API_URL}/api/queue/enqueue`, { project, episodes }),
    clearQueue: (status = 'history') => axios.delete(`${API_URL}/api/queue/clear`, { params: { status } }),
    clearQueueHistory: () => axios.delete(`${API_URL}/api/queue/clear`, { params: { status: 'history' } }),

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

    // Embedded ASS Subtitle Lookup & Extraction
    probeEpisodeEmbedded: (project, episode, forceRefresh = false) =>
        axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/episodes/${encodeURIComponent(episode)}/embedded/probe`, { params: { force_refresh: forceRefresh }, timeout: 120000 }),
    getEmbeddedTracks: (project, episode, forceRefresh = false) =>
        axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/episodes/${encodeURIComponent(episode)}/embedded/probe`, { params: { force_refresh: forceRefresh }, timeout: 120000 }),
    extractEpisodeEmbedded: (project, episode, options = {}) =>
        axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/episodes/${encodeURIComponent(episode)}/embedded/extract`, options, { timeout: 600000 }),
    extractEmbeddedTrack: (project, episode, trackIndex, options = {}) => {
        const payload = typeof trackIndex === 'object' ? trackIndex : { track_index: trackIndex, ...options };
        return axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/episodes/${encodeURIComponent(episode)}/embedded/extract`, payload, { timeout: 600000 });
    },
    getProjectEmbeddedStatus: (project) =>
        axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/embedded/status`),
    batchExtractEmbedded: (project, options = {}) =>
        axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/embedded/extract-all`, options, { timeout: 600000 }),
    testMediaProbe: (mediaPath) =>
        axios.post(`${API_URL}/api/settings/test-media-probe`, { media_path: mediaPath }, { timeout: 120000 }),

    // System & Updates
    getUpdateStatus: () => axios.get(`${API_URL}/api/system/updates/status`),
    checkForUpdates: () => axios.post(`${API_URL}/api/system/updates/check`),
    applyUpdate: (force = false) => axios.post(`${API_URL}/api/system/updates/apply`, { force }),
    restartServer: () => axios.post(`${API_URL}/api/system/restart`),
    getBackupEstimate: () => axios.get(`${API_URL}/api/backup/estimate`),
    exportBackupBundle: (mode = 'compact') => axios.get(`${API_URL}/api/backup/export`, { params: { mode }, responseType: 'blob' }),
    inspectBackupBundle: (formData) => axios.post(`${API_URL}/api/backup/inspect`, formData, { headers: { 'Content-Type': 'multipart/form-data' } }),
    importBackupBundle: (formData) => axios.post(`${API_URL}/api/backup/import`, formData, { headers: { 'Content-Type': 'multipart/form-data' } }),

    // Agent Tasks
    getPendingAgentTasks: () => axios.get(`${API_URL}/api/agent/tasks/pending`),
    claimNextAgentTask: () => axios.post(`${API_URL}/api/agent/tasks/next`),
    prepareAgentTask: (queueId) => axios.post(`${API_URL}/api/agent/tasks/${encodeURIComponent(queueId)}/prepare`),
    prepareTargetAgentTask: (projectName, episodeName, lang = null) => axios.post(`${API_URL}/api/agent/tasks/target`, { project_name: projectName, episode_name: episodeName, lang }),
    enqueueForAgent: (projectName, episodeNames = null, priority = 1, options = {}) => axios.post(`${API_URL}/api/agent/enqueue`, { project_name: projectName, episode_names: episodeNames, priority, ...options }),
    getActiveAgentTasks: () => axios.get(`${API_URL}/api/agent/tasks/active`),
    checkAgentTask: (taskId) => axios.post(`${API_URL}/api/agent/tasks/${encodeURIComponent(taskId)}/check`, {}),
    releaseAgentTask: (taskId) => axios.post(`${API_URL}/api/agent/tasks/${encodeURIComponent(taskId)}/release`),
    // `options` carries the optional audit flags: { repair, misplaced, full }. requeue=true implies repair.
    auditProjectTranslations: (projectName, requeue = false, options = {}) => axios.post(`${API_URL}/api/agent/audit`, { project_name: projectName, requeue, ...options }),
    // Library-wide translation check (background job on the server). POST starts it (HTTP 409 + the
    // current state if one is already running); GET returns { status, progress, result, ... } to poll.
    startLibraryAudit: (options = {}) => axios.post(`${API_URL}/api/agent/library-audit`, options),
    getLibraryAudit: () => axios.get(`${API_URL}/api/agent/library-audit`),
    submitAgentTask: (taskId, payload = {}) => axios.post(`${API_URL}/api/agent/tasks/${encodeURIComponent(taskId)}/submit`, payload),
    syncAgentTasks: () => axios.post(`${API_URL}/api/agent/tasks/sync`),
    cancelAgentTask: (taskId) => axios.post(`${API_URL}/api/agent/tasks/${encodeURIComponent(taskId)}/cancel`),
    cancelAllAgentTasks: (status = 'pending') => axios.post(`${API_URL}/api/agent/tasks/cancel-all`, null, { params: { status } }),
    clearAgentTasks: (status = 'finished') => axios.post(`${API_URL}/api/agent/tasks/clear`, null, { params: { status } }),
    // Direct agent runner (translates the queue through an agent CLI)
    getAgentRunner: (since = 0, quota = false) => axios.get(`${API_URL}/api/agent/runner`, { params: { since, quota } }),
    startAgentRunner: (config = {}) => axios.post(`${API_URL}/api/agent/runner/start`, config),
    stopAgentRunner: (now = false) => axios.post(`${API_URL}/api/agent/runner/stop`, null, { params: { now } }),
    resumeAgentRunnerNow: () => axios.post(`${API_URL}/api/agent/runner/resume-now`),
    // Autopilot (docs/PLAN_autopilot.md 4.10)
    getAutopilotStatus: () => axios.get(`${API_URL}/api/autopilot/status`),
    getAutopilotItems: ({ state, q, limit = 50, offset = 0 } = {}) => axios.get(`${API_URL}/api/autopilot/items`, { params: { state: state || undefined, q: q || undefined, limit, offset } }),
    getAutopilotItem: (id) => axios.get(`${API_URL}/api/autopilot/items/${encodeURIComponent(id)}`),
    retryAutopilotItem: (id) => axios.post(`${API_URL}/api/autopilot/items/${encodeURIComponent(id)}/retry`),
    skipAutopilotItem: (id) => axios.post(`${API_URL}/api/autopilot/items/${encodeURIComponent(id)}/skip`),
    autopilotItemNow: (id) => axios.post(`${API_URL}/api/autopilot/items/${encodeURIComponent(id)}/now`),
    autopilotPollNow: () => axios.post(`${API_URL}/api/autopilot/poll`),
    autopilotReconcileNow: () => axios.post(`${API_URL}/api/autopilot/reconcile`),
};

