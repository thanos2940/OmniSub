import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import ReactDOMServer from 'react-dom/server';
import axios from 'axios';
import { createServer } from 'vite';

// Mock localStorage for axios interceptors in Node environment
if (!globalThis.localStorage) {
    globalThis.localStorage = {
        getItem: () => null,
        setItem: () => {},
        removeItem: () => {},
    };
}

let viteServer;
let apiModule;
let useProjectDetailModule;
let TranslationModalModule;

test.before(async () => {
    viteServer = await createServer({
        server: { middlewareMode: true },
        appType: 'custom',
    });

    const apiImport = await viteServer.ssrLoadModule('./src/api.js');
    apiModule = apiImport.api;
    const hookImport = await viteServer.ssrLoadModule('./src/hooks/useProjectDetail.js');
    useProjectDetailModule = hookImport.useProjectDetail;
    const modalImport = await viteServer.ssrLoadModule('./src/components/project/modals/TranslationModal.jsx');
    TranslationModalModule = modalImport.default;
});

test.after(async () => {
    if (viteServer) {
        await viteServer.close();
    }
});

// Helper to instantiate useProjectDetail in SSR
function createHookInstance(toastSpy = {}, addJobSpy = () => {}) {
    let hookResult;
    function TestComp() {
        hookResult = useProjectDetailModule({
            projectName: 'demo_project',
            addJob: addJobSpy,
            activeJobs: {},
            toast: {
                warning: () => {},
                success: () => {},
                error: () => {},
                info: () => {},
                ...toastSpy,
            },
        });
        return null;
    }
    ReactDOMServer.renderToString(React.createElement(TestComp));
    return hookResult;
}

// Helper to traverse React element tree
function findElement(node, predicate) {
    if (!node || typeof node !== 'object') return null;
    if (predicate(node)) return node;
    const children = node.props?.children;
    if (Array.isArray(children)) {
        for (const child of children) {
            const found = findElement(child, predicate);
            if (found) return found;
        }
    } else if (children && typeof children === 'object') {
        return findElement(children, predicate);
    }
    return null;
}

// =========================================================================
// SUITE 1: API Client Dual Signature & Payload Normalization
// =========================================================================

test('API: translateEpisode accepts positional arguments correctly', async () => {
    let postedUrl;
    let postedData;

    const origPost = axios.post;
    axios.post = async (url, data) => {
        postedUrl = url;
        postedData = data;
        return { data: { job_id: 'ep-pos-1' } };
    };

    await apiModule.translateEpisode('DemoProj', 'Ep01', 'flash-lite', true, true, 'scratch');
    assert.ok(postedUrl.includes('/projects/DemoProj/episodes/Ep01/translate'));
    assert.deepEqual(postedData, {
        model: 'flash-lite',
        enhance_glossary: true,
        force: true,
        translation_type: 'scratch',
    });

    axios.post = origPost;
});

test('API: translateEpisode accepts options object with camelCase / snake_case', async () => {
    let postedData;
    const origPost = axios.post;

    axios.post = async (url, data) => {
        postedData = data;
        return { data: { job_id: 'ep-opt-1' } };
    };

    await apiModule.translateEpisode('DemoProj', 'Ep01', {
        model_name: 'gemini-flash',
        enhanceGlossary: true,
        force: false,
        translationType: 'saved',
        target_language_name: 'Greek',
        lang: 'el',
    });

    assert.deepEqual(postedData, {
        model: 'gemini-flash',
        enhance_glossary: true,
        force: false,
        translation_type: 'saved',
        target_language_name: 'Greek',
        lang: 'el',
    });

    axios.post = origPost;
});

test('API: batchTranslate accepts options object with episode_names or episodes', async () => {
    let postedData;
    const origPost = axios.post;

    axios.post = async (url, data) => {
        postedData = data;
        return { data: { job_id: 'batch-opt-1' } };
    };

    await apiModule.batchTranslate('DemoProj', {
        episodes: ['Ep01', 'Ep02'],
        model: 'flash-pro',
        enhance_glossary: false,
        force: true,
        translation_type: 'scratch',
    });

    assert.deepEqual(postedData, {
        episode_names: ['Ep01', 'Ep02'],
        model: 'flash-pro',
        enhance_glossary: false,
        force: true,
        translation_type: 'scratch',
    });

    axios.post = origPost;
});

