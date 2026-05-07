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

    // Local Models
    fetchLocalModels: (baseUrl = null) => axios.get(`${API_URL}/api/config/models/local`, { params: { base_url: baseUrl } }),
};
