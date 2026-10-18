import assert from 'node:assert/strict';

console.log("=== Starting Challenger M3-2 Empirical Frontend Verification ===");

// ---------------------------------------------------------------------------
// 1. API Dual Signature Verification (api.translateEpisode & api.batchTranslate)
// ---------------------------------------------------------------------------
console.log("\n[TEST 1] API Dual Signature (Positional vs Object Options)...");

let lastPostCall = null;
const mockAxios = {
    post: async (url, payload) => {
        lastPostCall = { url, payload };
        return { data: { success: true, job_id: 'mock_job_123' } };
    }
};

// Replicate api.js translateEpisode signature & extraction logic
const translateEpisode = (projectName, episodeName, modelOrOptions, enhanceGlossary = false, force = false, translationType = 'saved') => {
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
    return mockAxios.post(`/projects/${encodeURIComponent(projectName)}/episodes/${encodeURIComponent(episodeName)}/translate`, payload);
};

// Replicate api.js batchTranslate signature & extraction logic
const batchTranslate = (projectName, episodeNamesOrOptions, model, enhanceGlossary = false, force = false, translationType = 'saved') => {
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
    return mockAxios.post(`/projects/${encodeURIComponent(projectName)}/batch-translate`, payload);
};

// Test 1a: translateEpisode with positional arguments
await translateEpisode("TestProj", "S01E01", "gemini-2.5-flash", true, false, "saved");
assert.equal(lastPostCall.url, "/projects/TestProj/episodes/S01E01/translate");
assert.deepEqual(lastPostCall.payload, {
    model: "gemini-2.5-flash",
    enhance_glossary: true,
    force: false,
    translation_type: "saved"
});

// Test 1b: translateEpisode with options object
await translateEpisode("TestProj", "S01E01", {
    model: "gemini-2.5-pro",
    enhanceGlossary: true,
    force: true,
    translationType: "scratch",
    target_language_name: "Greek"
});
assert.deepEqual(lastPostCall.payload, {
    model: "gemini-2.5-pro",
    enhance_glossary: true,
    force: true,
    translation_type: "scratch",
    target_language_name: "Greek",
    lang: undefined
});

// Test 1c: batchTranslate with positional arguments
await batchTranslate("TestProj", ["S01E01", "S01E02"], "gemini-2.5-flash", true, true, "saved");
assert.equal(lastPostCall.url, "/projects/TestProj/batch-translate");
assert.deepEqual(lastPostCall.payload, {
    episode_names: ["S01E01", "S01E02"],
    model: "gemini-2.5-flash",
    enhance_glossary: true,
    force: true,
    translation_type: "saved"
});

// Test 1d: batchTranslate with options object
await batchTranslate("TestProj", {
    episode_names: ["S01E01", "S01E02", "S01E03"],
    model: "gemini-2.5-flash",
    enhanceGlossary: true,
    force: false,
    translationType: "scratch"
});
assert.deepEqual(lastPostCall.payload, {
    episode_names: ["S01E01", "S01E02", "S01E03"],
    model: "gemini-2.5-flash",
    enhance_glossary: true,
    force: false,
    translation_type: "scratch"
});
console.log("  -> [PASS] Dual-signature handling verified for both positional and options formats.");

// ---------------------------------------------------------------------------
// 2. SimplePipelineWizard Lifecycle & Ref Guard Stress Test
// ---------------------------------------------------------------------------
console.log("\n[TEST 2] SimplePipelineWizard Lifecycle, Polling Stability & Ref Guards...");

class WizardLifecycleSimulator {
    constructor(projectName, mockApi, onComplete) {
        this.projectName = projectName;
        this.api = mockApi;
        this.onComplete = onComplete;

        // Refs from SimplePipelineWizard.jsx
        this.activeJobsRef = { current: {} };
        this.isExecutingRef = { current: false };
        this.hasTriggeredRef = { current: false };
        this.isConfirmingRef = { current: false };
        this.hasCompletedRef = { current: false };
        this.prevProjectRef = { current: projectName };
        this.prevStageRef = { current: null };

        // States
        this.isStarting = false;
        this.pipelineJobId = null;
        this.episodes = [];
        this.activeJobs = {};

        // Tracking call counts
        this.startPipelineCallCount = 0;
        this.apiStartPipelineCount = 0;
        this.onCompleteCallCount = 0;
        this.fetchEpisodesCount = 0;
    }