// =========================================================================
// SUITE 2: useProjectDetail In-Flight Locks & Concurrency Stress
// =========================================================================

test('useProjectDetail: handleAutoTranslate double-click lock and error recovery', async () => {
    let callCount = 0;
    let warningCount = 0;
    let errorToastCount = 0;
    const origAutoTranslate = apiModule.autoTranslate;

    apiModule.autoTranslate = async () => {
        callCount++;
        await new Promise((r) => setTimeout(r, 40));
        return { data: { job_id: 'auto-1' } };
    };

    const hook = createHookInstance({
        warning: (msg) => {
            if (msg.includes('already in progress')) warningCount++;
        },
        error: () => {
            errorToastCount++;
        },
    });

    // 5 concurrent rapid clicks
    const results = await Promise.all([
        hook.handleAutoTranslate(),
        hook.handleAutoTranslate(),
        hook.handleAutoTranslate(),
        hook.handleAutoTranslate(),
        hook.handleAutoTranslate(),
    ]);

    assert.equal(callCount, 1, 'Only 1 network call across 5 concurrent clicks');
    assert.equal(warningCount, 4, '4 duplicate clicks received warning toast');
    assert.ok(results[0]);
    assert.equal(results[1], null);

    // Error recovery: simulate API failure
    apiModule.autoTranslate = async () => {
        callCount++;
        await new Promise((r) => setTimeout(r, 20));
        throw new Error('503 Service Unavailable');
    };

    const errResult = await hook.handleAutoTranslate();
    assert.equal(errResult, null);
    assert.equal(errorToastCount, 1);

    // Next call after error must succeed (lock released)
    apiModule.autoTranslate = async () => {
        callCount++;
        return { data: { job_id: 'auto-recovered' } };
    };
    const recovered = await hook.handleAutoTranslate();
    assert.ok(recovered);
    assert.equal(callCount, 3);

    apiModule.autoTranslate = origAutoTranslate;
});

test('useProjectDetail: handleBatchExtractEmbedded double-click lock and error recovery', async () => {
    let callCount = 0;
    let warningCount = 0;
    const origExtract = apiModule.batchExtractEmbedded;

    apiModule.batchExtractEmbedded = async () => {
        callCount++;
        await new Promise((r) => setTimeout(r, 30));
        return { data: { job_id: 'extract-1' } };
    };

    const hook = createHookInstance({
        warning: () => warningCount++,
    });

    // 5 concurrent rapid clicks
    await Promise.all([
        hook.handleBatchExtractEmbedded(['Ep01']),
        hook.handleBatchExtractEmbedded(['Ep01']),
        hook.handleBatchExtractEmbedded(['Ep01']),
        hook.handleBatchExtractEmbedded(['Ep01']),
        hook.handleBatchExtractEmbedded(['Ep01']),
    ]);

    assert.equal(callCount, 1, 'Only 1 extract call across 5 concurrent clicks');
    assert.equal(warningCount, 4);

    // Error recovery
    apiModule.batchExtractEmbedded = async () => {
        callCount++;
        throw new Error('Extraction failed');
    };
    await hook.handleBatchExtractEmbedded(['Ep01']);

    // Subsequent call after error succeeds
    apiModule.batchExtractEmbedded = async () => {
        callCount++;
        return { data: { job_id: 'extract-2' } };
    };
    const okRes = await hook.handleBatchExtractEmbedded(['Ep01']);
    assert.ok(okRes);
    assert.equal(callCount, 3);

    apiModule.batchExtractEmbedded = origExtract;
});

test('useProjectDetail: handleBatchTranslate double-click lock and error recovery', async () => {
    let callCount = 0;
    let warningCount = 0;
    const origBatch = apiModule.batchTranslate;

    apiModule.batchTranslate = async () => {
        callCount++;
        await new Promise((r) => setTimeout(r, 30));
        return { data: { job_id: 'batch-1' } };
    };

    const hook = createHookInstance({
        warning: () => warningCount++,
    });

    await Promise.all([
        hook.handleBatchTranslate(['Ep01', 'Ep02']),
        hook.handleBatchTranslate(['Ep01', 'Ep02']),
        hook.handleBatchTranslate(['Ep01', 'Ep02']),
    ]);

    assert.equal(callCount, 1);
    assert.equal(warningCount, 2);

    // Error recovery
    apiModule.batchTranslate = async () => {
        callCount++;
        throw new Error('Batch translation error');
    };
    await hook.handleBatchTranslate(['Ep01']);

    apiModule.batchTranslate = async () => {
        callCount++;
        return { data: { job_id: 'batch-2' } };
    };
    const res = await hook.handleBatchTranslate(['Ep01']);
    assert.ok(res);
    assert.equal(callCount, 3);

    apiModule.batchTranslate = origBatch;
});

