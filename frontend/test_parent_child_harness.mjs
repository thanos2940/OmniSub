/**
 * Empirical Adversarial Verification Harness for Milestone 4 (R3):
 * Parent-Child Job Discovery, Root Filtering, and Accordion Observability.
 * 
 * Author: Challenger M4-1
 */

import assert from 'node:assert/strict';

console.log('--- STARTING EMPIRICAL PARENT-CHILD ADVERSARIAL HARNESS ---');

// ============================================================================
// 1. Re-implementation of exact algorithms from JobProgressWidget.jsx & JobContext.jsx
// ============================================================================

const TERMINAL_STATUSES = ['completed', 'failed', 'cancelled'];

const hasJobChanged = (prevJob, nextJob) => {
    if (!prevJob || !nextJob) return true;
    if (prevJob.status !== nextJob.status) return true;
    if (prevJob.parentId !== nextJob.parentId) return true;
    if (prevJob.progress !== nextJob.progress) return true;
    if (prevJob.message !== nextJob.message) return true;
    if (prevJob.pipeline_stage !== nextJob.pipeline_stage) return true;
    if (prevJob.pipeline_mode !== nextJob.pipeline_mode) return true;
    if (prevJob.logs?.length !== nextJob.logs?.length) return true;
    if (prevJob.eta_seconds !== nextJob.eta_seconds) return true;
    if (prevJob.cues_translated !== nextJob.cues_translated) return true;
    if (prevJob.total_cues !== nextJob.total_cues) return true;
    if (prevJob.cues_total !== nextJob.cues_total) return true;
    if (prevJob.current_scene !== nextJob.current_scene) return true;
    if (prevJob.total_scenes !== nextJob.total_scenes) return true;
    if (prevJob.scenes_done !== nextJob.scenes_done) return true;
    if (prevJob.scenes_total !== nextJob.scenes_total) return true;
    if (prevJob.daily_limit_reached !== nextJob.daily_limit_reached) return true;
    if (prevJob.rate_limit_hits !== nextJob.rate_limit_hits) return true;
    if (prevJob.completed_episodes?.length !== nextJob.completed_episodes?.length) return true;
    if (prevJob.failed_episodes?.length !== nextJob.failed_episodes?.length) return true;
    if (prevJob.project_name !== nextJob.project_name) return true;
    if (prevJob.episode_name !== nextJob.episode_name) return true;

    // Sub-jobs array equality
    const pSub = prevJob.sub_jobs || [];
    const nSub = nextJob.sub_jobs || [];
    if (pSub.length !== nSub.length) return true;
    for (let i = 0; i < nSub.length; i++) {
        if (String(pSub[i]) !== String(nSub[i])) return true;
    }

    const pScenes = prevJob.scene_status || {};
    const nScenes = nextJob.scene_status || {};
    const pKeys = Object.keys(pScenes);
    const nKeys = Object.keys(nScenes);
    if (pKeys.length !== nKeys.length) return true;
    for (const key of nKeys) {
        if (pScenes[key] !== nScenes[key]) return true;
    }

    return false;
};

const computeVisibleJobs = (activeJobs) => {
    const jobs = Object.values(activeJobs || {});
    const idSet = new Set();
    jobs.forEach(j => {
        if (Array.isArray(j?.sub_jobs)) {
            j.sub_jobs.forEach(subId => {
                const sid = String(subId);
                idSet.add(sid);
                idSet.add(`bg_${sid}`);
            });
        }
    });
    return jobs.filter(job => job && !job.parentId && !idSet.has(String(job.id)));
};

const isParentJob = (job) => {
    if (!job) return false;
    const parentTypes = ['batch_translate', 'auto_translate', 'simple_pipeline', 'pipeline'];
    if (parentTypes.includes(job.type)) return true;
    if (parentTypes.some(t => typeof job.id === 'string' && job.id.startsWith(`${t}_`))) return true;
    if (Array.isArray(job.sub_jobs) && job.sub_jobs.length > 0) return true;
    if ((job.completed_episodes?.length || 0) + (job.failed_episodes?.length || 0) > 0) return true;
    return false;
};