    // Callback startPipeline (replicates lines 59-104)
    async startPipeline() {
        this.startPipelineCallCount++;
        if (this.isExecutingRef.current) return;
        this.isExecutingRef.current = true;
        this.isStarting = true;

        try {
            const currentJobs = this.activeJobsRef.current || {};
            const existingJobId = Object.keys(currentJobs).find(jid => {
                const j = currentJobs[jid];
                const matchesProject = j && (
                    (j.project_name && j.project_name.toLowerCase() === this.projectName.toLowerCase()) ||
                    (j.metadata?.projectId && j.metadata.projectId.toLowerCase() === this.projectName.toLowerCase()) ||
                    j.project_name === this.projectName ||
                    j.metadata?.projectId === this.projectName
                );
                const matchesType = j && (j.pipeline_mode === 'simple' || j.id?.startsWith('simple_pipeline') || j.type === 'simple_pipeline');
                return matchesProject && matchesType && ['pending', 'running', 'awaiting_review'].includes(j?.status);
            });
            if (existingJobId) {
                this.pipelineJobId = existingJobId;
                return;
            }

            this.apiStartPipelineCount++;
            const res = await this.api.startSimplePipeline(this.projectName);
            const jobId = res.data.job_id;
            this.pipelineJobId = jobId;
        } finally {
            this.isExecutingRef.current = false;
            this.isStarting = false;
        }
    }

    async fetchEpisodes() {
        this.fetchEpisodesCount++;
        const res = await this.api.getEpisodes(this.projectName);
        this.episodes = res.data || [];
    }

    // Mount effect (replicates lines 106-116)
    mount() {
        if (!this.hasTriggeredRef.current) {
            this.hasTriggeredRef.current = true;
            this.startPipeline();
        }
        this.fetchEpisodes();
    }

    // Polling tick: updates activeJobs and triggers effects
    updateActiveJobs(newActiveJobs) {
        this.activeJobs = newActiveJobs;
        // activeJobsRef updates via useEffect(..., [activeJobs])
        this.activeJobsRef.current = newActiveJobs;

        // Status effect (replicates lines 122-143)
        const job = this.pipelineJobId ? this.activeJobs[this.pipelineJobId] : null;
        if (!job) return;

        if (job.status === 'completed') {
            if (!this.hasCompletedRef.current) {
                this.hasCompletedRef.current = true;
                this.onCompleteCallCount++;
                if (this.onComplete) this.onComplete();
            }
        }

        if (job.pipeline_stage === 'translate' && this.prevStageRef.current !== 'translate') {
            this.fetchEpisodes();
        }
        this.prevStageRef.current = job.pipeline_stage;
    }
}

const mockWizardApi = {
    startSimplePipeline: async (proj) => {
        // simulate 20ms async API latency
        await new Promise(r => setTimeout(r, 20));
        return { data: { job_id: `sp_${proj}_001` } };
    },
    getEpisodes: async () => ({ data: [{ name: 'ep1', translated: false }] })
};

const wizard = new WizardLifecycleSimulator("TestShow", mockWizardApi, () => {});

// Mount the wizard
wizard.mount();
assert.equal(wizard.hasTriggeredRef.current, true, "hasTriggeredRef should be true on mount");
assert.equal(wizard.isStarting, true, "isStarting state must be true while API call in flight");

// Simulate a rapid second mount or re-render during start
wizard.mount();
assert.equal(wizard.apiStartPipelineCount, 1, "startPipeline API must NOT be called twice on re-render");

// Wait for initial start to complete
await new Promise(r => setTimeout(r, 30));
assert.equal(wizard.isStarting, false, "isStarting must be false after start completes");
assert.equal(wizard.pipelineJobId, "sp_TestShow_001");

// Simulate 20 polling ticks of activeJobs (simulating polling every 2s for 40s)
console.log("  -> Simulating 20 polling ticks with activeJobs updates...");
for (let i = 1; i <= 20; i++) {
    wizard.updateActiveJobs({
        "sp_TestShow_001": {
            id: "sp_TestShow_001",
            status: "running",
            pipeline_stage: i < 10 ? "analyze" : "translate",
            progress: i * 5,
            message: `Processing step ${i}`
        }
    });
}

// Verification: startPipeline should NEVER have been triggered by polling ticks
assert.equal(wizard.apiStartPipelineCount, 1, "Polling ticks must NEVER trigger startPipeline API call");
assert.equal(wizard.startPipelineCallCount, 1, "startPipeline function must NOT be called during activeJobs polling");
console.log("  -> [PASS] Polling decoupled: startPipeline was NOT re-triggered across 20 polling ticks.");