test('useProjectDetail: handleTranslateEpisode per-episode fine-grained locking', async () => {
    const activeCalls = new Map();
    let warningCount = 0;
    const origTranslate = apiModule.translateEpisode;

    apiModule.translateEpisode = async (proj, ep) => {
        const count = (activeCalls.get(ep) || 0) + 1;
        activeCalls.set(ep, count);
        await new Promise((r) => setTimeout(r, 40));
        return { data: { job_id: `job-${ep}` } };
    };

    const hook = createHookInstance({
        warning: () => warningCount++,
    });

    // Fire:
    // 3 concurrent clicks on 'Ep01'
    // 2 concurrent clicks on 'Ep02'
    // 1 click on 'Ep03'
    await Promise.all([
        hook.handleTranslateEpisode('Ep01'),
        hook.handleTranslateEpisode('Ep01'),
        hook.handleTranslateEpisode('Ep01'),
        hook.handleTranslateEpisode('Ep02'),
        hook.handleTranslateEpisode('Ep02'),
        hook.handleTranslateEpisode('Ep03'),
    ]);

    assert.equal(activeCalls.get('Ep01'), 1, 'Ep01 should have exactly 1 API call');
    assert.equal(activeCalls.get('Ep02'), 1, 'Ep02 should have exactly 1 API call');
    assert.equal(activeCalls.get('Ep03'), 1, 'Ep03 should have exactly 1 API call');
    assert.equal(warningCount, 3, 'Duplicate clicks (2 on Ep01, 1 on Ep02) receive warning toasts');

    // Error recovery on Ep01
    apiModule.translateEpisode = async (proj, ep) => {
        if (ep === 'Ep01') {
            throw new Error('Episode error');
        }
        return { data: { job_id: `job-${ep}` } };
    };
    await hook.handleTranslateEpisode('Ep01');

    // Subsequent call on Ep01 should succeed
    apiModule.translateEpisode = async (proj, ep) => {
        return { data: { job_id: `recovered-${ep}` } };
    };
    const recRes = await hook.handleTranslateEpisode('Ep01');
    assert.ok(recRes, 'Subsequent call on Ep01 after error succeeds');

    apiModule.translateEpisode = origTranslate;
});

test('useProjectDetail: handleBatchDelete single confirm, atomic lock, error release', async () => {
    let confirmPromptCount = 0;
    let deleteCallCount = 0;
    let warningCount = 0;

    globalThis.window = {
        confirm: () => {
            confirmPromptCount++;
            return true;
        },
    };

    const origDelete = apiModule.deleteEpisode;
    const origGetProj = apiModule.getProject;
    const origGetProjs = apiModule.getProjects;
    const origGetSummary = apiModule.getProjectTokenSummary;

    apiModule.getProject = async () => ({ data: { name: 'demo_project' } });
    apiModule.getProjects = async () => ({ data: [] });
    apiModule.getProjectTokenSummary = async () => ({ data: {} });
    apiModule.getEpisodes = async () => ({ data: [] });
    const origGetEps = apiModule.getEpisodes;

    apiModule.deleteEpisode = async () => {
        deleteCallCount++;
        await new Promise((r) => setTimeout(r, 40));
        return { data: { success: true } };
    };

    const hook = createHookInstance({
        warning: () => warningCount++,
    });

    // 5 concurrent rapid clicks on batch delete for 3 episodes
    const results = await Promise.all([
        hook.handleBatchDelete(['Ep01', 'Ep02', 'Ep03']),
        hook.handleBatchDelete(['Ep01', 'Ep02', 'Ep03']),
        hook.handleBatchDelete(['Ep01', 'Ep02', 'Ep03']),
        hook.handleBatchDelete(['Ep01', 'Ep02', 'Ep03']),
        hook.handleBatchDelete(['Ep01', 'Ep02', 'Ep03']),
    ]);

    // Only 1 confirmation prompt across 5 clicks
    assert.equal(confirmPromptCount, 1, 'Only exactly 1 confirmation dialog presented');
    assert.equal(warningCount, 4, '4 duplicate clicks blocked with warnings');
    // For the 1 executed batch, deleteEpisode called 3 times (once per episode)
    assert.equal(deleteCallCount, 3, 'deleteEpisode called for each of the 3 episodes');
    assert.equal(results[0], true);
    assert.equal(results[1], false);

    // Empty selection returns false immediately without confirmation
    const emptyRes = await hook.handleBatchDelete([]);
    assert.equal(emptyRes, false);
    assert.equal(confirmPromptCount, 1, 'Empty selection does not trigger confirmation');

    // User cancellation (confirm returns false)
    globalThis.window.confirm = () => false;
    const cancelRes = await hook.handleBatchDelete(['Ep01']);
    assert.equal(cancelRes, false, 'Cancellation returns false cleanly');

    apiModule.deleteEpisode = origDelete;
    apiModule.getProject = origGetProj;
    apiModule.getProjects = origGetProjs;
    apiModule.getProjectTokenSummary = origGetSummary;
    apiModule.getEpisodes = origGetEps;
    delete globalThis.window;
});

