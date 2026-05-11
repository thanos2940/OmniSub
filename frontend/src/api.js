import axios from 'axios';

const API_URL = 'http://localhost:8000';

export const api = {
    // Projects
    getProjects: () => axios.get(`${API_URL}/projects`),
    createProject: (name, targetLanguage, parentProject = null, type = 'show') => axios.post(`${API_URL}/projects`, { name, target_language: targetLanguage, parent_project: parentProject, type }),
    getProject: (name) => axios.get(`${API_URL}/projects/${encodeURIComponent(name)}`),
    getProjectTokenSummary: (name) => axios.get(`${API_URL}/projects/${encodeURIComponent(name)}/token-summary`),
    testTranslation: (name, data) => axios.post(`${API_URL}/projects/${encodeURIComponent(name)}/test-translation`, data),
    updateProject: (name, data) => axios.put(`${API_URL}/projects/${encodeURIComponent(name)}`, data),
    importProjectData: (projectName, sourceProject, importGlossary = true, importContext = true) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/import`, { source_project: sourceProject, import_glossary: importGlossary, import_context: importContext }),

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

    // Actions
    uploadEpisode: (projectName, episodeName, file) => {
        const formData = new FormData();
        formData.append('file', file);
        return axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/upload`, formData, {
            headers: { 'Content-Type': 'multipart/form-data' }
        });
    },
    scanEpisode: (projectName, episodeName, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/scan`, { model }),
    translateEpisode: (projectName, episodeName, model, enhanceGlossary = false) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/translate`, { model, enhance_glossary: enhanceGlossary }),
    clearTranslation: (projectName, episodeName) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/clear`),
    retranslateEpisode: (projectName, episodeName, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/retranslate`, { model }),
    mergeTranslation: (projectName, episodeName, selectedLines) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/merge`, { selected_lines: selectedLines }),

    // Batch Operations
    batchTranslate: (projectName, episodeNames, model, enhanceGlossary = false) => axios.post(`${API_URL}/projects/${encodeURIComponent(projectName)}/batch-translate`, { episode_names: episodeNames, model, enhance_glossary: enhanceGlossary }),
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
        const url = window.URL.createObjectURL(new Blob([response.data]));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `${episodeName}.srt`); // Backend provides better name, but this is a fallback
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
    updateCharacter: (project, name, data) => axios.put(`${API_URL}/projects/${encodeURIComponent(project)}/characters/${encodeURIComponent(name)}`, data),
    deleteCharacter: (project, name) => axios.delete(`${API_URL}/projects/${encodeURIComponent(project)}/characters/${encodeURIComponent(name)}`),
    // Returns { job_id } — use getJob() to poll for completion
    generateCharacters: (project, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/characters/generate`, null, { params: { model } }),

    // Review Queue
    // GET returns { items: [...], count: N }
    getReviewQueue: (project) => axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/review-queue`),
    // POST /review-queue/resolve?episode_name=X&line_index=N
    resolveReviewItem: (project, episode, index) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/review-queue/resolve`, null, { params: { episode_name: episode, line_index: index } }),
    resolveAllReviewItems: (project) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/review-queue/resolve-all`),

    // Episode Summaries
    // GET /summaries returns { episode_name: summary_text, ... }
    getSummaries: (project) => axios.get(`${API_URL}/projects/${encodeURIComponent(project)}/summaries`),
    // PUT /summaries/{episode} expects { summary: "text" }
    updateSummary: (project, episode, summary) => axios.put(`${API_URL}/projects/${encodeURIComponent(project)}/summaries/${encodeURIComponent(episode)}`, { summary }),
    deleteSummary: (project, episode) => axios.delete(`${API_URL}/projects/${encodeURIComponent(project)}/summaries/${encodeURIComponent(episode)}`),
    // POST /summaries/{episode}/generate?model=X — returns { job_id }
    generateSummary: (project, episode, model) => axios.post(`${API_URL}/projects/${encodeURIComponent(project)}/summaries/${encodeURIComponent(episode)}/generate`, null, { params: { model } }),

    // Bazarr Integration
    getBazarrStatus: () => axios.get(`${API_URL}/integrations/bazarr/status`),
    testBazarr: (settings) => axios.post(`${API_URL}/integrations/bazarr/test`, settings),
    scanNow: (settings) => axios.post(`${API_URL}/integrations/bazarr/scan-now`, settings),
    getBazarrPreview: () => axios.get(`${API_URL}/integrations/bazarr/preview`),
    // POST /integrations/bazarr/toggle?enabled=true|false
    toggleBazarr: (enabled) => axios.post(`${API_URL}/integrations/bazarr/toggle`, null, { params: { enabled } }),

    // Bazarr Library & Sync
    getBazarrLibrary: () => axios.get(`${API_URL}/integrations/bazarr/library`),
    syncBazarr: () => axios.post(`${API_URL}/integrations/bazarr/sync`),
    getBazarrProfiles: () => axios.get(`${API_URL}/integrations/bazarr/profiles`),
    disableLibraryEntry: (project) => axios.post(`${API_URL}/integrations/bazarr/library/${encodeURIComponent(project)}/disable`),
    enableLibraryEntry: (project) => axios.post(`${API_URL}/integrations/bazarr/library/${encodeURIComponent(project)}/enable`),
};