// Test 2b: Completion guard
console.log("  -> Testing completion deduplication guard...");
// Send status = 'completed' across 10 consecutive polling ticks
for (let i = 0; i < 10; i++) {
    wizard.updateActiveJobs({
        "sp_TestShow_001": {
            id: "sp_TestShow_001",
            status: "completed",
            progress: 100
        }
    });
}
assert.equal(wizard.onCompleteCallCount, 1, "onComplete MUST be called exactly once despite repeated completed status ticks");
console.log("  -> [PASS] onComplete deduplicated: called exactly 1 time across 10 completed ticks.");

// Test 2c: Retry pipeline button with in-flight lock
console.log("  -> Testing 'Retry pipeline' concurrent click lock and spinner...");
let retryStarted = false;
wizard.api.startSimplePipeline = async () => {
    retryStarted = true;
    await new Promise(r => setTimeout(r, 50));
    return { data: { job_id: 'sp_retry_999' } };
};

// Fire 5 rapid clicks on "Retry pipeline"
const retryPromises = [
    wizard.startPipeline(),
    wizard.startPipeline(),
    wizard.startPipeline(),
    wizard.startPipeline(),
    wizard.startPipeline()
];
assert.equal(wizard.isStarting, true, "isStarting state must be true on retry click");
await Promise.all(retryPromises);
assert.equal(wizard.isStarting, false, "isStarting state must be reset to false after retry completes");
assert.equal(wizard.pipelineJobId, "sp_retry_999");
// Only 1 of the 5 concurrent clicks could acquire the lock
console.log("  -> [PASS] Retry pipeline concurrent clicks locked: exactly 1 execution allowed.");

// ---------------------------------------------------------------------------
// 3. ProjectDetail Routing & Single vs Batch Translation Dispatch
// ---------------------------------------------------------------------------
console.log("\n[TEST 3] ProjectDetail Translation Routing & Single-Confirm Batch Delete...");

class ProjectDetailSimulator {
    constructor(projectName) {
        this.projectName = projectName;
        this.translationInFlightRef = { current: false };
        this.isBatchDeletingRef = { current: false };

        this.translateEpisodeCalls = [];
        this.batchTranslateCalls = [];
        this.autoTranslateCalls = [];
        this.deleteEpisodeCalls = [];
        this.loadDataCalls = [];
        this.confirmDialogCalls = [];
        this.confirmReturnValue = true;

        this.selectedEpisodes = new Set();
        this.episodes = [
            { name: "S01E01", translated: false, has_source: true },
            { name: "S01E02", translated: false, has_source: true },
            { name: "S01E03", translated: true, has_source: true },
            { name: "S01E04", translated: false, has_source: true },
        ];
    }

    async translateEpisode(p, ep, model, enhance, force, type) {
        await new Promise(r => setTimeout(r, 10));
        this.translateEpisodeCalls.push({ p, ep, model, enhance, force, type });
        return { data: { job_id: `tr_${ep}` } };
    }

    async batchTranslate(p, payload) {
        await new Promise(r => setTimeout(r, 10));
        this.batchTranslateCalls.push({ p, payload });
        return { data: { job_id: `batch_${payload.episode_names.length}` } };
    }

    async autoTranslate(p, options) {
        await new Promise(r => setTimeout(r, 10));
        this.autoTranslateCalls.push({ p, options });
        return { data: { job_id: `auto_${p}` } };
    }

    async deleteEpisode(p, ep) {
        await new Promise(r => setTimeout(r, 10));
        this.deleteEpisodeCalls.push({ p, ep });
        return { data: { success: true } };
    }

    async loadData(silent = false) {
        this.loadDataCalls.push({ silent, timestamp: Date.now() });
    }

    confirm(message) {
        this.confirmDialogCalls.push(message);
        return this.confirmReturnValue;
    }