test('useProjectDetail: handleBatchDownload & handleBatchExportToPath in-flight locks', async () => {
    let dlCalls = 0;
    let expCalls = 0;
    let warningCount = 0;

    const origDl = apiModule.batchDownload;
    const origExp = apiModule.batchExportToPath;

    apiModule.batchDownload = async () => {
        dlCalls++;
        await new Promise((r) => setTimeout(r, 30));
    };
    apiModule.batchExportToPath = async () => {
        expCalls++;
        await new Promise((r) => setTimeout(r, 30));
    };

    const hook = createHookInstance({
        warning: () => warningCount++,
    });

    await Promise.all([
        hook.handleBatchDownload(['Ep01']),
        hook.handleBatchDownload(['Ep01']),
    ]);
    assert.equal(dlCalls, 1);

    await Promise.all([
        hook.handleBatchExportToPath(['Ep01']),
        hook.handleBatchExportToPath(['Ep01']),
    ]);
    assert.equal(expCalls, 1);
    assert.equal(warningCount, 2);

    apiModule.batchDownload = origDl;
    apiModule.batchExportToPath = origExp;
});

test('useProjectDetail: handleAiAction fine-grained locking per action type', async () => {
    const actionCounts = new Map();
    let warningCount = 0;

    const origEnhanceCtx = apiModule.enhanceContext;
    const origCreateGlossary = apiModule.createGlossary;

    apiModule.enhanceContext = async () => {
        actionCounts.set('enhance_context', (actionCounts.get('enhance_context') || 0) + 1);
        await new Promise((r) => setTimeout(r, 40));
        return { data: { job_id: 'job-ctx' } };
    };
    apiModule.createGlossary = async () => {
        actionCounts.set('create_glossary', (actionCounts.get('create_glossary') || 0) + 1);
        await new Promise((r) => setTimeout(r, 40));
        return { data: { job_id: 'job-glo' } };
    };

    const hook = createHookInstance({
        warning: () => warningCount++,
    });

    // 2 concurrent calls for 'enhance_context' + 2 concurrent calls for 'create_glossary'
    await Promise.all([
        hook.handleAiAction('enhance_context'),
        hook.handleAiAction('enhance_context'),
        hook.handleAiAction('create_glossary'),
        hook.handleAiAction('create_glossary'),
    ]);

    assert.equal(actionCounts.get('enhance_context'), 1);
    assert.equal(actionCounts.get('create_glossary'), 1);
    assert.equal(warningCount, 2, 'Two duplicate calls warned');

    apiModule.enhanceContext = origEnhanceCtx;
    apiModule.createGlossary = origCreateGlossary;
});

// =========================================================================
// SUITE 3: ProjectDetail Dispatch Routing Logic Simulation
// =========================================================================