const getChildTasks = (parentJob, activeJobs = {}) => {
    if (!parentJob) return [];

    const tasksMap = new Map();
    const subJobIds = new Set((parentJob.sub_jobs || []).map(String));

    // 1. Scan activeJobs for children associated with this parent
    Object.values(activeJobs).forEach(job => {
        if (!job) return;
        const sid = String(job.id);
        const bareSid = sid.replace(/^bg_/, '');
        const isChild = job.parentId === parentJob.id ||
                        subJobIds.has(sid) ||
                        subJobIds.has(bareSid);
        if (isChild) {
            const epName = job.episode_name || job.metadata?.episodeName || `Item ${job.id}`;
            tasksMap.set(epName, {
                id: job.id,
                episodeName: epName,
                status: job.status || 'running',
                progress: typeof job.progress === 'number' ? job.progress : 0,
                cues_translated: job.cues_translated ?? null,
                total_cues: job.total_cues ?? job.cues_total ?? null,
                logs: Array.isArray(job.logs) ? job.logs : [],
                hasLogs: Boolean(job.logs && job.logs.length > 0),
                message: job.message || '',
                scene_status: job.scene_status || {},
                jobRef: job
            });
        }
    });

    // 2. Incorporate planned episodes from metadata if present
    const metaEpisodes = parentJob.metadata?.episodes || parentJob.metadata?.episodeNames || [];
    metaEpisodes.forEach(epName => {
        if (!tasksMap.has(epName)) {
            tasksMap.set(epName, {
                id: `ep_${epName}`,
                episodeName: epName,
                status: 'pending',
                progress: 0,
                cues_translated: null,
                total_cues: null,
                logs: [],
                hasLogs: false,
                message: 'Pending in queue',
                scene_status: {},
                jobRef: null
            });
        }
    });

    // 3. Mark completed episodes from parent telemetry
    (parentJob.completed_episodes || []).forEach(epName => {
        const existing = tasksMap.get(epName);
        if (existing) {
            existing.status = 'completed';
            existing.progress = 100;
        } else {
            tasksMap.set(epName, {
                id: `completed_${epName}`,
                episodeName: epName,
                status: 'completed',
                progress: 100,
                cues_translated: null,
                total_cues: null,
                logs: [],
                hasLogs: false,
                message: 'Translation completed',
                scene_status: {},
                jobRef: null
            });
        }
    });

    // 4. Mark failed episodes from parent telemetry
    (parentJob.failed_episodes || []).forEach(epName => {
        const existing = tasksMap.get(epName);
        if (existing) {
            existing.status = 'failed';
        } else {
            tasksMap.set(epName, {
                id: `failed_${epName}`,
                episodeName: epName,
                status: 'failed',
                progress: 0,
                cues_translated: null,
                total_cues: null,
                logs: [],
                hasLogs: false,
                message: 'Translation failed',
                scene_status: {},
                jobRef: null
            });
        }
    });

    // 5. Fallback: if no episode names were available but sub_jobs exist
    if (tasksMap.size === 0 && Array.isArray(parentJob.sub_jobs)) {
        parentJob.sub_jobs.forEach(subId => {
            const sid = String(subId);
            const childJob = activeJobs[sid] || activeJobs[`bg_${sid}`];
            const epName = childJob?.episode_name || childJob?.metadata?.episodeName || `Queue #${sid}`;
            tasksMap.set(sid, {
                id: childJob?.id || `bg_${sid}`,
                episodeName: epName,
                status: childJob?.status || 'pending',
                progress: childJob?.progress || 0,
                cues_translated: childJob?.cues_translated ?? null,
                total_cues: childJob?.total_cues ?? childJob?.cues_total ?? null,
                logs: childJob?.logs || [],
                hasLogs: Boolean(childJob?.logs?.length),
                message: childJob?.message || '',
                scene_status: childJob?.scene_status || {},
                jobRef: childJob || null
            });
        });
    }

    return Array.from(tasksMap.values());
};

const getSortedScenes = (sceneStatus) => {
    if (!sceneStatus || typeof sceneStatus !== 'object') return [];
    return Object.entries(sceneStatus).sort(([keyA], [keyB]) => {
        const numA = parseInt(keyA.replace(/\D+/g, ''), 10);
        const numB = parseInt(keyB.replace(/\D+/g, ''), 10);
        if (!isNaN(numA) && !isNaN(numB)) {
            return numA - numB;
        }
        return keyA.localeCompare(keyB, undefined, { numeric: true });
    });
};