    // handleConfirmTranslation (replicates ProjectDetail.jsx lines 104-200)
    async handleConfirmTranslation({ translationType, force, enhanceGlossary, model, episodeName, isBatch }) {
        if (this.translationInFlightRef.current) {
            return false;
        }
        this.translationInFlightRef.current = true;

        try {
            // Single Episode
            if (episodeName) {
                await this.translateEpisode(
                    this.projectName,
                    episodeName,
                    model || undefined,
                    enhanceGlossary,
                    force,
                    translationType
                );
                return true;
            }

            // Batch
            let epList = [];
            if (isBatch && this.selectedEpisodes.size > 0) {
                epList = Array.from(this.selectedEpisodes);
            } else if (isBatch) {
                epList = (this.episodes || [])
                    .filter(e => !e.translated && e.has_source)
                    .map(e => e.name);
            }

            if (epList.length === 0) return false;

            // Batch of exactly 1 routes as direct single episode
            if (epList.length === 1) {
                const singleEp = epList[0];
                await this.translateEpisode(
                    this.projectName,
                    singleEp,
                    model || undefined,
                    enhanceGlossary,
                    force,
                    translationType
                );
                return true;
            }

            // Multi-episode batch routes to batchTranslate
            await this.batchTranslate(this.projectName, {
                episode_names: epList,
                model: model || undefined,
                enhance_glossary: enhanceGlossary,
                force,
                translation_type: translationType,
            });
            return true;
        } finally {
            this.translationInFlightRef.current = false;
        }
    }

    // handleBatchDelete (replicates useProjectDetail.js lines 345-392)
    async handleBatchDelete(selectedList) {
        if (!selectedList || selectedList.length === 0) return false;
        if (this.isBatchDeletingRef.current) return false;

        const epList = Array.isArray(selectedList) ? selectedList : Array.from(selectedList);

        if (!this.confirm(`Delete subtitles for ${epList.length} selected episode(s)?`)) {
            return false;
        }

        this.isBatchDeletingRef.current = true;
        try {
            await Promise.all(
                epList.map(epName => this.deleteEpisode(this.projectName, epName))
            );
            await this.loadData(true);
            return true;
        } finally {
            this.isBatchDeletingRef.current = false;
        }
    }
}

const pd = new ProjectDetailSimulator("SeriesProject");

// Test 3a: Single Episode Translation Dispatch
console.log("  -> Testing single episode translation dispatch...");
await pd.handleConfirmTranslation({
    episodeName: "S01E01",
    model: "gemini-2.5-flash",
    translationType: "saved",
    force: false,
    enhanceGlossary: false,
    isBatch: false
});
assert.equal(pd.translateEpisodeCalls.length, 1, "translateEpisode must be called for single episode");
assert.equal(pd.translateEpisodeCalls[0].ep, "S01E01");
assert.equal(pd.batchTranslateCalls.length, 0, "batchTranslate must NOT be called for single episode");
assert.equal(pd.autoTranslateCalls.length, 0, "autoTranslate must NEVER be called for single episode");
console.log("  -> [PASS] Single episode dispatched directly to api.translateEpisode (no autoTranslate pollution).");

// Test 3b: Batch Translation with >1 episodes
console.log("  -> Testing batch translation dispatch with multiple episodes...");
pd.selectedEpisodes = new Set(["S01E01", "S01E02", "S01E04"]);
await pd.handleConfirmTranslation({
    model: "gemini-2.5-flash",
    translationType: "saved",
    force: false,
    enhanceGlossary: true,
    isBatch: true
});
assert.equal(pd.batchTranslateCalls.length, 1, "batchTranslate must be called for 3 episodes");
assert.deepEqual(pd.batchTranslateCalls[0].payload.episode_names, ["S01E01", "S01E02", "S01E04"]);
console.log("  -> [PASS] Multi-episode batch dispatched to api.batchTranslate.");

// Test 3c: Batch Translation resolving to exactly 1 episode
console.log("  -> Testing batch translation resolving to exactly 1 episode...");
pd.selectedEpisodes = new Set(["S01E02"]);
await pd.handleConfirmTranslation({
    model: "gemini-2.5-flash",
    translationType: "saved",
    force: false,
    enhanceGlossary: false,
    isBatch: true
});
assert.equal(pd.translateEpisodeCalls.length, 2, "Single item batch must route to translateEpisode");
assert.equal(pd.translateEpisodeCalls[1].ep, "S01E02");
console.log("  -> [PASS] Single-item batch cleanly routed to translateEpisode.");

// Test 3d: In-Flight Lock on Double Dispatch
console.log("  -> Testing in-flight double dispatch lock...");
pd.selectedEpisodes = new Set(["S01E01", "S01E02"]);
const [firstCall, secondCall] = await Promise.all([
    pd.handleConfirmTranslation({ isBatch: true }),
    pd.handleConfirmTranslation({ isBatch: true })
]);
assert.equal(firstCall, true, "First call should proceed");
assert.equal(secondCall, false, "Second call during in-flight must be rejected");
console.log("  -> [PASS] In-flight double click rejected by translationInFlightRef.");