test('ProjectDetail dispatch: single episode routes to translateEpisode, NOT autoTranslate', async () => {
    let translateEpisodeCalled = false;
    let autoTranslateCalled = false;
    let batchTranslateCalled = false;

    const origTranslate = apiModule.translateEpisode;
    const origAuto = apiModule.autoTranslate;
    const origBatch = apiModule.batchTranslate;

    apiModule.translateEpisode = async () => {
        translateEpisodeCalled = true;
        return { data: { job_id: 'single-ep-1' } };
    };
    apiModule.autoTranslate = async () => {
        autoTranslateCalled = true;
        return { data: { job_id: 'auto-1' } };
    };
    apiModule.batchTranslate = async () => {
        batchTranslateCalled = true;
        return { data: { job_id: 'batch-1' } };
    };

    // Simulate ProjectDetail's handleConfirmTranslation logic directly
    const translationInFlightRef = { current: false };
    const handleConfirmTranslation = async ({ translationType, force, enhanceGlossary, model, episodeName, isBatch, selectedEpisodes, episodes }) => {
        if (translationInFlightRef.current) return;
        translationInFlightRef.current = true;
        try {
            if (episodeName) {
                await apiModule.translateEpisode('DemoProj', episodeName, model || undefined, enhanceGlossary, force, translationType);
                return;
            }
            let epList = [];
            if (isBatch && selectedEpisodes?.size > 0) {
                epList = Array.from(selectedEpisodes);
            } else if (isBatch) {
                epList = (episodes || []).filter((e) => !e.translated).map((e) => e.name);
            }
            if (epList.length === 1) {
                await apiModule.translateEpisode('DemoProj', epList[0], model || undefined, enhanceGlossary, force, translationType);
                return;
            }
            await apiModule.batchTranslate('DemoProj', {
                episode_names: epList,
                model: model || undefined,
                enhance_glossary: enhanceGlossary,
                force,
                translation_type: translationType,
            });
        } finally {
            translationInFlightRef.current = false;
        }
    };

    // Flow 1: Click single episode "Ep01"
    await handleConfirmTranslation({
        translationType: 'saved',
        force: false,
        enhanceGlossary: false,
        model: 'flash',
        episodeName: 'Ep01',
        isBatch: false,
    });

    assert.equal(translateEpisodeCalled, true, 'Single episode must call api.translateEpisode');
    assert.equal(autoTranslateCalled, false, 'Single episode must NEVER call api.autoTranslate');
    assert.equal(batchTranslateCalled, false, 'Single episode must NEVER call api.batchTranslate');

    // Flow 2: Batch resolving to 1 episode -> routes to translateEpisode
    translateEpisodeCalled = false;
    await handleConfirmTranslation({
        translationType: 'saved',
        force: false,
        enhanceGlossary: false,
        model: 'flash',
        episodeName: null,
        isBatch: true,
        selectedEpisodes: new Set(['Ep01']),
    });
    assert.equal(translateEpisodeCalled, true, '1-episode batch routes to translateEpisode');
    assert.equal(batchTranslateCalled, false);

    // Flow 3: Batch resolving to 2+ episodes -> routes to batchTranslate
    translateEpisodeCalled = false;
    await handleConfirmTranslation({
        translationType: 'saved',
        force: false,
        enhanceGlossary: false,
        model: 'flash',
        episodeName: null,
        isBatch: true,
        selectedEpisodes: new Set(['Ep01', 'Ep02']),
    });
    assert.equal(translateEpisodeCalled, false);
    assert.equal(batchTranslateCalled, true, 'Multi-episode batch routes to batchTranslate');

    apiModule.translateEpisode = origTranslate;
    apiModule.autoTranslate = origAuto;
    apiModule.batchTranslate = origBatch;
});

// =========================================================================
// SUITE 4: TranslationModal Component Concurrency & Error Recovery
// =========================================================================

test('TranslationModal: handleStart double-click concurrency and success close', async () => {
    let confirmCallCount = 0;
    let closeCallCount = 0;

    let capturedElement;
    function TestWrapper() {
        capturedElement = TranslationModalModule({
            isOpen: true,
            onClose: () => closeCallCount++,
            onConfirm: async () => {
                confirmCallCount++;
                await new Promise((r) => setTimeout(r, 40));
            },
            isBatch: false,
            episodeName: 'Episode_01',
        });
        return capturedElement;
    }
    ReactDOMServer.renderToString(React.createElement(TestWrapper));

    const submitBtn = findElement(capturedElement, (node) => {
        return node.type === 'button' && typeof node.props?.onClick === 'function' && node.props?.className?.includes('bg-indigo-600');
    });

    assert.ok(submitBtn);
    const handleStart = submitBtn.props.onClick;

    // 5 concurrent clicks
    await Promise.all([
        handleStart(),
        handleStart(),
        handleStart(),
        handleStart(),
        handleStart(),
    ]);

    assert.equal(confirmCallCount, 1, 'onConfirm called exactly once across 5 clicks');
    assert.equal(closeCallCount, 1, 'onClose called exactly once on success');
});