const resolveParentTier123 = (item, currentJobs) => {
    const itemIdStr = String(item.id);
    const bgJobId = `bg_${item.id}`;
    const projectName = item.project_name || null;
    const episodeName = item.episode_name || null;
    let parentJobId = null;

    // Tier 1: Check known sub_jobs across all active jobs
    for (const [pId, pJob] of Object.entries(currentJobs)) {
        if (!pJob) continue;
        const subList = pJob.sub_jobs || pJob.metadata?.sub_job_ids || pJob.metadata?.child_jobs;
        if (Array.isArray(subList)) {
            if (subList.some(s => String(s) === itemIdStr || String(s) === bgJobId)) {
                parentJobId = pId;
                break;
            }
        }
    }

    // Tier 2: Check item.options for parent_job_id
    if (!parentJobId && item.options) {
        try {
            const opts = typeof item.options === 'string' ? JSON.parse(item.options) : item.options;
            if (opts.parent_job_id && currentJobs[opts.parent_job_id]) {
                parentJobId = opts.parent_job_id;
            }
        } catch {}
    }

    // Tier 3: Correlate with in-flight parent jobs for same project
    if (!parentJobId && projectName) {
        for (const [pId, pJob] of Object.entries(currentJobs)) {
            if (!pJob || TERMINAL_STATUSES.includes(pJob.status) || pJob.parentId) continue;
            if (['batch_translate', 'auto_translate', 'simple_pipeline', 'pipeline'].includes(pJob.type)) {
                const pProj = pJob.project_name || pJob.metadata?.projectId;
                if (pProj && pProj.toLowerCase() === projectName.toLowerCase()) {
                    const epList = pJob.metadata?.episodeNames;
                    if (Array.isArray(epList) && episodeName) {
                        if (epList.some(e => e.toLowerCase() === episodeName.toLowerCase())) {
                            parentJobId = pId;
                            break;
                        }
                    } else {
                        parentJobId = pId;
                        break;
                    }
                }
            }
        }
    }

    return parentJobId;
};

// ============================================================================
// TEST 1: 5-Episode Batch Translate Pre-Seeding & Root Filtering
// ============================================================================
console.log('\n[Test 1] 5-Episode Batch Translate registers exactly 1 root job...');
{
    const subJobIds = [101, 102, 103, 104, 105];
    const episodeNames = ['S01E01', 'S01E02', 'S01E03', 'S01E04', 'S01E05'];
    const parentJobId = 'batch_translate_test_123';

    // Simulate addJob dispatch
    const activeJobs = {
        [parentJobId]: {
            id: parentJobId,
            type: 'batch_translate',
            description: 'Batch Translate: 5 episodes',
            metadata: {
                projectId: 'TestProject',
                sub_job_ids: subJobIds,
                episodeNames: episodeNames
            },
            parentId: null,
            sub_jobs: subJobIds.map(String),
            status: 'running',
            progress: 20
        }
    };

    // Pre-seed children (as addJob does)
    subJobIds.forEach((childId, idx) => {
        const childKey = `bg_${childId}`;
        activeJobs[childKey] = {
            id: childKey,
            type: 'translate_episode',
            description: `Worker: ${episodeNames[idx]}`,
            metadata: {
                projectId: 'TestProject',
                episodeName: episodeNames[idx],
                parentId: parentJobId
            },
            parentId: parentJobId,
            status: idx === 0 ? 'running' : 'pending',
            progress: idx === 0 ? 35 : 0,
            cues_translated: idx === 0 ? 70 : 0,
            total_cues: 200,
            logs: [`Translating cue ${idx * 10}`]
        };
    });

    assert.equal(Object.keys(activeJobs).length, 6, 'Total activeJobs must be 6 (1 parent + 5 children)');

    const visible = computeVisibleJobs(activeJobs);
    assert.equal(visible.length, 1, `visibleJobs must contain exactly 1 root job, got ${visible.length}`);
    assert.equal(visible[0].id, parentJobId, 'Root job must be the batch_translate job');

    // Verify children are inside accordion
    assert.ok(isParentJob(visible[0]), 'Batch job must be recognized as parent job');
    const childTasks = getChildTasks(visible[0], activeJobs);
    assert.equal(childTasks.length, 5, `getChildTasks must return 5 child tasks, got ${childTasks.length}`);

    // Verify status pills, cue counts, and log buttons metadata
    const firstChild = childTasks.find(c => c.episodeName === 'S01E01');
    assert.ok(firstChild, 'S01E01 child task must exist');
    assert.equal(firstChild.status, 'running');
    assert.equal(firstChild.cues_translated, 70);
    assert.equal(firstChild.total_cues, 200);
    assert.equal(firstChild.hasLogs, true);

    const secondChild = childTasks.find(c => c.episodeName === 'S01E02');
    assert.ok(secondChild, 'S01E02 child task must exist');
    assert.equal(secondChild.status, 'pending');

    console.log('✓ PASS: 5-episode batch registered exactly 1 root job in visibleJobs, 5 child tasks in accordion');
}