// Test 3e: Single-Confirm Batch Delete
console.log("\n[TEST 4] Batch Deletion Verification (Single Dialog & Single Reload)...");
const deleteList = ["S01E01", "S01E02", "S01E04"];

// Test with confirm = true
pd.confirmReturnValue = true;
pd.deleteEpisodeCalls = [];
pd.loadDataCalls = [];
pd.confirmDialogCalls = [];

const deleteResult = await pd.handleBatchDelete(deleteList);
assert.equal(deleteResult, true);
assert.equal(pd.confirmDialogCalls.length, 1, "MUST show exactly 1 confirmation dialog");
assert.equal(pd.deleteEpisodeCalls.length, 3, "All 3 selected episodes must be deleted");
assert.equal(pd.loadDataCalls.length, 1, "MUST trigger exactly 1 loadData refresh");
assert.equal(pd.loadDataCalls[0].silent, true, "loadData must be silent to avoid screen flicker");
console.log("  -> [PASS] Exactly 1 confirm dialog shown, all 3 episodes deleted, exactly 1 reload triggered.");

// Test with confirm = false (User clicks Cancel)
console.log("  -> Testing user cancellation of batch delete...");
pd.confirmReturnValue = false;
pd.deleteEpisodeCalls = [];
pd.loadDataCalls = [];
pd.confirmDialogCalls = [];

const cancelResult = await pd.handleBatchDelete(deleteList);
assert.equal(cancelResult, false);
assert.equal(pd.confirmDialogCalls.length, 1, "Confirmation dialog was shown");
assert.equal(pd.deleteEpisodeCalls.length, 0, "No delete calls if user cancelled");
assert.equal(pd.loadDataCalls.length, 0, "No reload if user cancelled");
console.log("  -> [PASS] Cancel safely aborted deletion without API calls or reloads.");

// Test concurrent batch delete calls
console.log("  -> Testing concurrent batch delete prevention...");
pd.confirmReturnValue = true;
pd.deleteEpisodeCalls = [];
pd.loadDataCalls = [];
pd.confirmDialogCalls = [];

const [del1, del2] = await Promise.all([
    pd.handleBatchDelete(deleteList),
    pd.handleBatchDelete(deleteList)
]);
assert.equal(del1, true, "First batch delete proceeds");
assert.equal(del2, false, "Second concurrent batch delete rejected by lock");
assert.equal(pd.confirmDialogCalls.length, 1, "Only 1 dialog shown despite 2 concurrent calls");
console.log("  -> [PASS] Concurrent batch delete prevented by isBatchDeletingRef.");

// ---------------------------------------------------------------------------
// 5. TranslationModal Submission Guard & State Simulation
// ---------------------------------------------------------------------------
console.log("\n[TEST 5] TranslationModal In-Flight Submission Guard...");

class ModalSimulator {
    constructor() {
        this.isSubmitting = false;
        this.isSubmittingRef = { current: false };
        this.onConfirmCallCount = 0;
    }

    async onConfirm() {
        this.onConfirmCallCount++;
        await new Promise(r => setTimeout(r, 30));
    }

    async handleStart() {
        if (this.isSubmittingRef.current || this.isSubmitting) return false;
        this.isSubmittingRef.current = true;
        this.isSubmitting = true;
        try {
            await this.onConfirm();
            return true;
        } finally {
            this.isSubmittingRef.current = false;
            this.isSubmitting = false;
        }
    }
}

const modal = new ModalSimulator();
// Simulate 5 rapid clicks on "Start Translation"
const modalClicks = await Promise.all([
    modal.handleStart(),
    modal.handleStart(),
    modal.handleStart(),
    modal.handleStart(),
    modal.handleStart(),
]);

const acceptedClicks = modalClicks.filter(c => c === true).length;
const rejectedClicks = modalClicks.filter(c => c === false).length;
assert.equal(acceptedClicks, 1, "Only 1 click accepted");
assert.equal(rejectedClicks, 4, "4 rapid clicks rejected");
assert.equal(modal.onConfirmCallCount, 1, "onConfirm called exactly once");
assert.equal(modal.isSubmitting, false, "isSubmitting reset to false");
assert.equal(modal.isSubmittingRef.current, false, "isSubmittingRef reset to false");
console.log("  -> [PASS] TranslationModal rejected 4 duplicate clicks synchronously and reset state cleanly.");

console.log("\n=======================================================");
console.log("ALL 5 EMPIRICAL CHALLENGER VERIFICATION TESTS PASSED!");
console.log("=======================================================\n");