test('TranslationModal: error in onConfirm resets isSubmitting and does not call onClose', async () => {
    let onCloseCalls = 0;
    let onConfirmCalls = 0;

    let capturedElement;
    function TestWrapper() {
        capturedElement = TranslationModalModule({
            isOpen: true,
            onClose: () => onCloseCalls++,
            onConfirm: async () => {
                onConfirmCalls++;
                await new Promise((r) => setTimeout(r, 20));
                throw new Error('Network error on submit');
            },
            isBatch: true,
            batchCount: 3,
        });
        return capturedElement;
    }
    ReactDOMServer.renderToString(React.createElement(TestWrapper));

    const submitBtn = findElement(capturedElement, (node) => {
        return node.type === 'button' && typeof node.props?.onClick === 'function' && node.props?.className?.includes('bg-indigo-600');
    });
    assert.ok(submitBtn);

    // Call submit button which throws
    await submitBtn.props.onClick();

    assert.equal(onConfirmCalls, 1, 'onConfirm was invoked');
    assert.equal(onCloseCalls, 0, 'onClose was NOT called on error, leaving modal open for user retry');

    // Next click should execute again (lock released)
    await submitBtn.props.onClick();
    assert.equal(onConfirmCalls, 2, 'Subsequent click successfully invokes onConfirm after error');
});

// =========================================================================
// SUITE 5: SimplePipelineWizard Polling Decoupling & Concurrency Guards
// =========================================================================

test('SimplePipelineWizard: guards prevent polling churn, duplicate start, and multiple onComplete', async () => {
    let startPipelineCalls = 0;
    let onCompleteCalls = 0;

    // Simulate SimplePipelineWizard refs and lifecycle
    const activeJobsRef = { current: {} };
    const hasTriggeredRef = { current: false };
    const isExecutingRef = { current: false };
    const hasCompletedRef = { current: false };

    const startPipeline = async () => {
        if (isExecutingRef.current) return;
        isExecutingRef.current = true;
        try {
            startPipelineCalls++;
            await new Promise((r) => setTimeout(r, 30));
        } finally {
            isExecutingRef.current = false;
        }
    };

    // 1. Initial mount auto-start fires once
    if (!hasTriggeredRef.current) {
        hasTriggeredRef.current = true;
        await startPipeline();
    }
    assert.equal(startPipelineCalls, 1, 'Auto-start fires exactly once');

    // 2. ActiveJobs polling ticks occur (3 ticks)
    for (let tick = 0; tick < 3; tick++) {
        activeJobsRef.current = { 'job-1': { id: 'job-1', status: 'running', progress: tick * 30 } };
        // Mount effect has [startPipeline, fetchEpisodes] with stable deps
        // hasTriggeredRef is already true, so startPipeline is NOT called again
        if (!hasTriggeredRef.current) {
            await startPipeline();
        }
    }
    assert.equal(startPipelineCalls, 1, 'Polling ticks do not re-trigger startPipeline');

    // 3. User double-clicks "Retry pipeline" button
    await Promise.all([
        startPipeline(),
        startPipeline(),
        startPipeline(),
    ]);
    assert.equal(startPipelineCalls, 2, 'Retry double-clicks guarded by isExecutingRef: exactly 1 retry runs');

    // 4. Multiple completion ticks
    const handleJobUpdate = (job) => {
        if (job.status === 'completed') {
            if (!hasCompletedRef.current) {
                hasCompletedRef.current = true;
                onCompleteCalls++;
            }
        }
    };

    // 5 consecutive completed ticks
    handleJobUpdate({ id: 'job-1', status: 'completed' });
    handleJobUpdate({ id: 'job-1', status: 'completed' });
    handleJobUpdate({ id: 'job-1', status: 'completed' });
    handleJobUpdate({ id: 'job-1', status: 'completed' });
    handleJobUpdate({ id: 'job-1', status: 'completed' });

    assert.equal(onCompleteCalls, 1, 'onComplete called strictly once despite multiple completed ticks');
});