// ============================================================================
// TEST 2: Standalone Jobs Without Parents Remain Root Cards
// ============================================================================
console.log('\n[Test 2] Standalone jobs without parents remain root cards...');
{
    const activeJobs = {
        'batch_parent_1': {
            id: 'batch_parent_1',
            type: 'batch_translate',
            parentId: null,
            sub_jobs: ['10', '11'],
            status: 'running',
            progress: 50
        },
        'bg_10': {
            id: 'bg_10',
            type: 'translate_episode',
            parentId: 'batch_parent_1',
            status: 'running',
            episode_name: 'S01E01'
        },
        'bg_11': {
            id: 'bg_11',
            type: 'translate_episode',
            parentId: 'batch_parent_1',
            status: 'pending',
            episode_name: 'S01E02'
        },
        'bg_99_standalone': {
            id: 'bg_99_standalone',
            type: 'translate_episode',
            parentId: null, // Standalone external job
            status: 'running',
            episode_name: 'S02E01',
            description: 'Worker: S02E01'
        },
        'extract_standalone_55': {
            id: 'extract_standalone_55',
            type: 'batch_extract_embedded',
            parentId: null,
            status: 'running',
            description: 'Extract Subs: S01'
        }
    };

    const visible = computeVisibleJobs(activeJobs);
    assert.equal(visible.length, 3, `visibleJobs must contain 3 root cards, got ${visible.length}`);
    const visibleIds = visible.map(j => j.id).sort();
    assert.deepEqual(visibleIds, ['batch_parent_1', 'bg_99_standalone', 'extract_standalone_55'].sort());

    console.log('✓ PASS: Standalone jobs (bg_99_standalone, extract_standalone_55) remain independent root cards');
}

// ============================================================================
// TEST 3: 3-Tier Parent Resolution Engine
// ============================================================================
console.log('\n[Test 3] 3-Tier Parent Resolution Engine...');
{
    const currentJobs = {
        'parent_batch_tier1': {
            id: 'parent_batch_tier1',
            type: 'batch_translate',
            sub_jobs: ['201', '202'],
            status: 'running'
        },
        'parent_auto_tier2': {
            id: 'parent_auto_tier2',
            type: 'auto_translate',
            sub_jobs: [],
            status: 'running'
        },
        'parent_pipe_tier3': {
            id: 'parent_pipe_tier3',
            type: 'simple_pipeline',
            project_name: 'AnimeProject',
            metadata: {
                projectId: 'AnimeProject',
                episodeNames: ['S01E10', 'S01E11']
            },
            sub_jobs: [],
            status: 'running'
        }
    };

    // Case 1: Tier 1 match (sub_jobs list)
    const item1 = { id: 201, project_name: 'Other', episode_name: 'Ep1' };
    const p1 = resolveParentTier123(item1, currentJobs);
    assert.equal(p1, 'parent_batch_tier1', 'Tier 1 must match via sub_jobs');

    // Case 2: Tier 2 match (options.parent_job_id)
    const item2 = { id: 301, project_name: 'Other', options: JSON.stringify({ parent_job_id: 'parent_auto_tier2' }) };
    const p2 = resolveParentTier123(item2, currentJobs);
    assert.equal(p2, 'parent_auto_tier2', 'Tier 2 must match via options.parent_job_id');

    // Case 3: Tier 3 match (project & episode correlation)
    const item3 = { id: 401, project_name: 'AnimeProject', episode_name: 'S01E10' };
    const p3 = resolveParentTier123(item3, currentJobs);
    assert.equal(p3, 'parent_pipe_tier3', 'Tier 3 must match via project + episode correlation');

    // Case 4: Standalone unparented queue item
    const item4 = { id: 501, project_name: 'UnrelatedProject', episode_name: 'S99E99' };
    const p4 = resolveParentTier123(item4, currentJobs);
    assert.equal(p4, null, 'Unrelated item must resolve to null parentId');

    console.log('✓ PASS: All 3 tiers of parent resolution operate deterministically');
}

// ============================================================================
// TEST 4: Race Condition / Late Arrival Self-Healing Reconciliation
// ============================================================================
console.log('\n[Test 4] Late arrival & self-healing reconciliation...');
{
    // Child arrived FIRST via queue poll before parent sub_jobs was received
    const activeJobs = {
        'bg_777': {
            id: 'bg_777',
            type: 'translate_episode',
            parentId: null, // Initially unparented!
            status: 'running',
            episode_name: 'S01E01'
        },
        'batch_parent_late': {
            id: 'batch_parent_late',
            type: 'batch_translate',
            parentId: null,
            sub_jobs: null, // sub_jobs not synced yet
            status: 'pending'
        }
    };

    // At this moment, both appear in visibleJobs
    let visible = computeVisibleJobs(activeJobs);
    assert.equal(visible.length, 2, 'Before sync, both appear');

    // Now parent polls and receives sub_jobs = ['777']
    const s = { sub_jobs: ['777'] };
    // Trigger reconciliation algorithm from JobContext.jsx:488-501
    s.sub_jobs.forEach(subId => {
        const childIdStr = String(subId);
        const childKey = childIdStr.startsWith('bg_') ? childIdStr : `bg_${childIdStr}`;
        if (activeJobs[childKey] && activeJobs[childKey].parentId !== 'batch_parent_late') {
            activeJobs[childKey] = {
                ...activeJobs[childKey],
                parentId: 'batch_parent_late'
            };
        }
    });
    activeJobs['batch_parent_late'].sub_jobs = s.sub_jobs;

    // After reconciliation
    visible = computeVisibleJobs(activeJobs);
    assert.equal(visible.length, 1, 'After reconciliation, visibleJobs must contain exactly 1 job');
    assert.equal(visible[0].id, 'batch_parent_late');
    assert.equal(activeJobs['bg_777'].parentId, 'batch_parent_late');

    console.log('✓ PASS: Late child arrival self-healed and eliminated duplicate root card');
}

// ============================================================================
// TEST 5: Cascading Removal & Cancellation
// ============================================================================
console.log('\n[Test 5] Cascading removal & cancellation...');
{
    const activeJobs = {
        'parent_cancel': { id: 'parent_cancel', status: 'running', parentId: null },
        'bg_c1': { id: 'bg_c1', status: 'running', parentId: 'parent_cancel' },
        'bg_c2': { id: 'bg_c2', status: 'pending', parentId: 'parent_cancel' },
        'standalone_safe': { id: 'standalone_safe', status: 'running', parentId: null }
    };

    // Simulate cancelJob logic from JobContext.jsx:201-233
    const parentId = 'parent_cancel';
    const now = Date.now();
    activeJobs[parentId] = {
        ...activeJobs[parentId],
        status: 'cancelled',
        finished_at: now
    };
    for (const [id, j] of Object.entries(activeJobs)) {
        if (j.parentId === parentId && !TERMINAL_STATUSES.includes(j.status)) {
            activeJobs[id] = {
                ...j,
                status: 'cancelled',
                finished_at: now
            };
        }
    }

    assert.equal(activeJobs['parent_cancel'].status, 'cancelled');
    assert.equal(activeJobs['bg_c1'].status, 'cancelled');
    assert.equal(activeJobs['bg_c2'].status, 'cancelled');
    assert.equal(activeJobs['standalone_safe'].status, 'running', 'Standalone job must not be cancelled');

    // Simulate removeJob logic from JobContext.jsx:183-199
    delete activeJobs[parentId];
    for (const [id, j] of Object.entries(activeJobs)) {
        if (j.parentId === parentId) {
            delete activeJobs[id];
        }
    }

    assert.equal(activeJobs['parent_cancel'], undefined);
    assert.equal(activeJobs['bg_c1'], undefined);
    assert.equal(activeJobs['bg_c2'], undefined);
    assert.ok(activeJobs['standalone_safe'], 'Standalone job must remain untouched');

    console.log('✓ PASS: Cascading cancellation and dismissal cleanly purged children without touching standalone jobs');
}

// ============================================================================
// TEST 6: Scene Numerical Sorting & Auto-Scroll Detection Math
// ============================================================================
console.log('\n[Test 6] Scene sorting & auto-scroll math...');
{
    const rawScenes = {
        'Scene 10': 'completed',
        'Scene 1': 'completed',
        'Scene 2': 'running',
        'Scene 20': 'pending',
        'Scene 3': 'pending'
    };

    const sorted = getSortedScenes(rawScenes);
    const sortedNames = sorted.map(([s]) => s);
    assert.deepEqual(sortedNames, ['Scene 1', 'Scene 2', 'Scene 3', 'Scene 10', 'Scene 20'],
        'Scenes must sort numerically: 1, 2, 3, 10, 20 (not lexicographically 1, 10, 2, 20, 3)');

    // Test auto-scroll threshold formula: scrollHeight - (scrollTop + clientHeight) <= 35
    const atBottom = (scrollHeight, scrollTop, clientHeight) => {
        return (scrollHeight - (scrollTop + clientHeight)) <= 35;
    };

    assert.equal(atBottom(1000, 700, 300), true, 'Distance 0 -> true');
    assert.equal(atBottom(1000, 670, 300), true, 'Distance 30 <= 35 -> true');
    assert.equal(atBottom(1000, 665, 300), true, 'Distance 35 <= 35 -> true');
    assert.equal(atBottom(1000, 660, 300), false, 'Distance 40 > 35 -> false (user scrolled up)');

    console.log('✓ PASS: Scenes sort numerically and auto-scroll distance threshold operates accurately');
}

// ============================================================================
// TEST 7: Massive Stress Scale (50-Episode Batch)
// ============================================================================
console.log('\n[Test 7] Massive Stress Scale: 50-Episode Batch...');
{
    const subJobIds = Array.from({ length: 50 }, (_, i) => 1000 + i);
    const episodeNames = Array.from({ length: 50 }, (_, i) => `S01E${String(i + 1).padStart(2, '0')}`);
    const parentJobId = 'batch_huge_50';

    const activeJobs = {
        [parentJobId]: {
            id: parentJobId,
            type: 'batch_translate',
            parentId: null,
            sub_jobs: subJobIds.map(String),
            metadata: { episodeNames },
            status: 'running',
            progress: 10
        }
    };

    subJobIds.forEach((id, idx) => {
        activeJobs[`bg_${id}`] = {
            id: `bg_${id}`,
            parentId: parentJobId,
            type: 'translate_episode',
            episode_name: episodeNames[idx],
            status: idx < 5 ? 'completed' : (idx === 5 ? 'running' : 'pending'),
            progress: idx < 5 ? 100 : (idx === 5 ? 50 : 0),
            cues_translated: idx < 5 ? 300 : (idx === 5 ? 150 : 0),
            total_cues: 300
        };
    });

    const start = performance.now();
    const visible = computeVisibleJobs(activeJobs);
    const duration = performance.now() - start;

    assert.equal(visible.length, 1, 'Only 1 root job for 50 episodes');
    const tasks = getChildTasks(visible[0], activeJobs);
    assert.equal(tasks.length, 50, 'All 50 child tasks retrieved');
    assert.ok(duration < 10, `Root filtering took ${duration.toFixed(2)}ms, must be < 10ms`);

    const completed = tasks.filter(t => t.status === 'completed');
    const running = tasks.filter(t => t.status === 'running');
    const pending = tasks.filter(t => t.status === 'pending');
    assert.equal(completed.length, 5);
    assert.equal(running.length, 1);
    assert.equal(pending.length, 44);

    console.log(`✓ PASS: 50-episode stress batch completed in ${duration.toFixed(2)}ms with exactly 1 root card and 50 child tasks`);
}

console.log('\n======================================================');
console.log('ALL EMPIRICAL ADVERSARIAL HARNESS TESTS PASSED (7/7)!');
console.log('======================================================');
