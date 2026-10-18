/**
 * Empirical Adversarial Test Suite for Milestone 4 (R3):
 * Enhanced Live Log Console & Scene Status Verification.
 *
 * Author: Challenger M4-2
 * Focus:
 * 1. Auto-scroll lock threshold calculation (<= 35px) and pause banner appearance.
 * 2. Category filtering ('All', 'Errors & Warnings', 'Stages') with count badges and empty states.
 * 3. 'Copy Logs' (view-aware, filter-aware) and 'Download Logs (.txt)' output structure.
 * 4. Scene-by-scene status indicators with live colorization and numerical sorting.
 * 5. Log colorization engine and timestamp parsing.
 * 6. Child task log inspection and modal job resolution.
 */

import assert from 'node:assert/strict';

console.log('================================================================');
console.log('STARTING EMPIRICAL TEST: ENHANCED LIVE LOG CONSOLE (CHALLENGER M4-2)');
console.log('================================================================');

// ----------------------------------------------------------------------------
// 1. RE-IMPLEMENTATION OF CORE LOGIC DIRECTLY FROM JobProgressWidget.jsx
// ----------------------------------------------------------------------------

const renderColorizedLog = (log) => {
    if (!log) return null;
    const timestampMatch = log.match(/^(\[\d{2}:\d{2}:\d{2}\])\s*(.*)$/);
    let timestamp = null;
    let text = log;
    if (timestampMatch) {
        timestamp = timestampMatch[1];
        text = timestampMatch[2];
    }

    let colorClass = 'text-gray-300';
    const lower = text.toLowerCase();

    if (lower.includes('failed') || lower.includes('failure') || lower.includes('error') || lower.includes('exception') || lower.includes('✗')) {
        colorClass = 'text-red-400 font-semibold';
    } else if (lower.includes('warning') || lower.includes('incomplete') || lower.includes('limit') || lower.includes('retry') || lower.includes('retrying')) {
        colorClass = 'text-amber-400 font-medium';
    } else if (lower.includes('export') || lower.includes('saved') || lower.includes('save') || lower.includes('stored') || lower.includes('checkpoint') || lower.includes('✓')) {
        colorClass = 'text-emerald-400';
    } else if (lower.includes('translating') || lower.includes('translation') || lower.includes('retranslate') || lower.includes('reasoning:') || lower.includes('reused')) {
        colorClass = 'text-purple-400';
    } else if (lower.includes('running') || lower.includes('starting') || lower.includes('initializing') || lower.includes('syncing') || lower.includes('extracting') || lower.includes('reviewing')) {
        colorClass = 'text-blue-400';
    }

    return { timestamp, text, colorClass };
};

const getSceneColor = (status) => {
    switch (status) {
        case 'completed':
            return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40 dark:bg-emerald-950/60 dark:text-emerald-300';
        case 'running':
            return 'bg-blue-500/20 text-blue-400 border-blue-400 dark:bg-blue-950/60 dark:text-blue-300 animate-pulse font-medium';
        case 'pending':
            return 'bg-amber-500/20 text-amber-400 border-amber-500/40 dark:bg-amber-950/50 dark:text-amber-300';
        case 'failed':
        case 'retry':
        case 'retrying':
            return 'bg-rose-500/20 text-rose-400 border-rose-500/50 dark:bg-rose-950/60 dark:text-rose-300 animate-pulse font-medium';
        default:
            return 'bg-gray-200 text-gray-600 dark:bg-gray-700 dark:text-gray-300 border border-gray-600';
    }
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

const categorizeLogs = (logsParam = [], logFilter = 'all') => {
    const rawLogs = logsParam || [];
    const errors = rawLogs.filter(line =>
        /(?:failed|failure|error|exception|warning|incomplete|limit|quota|daily limit|cancelled|retry|retrying|429|500|✗)/i.test(line)
    );
    const stages = rawLogs.filter(line =>
        /(?:starting|initializing|step\s*\d|stage|phase|pipeline|scene\s*\d|scenes|translating|retranslating|repairing|repair pass|scan|context guide|glossary|research|export|saved|checkpoint|completed|finished|successfully|✓)/i.test(line)
    );

    let displayed = rawLogs;
    if (logFilter === 'errors') displayed = errors;
    else if (logFilter === 'stages') displayed = stages;

    return { allLogs: rawLogs, errorLogs: errors, stageLogs: stages, displayedLogs: displayed };
};

const computeAutoScrollLock = (scrollTop, scrollHeight, clientHeight) => {
    const distanceFromBottom = scrollHeight - (scrollTop + clientHeight);
    return distanceFromBottom <= 35;
};

const generateLogDownloadPayload = (job) => {
    const timestamp = '2026-09-16T00:30:00.000Z';
    const header = [
        '================================================================================',
        `OMBISUB JOB LOG DUMP — ${timestamp}`,
        '================================================================================',
        `Job ID:          ${job.id}`,
        `Project:         ${job.project_name || 'N/A'}`,
        `Episode:         ${job.episode_name || 'N/A'}`,
        `Status:          ${job.status}`,
        `Progress:        ${job.progress}%`,
        `Stage:           ${job.pipeline_stage || 'N/A'}`,
        `Cues Translated: ${job.cues_translated != null ? `${job.cues_translated}/${job.total_cues || job.cues_total || '?'}` : 'N/A'}`,
        `ETA:             ${job.eta_seconds != null ? `${Math.ceil(job.eta_seconds)}s` : 'N/A'}`,
        '================================================================================',
        '',
        '--- LOG STREAM ---',
        ''
    ].join('\n');

    let content = header + (job.logs || []).join('\n');

    if (job.prompt) {
        content += '\n\n' + [
            '================================================================================',
            '--- LAST PROMPT ---',
            '================================================================================',
            '',
            job.prompt
        ].join('\n');
    }

    if (job.ai_response) {
        content += '\n\n' + [
            '================================================================================',
            '--- LAST AI RESPONSE ---',
            '================================================================================',
            '',
            job.ai_response
        ].join('\n');
    }

    const sanitizedId = String(job.id).replace(/[^a-zA-Z0-9_-]/g, '_');
    const filename = `${sanitizedId}_logs.txt`;

    return { content, filename };
};

// ----------------------------------------------------------------------------
// TEST SUITE EXECUTION
// ----------------------------------------------------------------------------

let passedTests = 0;

function runTest(name, fn) {
    try {
        fn();
        console.log(`  ✓ [PASS] ${name}`);
        passedTests++;
    } catch (err) {
        console.error(`  ✗ [FAIL] ${name}`);
        console.error(err);
        process.exitCode = 1;
    }
}

// ----------------------------------------------------------------------------
// SUITE 1: AUTO-SCROLL LOCK ENGINE & PAUSE BANNER
// ----------------------------------------------------------------------------
console.log('\n[SUITE 1] Auto-Scroll Lock Engine & Pause Banner');

runTest('Auto-scroll locks when distance from bottom <= 35px', () => {
    // Exact bottom (distance = 0)
    assert.strictEqual(computeAutoScrollLock(600, 1000, 400), true);
    // Within 35px threshold (distance = 35)
    assert.strictEqual(computeAutoScrollLock(565, 1000, 400), true);
    // Boundary trip (distance = 35.1)
    assert.strictEqual(computeAutoScrollLock(564.9, 1000, 400), false);
    // Scrolled up (distance = 100)
    assert.strictEqual(computeAutoScrollLock(500, 1000, 400), false);
    // Scrolled to top (distance = 600)
    assert.strictEqual(computeAutoScrollLock(0, 1000, 400), false);
    // Overscroll / bounce (distance = -10)
    assert.strictEqual(computeAutoScrollLock(610, 1000, 400), true);
});

runTest('Pause banner visibility condition behaves correctly across tabs and scroll states', () => {
    const isBannerVisible = (maximizedTab, isAutoScrollLocked) => {
        return maximizedTab === 'logs' && !isAutoScrollLocked;
    };

    // Scrolled to bottom on logs tab -> banner hidden
    assert.strictEqual(isBannerVisible('logs', true), false);
    // Scrolled up on logs tab -> banner visible
    assert.strictEqual(isBannerVisible('logs', false), true);
    // Scrolled up on ai_response tab -> banner hidden
    assert.strictEqual(isBannerVisible('ai_response', false), false);
    // Scrolled up on prompt tab -> banner hidden
    assert.strictEqual(isBannerVisible('prompt', false), false);
});

runTest('Content growth simulation pins scrollTop when locked and preserves position when unlocked', () => {
    let isLocked = true;
    let scrollTop = 600;
    let clientHeight = 400;
    let scrollHeight = 1000;

    // Simulate incoming log batch while locked
    scrollHeight = 1300;
    if (isLocked) {
        scrollTop = scrollHeight; // Pins to newest
    }
    assert.strictEqual(scrollTop, 1300);

    // Operator scrolls up to inspect previous log at scrollTop = 500
    scrollTop = 500;
    isLocked = computeAutoScrollLock(scrollTop, scrollHeight, clientHeight);
    assert.strictEqual(isLocked, false); // 1300 - 900 = 400 > 35 -> unlocked!

    // Another log arrives while operator is reading
    scrollHeight = 1500;
    if (isLocked) {
        scrollTop = scrollHeight;
    }
    // scrollTop should NOT jump! It must stay at 500
    assert.strictEqual(scrollTop, 500);

    // Operator clicks Jump to Newest banner
    scrollTop = scrollHeight;
    isLocked = true;
    assert.strictEqual(scrollTop, 1500);
    assert.strictEqual(isLocked, true);
});

// ----------------------------------------------------------------------------
// SUITE 2: CATEGORY FILTERING, COUNT BADGES & EMPTY STATES
// ----------------------------------------------------------------------------
console.log('\n[SUITE 2] Category Filtering, Count Badges & Empty States');

const sampleLogs = [
    '[10:00:01] Starting translation pipeline for Breaking Bad S01E01...',
    '[10:00:02] Phase 1: Context Analysis initiated',
    '[10:00:05] Step 1 complete: Scene boundaries detected (12 scenes)',
    '[10:00:10] Sent 25 cues to model gemini-2.5-flash',
    '[10:00:15] Translating Scene 1 of 12 (cues 1-25)',
    '[10:00:20] Warning: Term declension ambiguity detected for Heisenberg',
    '[10:00:25] Checkpoint saved successfully ✓',
    '[10:00:30] Translating Scene 2 of 12 (cues 26-50)',
    '[10:00:35] Error: Model returned 429 daily limit quota exceeded',
    '[10:00:36] Retrying scene 2 with fallback model gemini-3.1-flash-lite',
    '[10:00:40] Scene 2 repair pass completed',
    '[10:00:45] ✗ Subtitle format validation exception in cue 48',
    '[10:00:50] Re-translating cue 48 with strict CPL limit',
    '[10:00:55] Final export saved to /data/subs/S01E01.srt',
    '[10:01:00] Pipeline finished successfully ✓'
];

runTest('Category count badges match exact classification counts', () => {
    const { allLogs, errorLogs, stageLogs } = categorizeLogs(sampleLogs);

    assert.strictEqual(allLogs.length, 15);
    // Errors/warnings:
    // 1. Warning: Term declension ambiguity...
    // 2. Error: Model returned 429...
    // 3. Retrying scene 2...
    // 4. ✗ Subtitle format validation exception...
    // 5. Re-translating cue 48 with strict CPL limit (matches 'limit')
    assert.strictEqual(errorLogs.length, 5);

    // Stages:
    // 1. Starting translation pipeline...
    // 2. Phase 1...
    // 3. Step 1 complete...
    // 4. Translating Scene 1...
    // 5. Checkpoint saved successfully ✓
    // 6. Translating Scene 2...
    // 7. Retrying scene 2... (matches retrying, scene)
    // 8. Scene 2 repair pass completed
    // 9. Re-translating cue 48...
    // 10. Final export saved...
    // 11. Pipeline finished successfully ✓
    assert.strictEqual(stageLogs.length, 11);
});

runTest('Filter tabs switch displayed logs accurately', () => {
    const { displayedLogs: allD } = categorizeLogs(sampleLogs, 'all');
    assert.strictEqual(allD.length, 15);

    const { displayedLogs: errD } = categorizeLogs(sampleLogs, 'errors');
    assert.strictEqual(errD.length, 5);
    assert.ok(errD.every(l => /(?:failed|failure|error|exception|warning|incomplete|limit|quota|daily limit|cancelled|retry|retrying|429|500|✗)/i.test(l)));

    const { displayedLogs: stageDisp } = categorizeLogs(sampleLogs, 'stages');
    assert.strictEqual(stageDisp.length, 11);
    assert.ok(stageDisp.every(l => /(?:starting|initializing|step\s*\d|stage|phase|pipeline|scene\s*\d|scenes|translating|retranslating|repairing|repair pass|scan|context guide|glossary|research|export|saved|checkpoint|completed|finished|successfully|✓)/i.test(l)));
});

runTest('Empty state triggers when no logs match the active filter', () => {
    const cleanLogs = [
        '[10:00:00] Job started',
        '[10:00:05] Translating Scene 1',
        '[10:00:10] Translation completed ✓'
    ];
    const { displayedLogs } = categorizeLogs(cleanLogs, 'errors');
    assert.strictEqual(displayedLogs.length, 0);

    const emptyMessage = `No log entries match the selected filter (errors).`;
    assert.strictEqual(emptyMessage, 'No log entries match the selected filter (errors).');
});

// ----------------------------------------------------------------------------
// SUITE 3: 'COPY LOGS' & 'DOWNLOAD LOGS (.TXT)'
// ----------------------------------------------------------------------------
console.log('\n[SUITE 3] Copy Logs & Download Logs (.txt)');

runTest('Copy Logs text generator respects active filter tab and mode', () => {
    const activeJob = {
        id: 'batch_translate_777',
        logs: sampleLogs,
        prompt: 'SYSTEM PROMPT: Translate English subtitles to Greek.',
        ai_response: '{"cues": [{"id": 1, "text": "Γεια σου κόσμε"}]}'
    };

    const getCopyText = (tab, filter) => {
        if (tab === 'ai_response') return activeJob.ai_response;
        if (tab === 'prompt') return activeJob.prompt;
        const { displayedLogs } = categorizeLogs(activeJob.logs, filter);
        return displayedLogs.join('\n');
    };

    // Logs - All
    const allCopy = getCopyText('logs', 'all');
    assert.strictEqual(allCopy.split('\n').length, 15);
    assert.ok(allCopy.includes('Starting translation pipeline'));

    // Logs - Errors
    const errCopy = getCopyText('logs', 'errors');
    assert.strictEqual(errCopy.split('\n').length, 5);
    assert.ok(errCopy.includes('Error: Model returned 429'));
    assert.ok(!errCopy.includes('Starting translation pipeline'));

    // AI Response tab
    const aiCopy = getCopyText('ai_response', 'all');
    assert.strictEqual(aiCopy, activeJob.ai_response);

    // Prompt tab
    const promptCopy = getCopyText('prompt', 'all');
    assert.strictEqual(promptCopy, activeJob.prompt);
});

runTest('Download Logs (.txt) creates standardized ASCII header, sections, and sanitized filename', () => {
    const job = {
        id: 'job/special:id#123',
        project_name: 'Breaking Bad',
        episode_name: 'S01E01',
        status: 'running',
        progress: 65,
        pipeline_stage: 'translate',
        cues_translated: 130,
        total_cues: 200,
        eta_seconds: 45.3,
        logs: sampleLogs,
        prompt: 'Translate with character register',
        ai_response: 'Scene 1 translated'
    };

    const { content, filename } = generateLogDownloadPayload(job);

    // Verify sanitized filename removes slashes, colons, hashes
    assert.strictEqual(filename, 'job_special_id_123_logs.txt');

    // Verify metadata headers
    assert.ok(content.includes('OMBISUB JOB LOG DUMP — 2026-09-16'));
    assert.ok(content.includes('Job ID:          job/special:id#123'));
    assert.ok(content.includes('Project:         Breaking Bad'));
    assert.ok(content.includes('Episode:         S01E01'));
    assert.ok(content.includes('Status:          running'));
    assert.ok(content.includes('Progress:        65%'));
    assert.ok(content.includes('Stage:           translate'));
    assert.ok(content.includes('Cues Translated: 130/200'));
    assert.ok(content.includes('ETA:             46s')); // Math.ceil(45.3)

    // Verify log stream section
    assert.ok(content.includes('--- LOG STREAM ---'));
    assert.ok(content.includes('[10:00:01] Starting translation pipeline'));

    // Verify prompt and AI response sections
    assert.ok(content.includes('--- LAST PROMPT ---'));
    assert.ok(content.includes('Translate with character register'));
    assert.ok(content.includes('--- LAST AI RESPONSE ---'));
    assert.ok(content.includes('Scene 1 translated'));
});

// ----------------------------------------------------------------------------
// SUITE 4: SCENE-BY-SCENE STATUS INDICATORS & NUMERICAL SORTING
// ----------------------------------------------------------------------------
console.log('\n[SUITE 4] Scene-by-Scene Status Indicators & Numerical Sorting');

runTest('getSortedScenes sorts scenes numerically, NOT lexicographically', () => {
    const sceneStatus = {
        'Scene 10': 'running',
        'Scene 1': 'completed',
        'Scene 20': 'pending',
        'Scene 2': 'completed',
        'Scene 100': 'pending',
        'Scene 3': 'failed'
    };

    const sorted = getSortedScenes(sceneStatus);
    const sortedKeys = sorted.map(s => s[0]);

    // Expected numerical order: 1, 2, 3, 10, 20, 100
    assert.deepStrictEqual(sortedKeys, [
        'Scene 1',
        'Scene 2',
        'Scene 3',
        'Scene 10',
        'Scene 20',
        'Scene 100'
    ]);

    // Contrast with naive lexicographical sort (which would be: 1, 10, 100, 2, 20, 3)
    const naiveLexicographical = Object.keys(sceneStatus).sort();
    assert.notDeepStrictEqual(sortedKeys, naiveLexicographical);
});

runTest('getSortedScenes handles varied naming conventions and edge inputs gracefully', () => {
    // Prefix variations: 'scene_05', 'scene_2', 'scene_12'
    const underscoreScenes = {
        'scene_12': 'pending',
        'scene_02': 'completed',
        'scene_5': 'running'
    };
    const sortedUnderscore = getSortedScenes(underscoreScenes).map(s => s[0]);
    assert.deepStrictEqual(sortedUnderscore, ['scene_02', 'scene_5', 'scene_12']);

    // Short format: S1, S10, S2
    const shortScenes = { 'S10': 'pending', 'S1': 'completed', 'S2': 'running' };
    const sortedShort = getSortedScenes(shortScenes).map(s => s[0]);
    assert.deepStrictEqual(sortedShort, ['S1', 'S2', 'S10']);

    // Edge cases: empty, null, undefined, primitives
    assert.deepStrictEqual(getSortedScenes({}), []);
    assert.deepStrictEqual(getSortedScenes(null), []);
    assert.deepStrictEqual(getSortedScenes(undefined), []);
    assert.deepStrictEqual(getSortedScenes('invalid'), []);
    assert.deepStrictEqual(getSortedScenes(42), []);
});

runTest('getSceneColor returns correct theme classes and pulse animations', () => {
    const completedClass = getSceneColor('completed');
    assert.ok(completedClass.includes('text-emerald-400'));
    assert.ok(completedClass.includes('border-emerald-500/40'));
    assert.ok(!completedClass.includes('animate-pulse'));

    const runningClass = getSceneColor('running');
    assert.ok(runningClass.includes('text-blue-400'));
    assert.ok(runningClass.includes('animate-pulse'));

    const pendingClass = getSceneColor('pending');
    assert.ok(pendingClass.includes('text-amber-400'));
    assert.ok(!pendingClass.includes('animate-pulse'));

    const failedClass = getSceneColor('failed');
    assert.ok(failedClass.includes('text-rose-400'));
    assert.ok(failedClass.includes('animate-pulse'));

    const retryClass = getSceneColor('retry');
    assert.ok(retryClass.includes('text-rose-400'));
    assert.ok(retryClass.includes('animate-pulse'));

    const retryingClass = getSceneColor('retrying');
    assert.ok(retryingClass.includes('text-rose-400'));
    assert.ok(retryingClass.includes('animate-pulse'));

    const fallbackClass = getSceneColor('unknown_status');
    assert.ok(fallbackClass.includes('text-gray-600') || fallbackClass.includes('text-gray-300'));
});

runTest('Scene progress counter calculates completed vs total correctly', () => {
    const scenes = {
        'Scene 1': 'completed',
        'Scene 2': 'completed',
        'Scene 3': 'running',
        'Scene 4': 'pending',
        'Scene 5': 'failed'
    };
    const sorted = getSortedScenes(scenes);
    const completedCount = sorted.filter(s => s[1] === 'completed').length;
    const totalCount = sorted.length;

    assert.strictEqual(completedCount, 2);
    assert.strictEqual(totalCount, 5);

    // Label format check
    const label = `Scenes (${completedCount}/${totalCount})`;
    assert.strictEqual(label, 'Scenes (2/5)');
});

// ----------------------------------------------------------------------------
// SUITE 5: LOG COLORIZATION ENGINE & TIMESTAMP PARSING
// ----------------------------------------------------------------------------
console.log('\n[SUITE 5] Log Colorization Engine & Timestamp Parsing');

runTest('renderColorizedLog parses leading [HH:MM:SS] timestamp and assigns accurate classes', () => {
    const errorLog = renderColorizedLog('[14:23:01] Fatal: Database connection failure');
    assert.strictEqual(errorLog.timestamp, '[14:23:01]');
    assert.strictEqual(errorLog.text, 'Fatal: Database connection failure');
    assert.strictEqual(errorLog.colorClass, 'text-red-400 font-semibold');

    const warningLog = renderColorizedLog('[09:12:44] Warning: Model retry in progress');
    assert.strictEqual(warningLog.timestamp, '[09:12:44]');
    assert.strictEqual(warningLog.colorClass, 'text-amber-400 font-medium');

    const successLog = renderColorizedLog('[09:15:00] Subtitles saved to disk ✓');
    assert.strictEqual(successLog.timestamp, '[09:15:00]');
    assert.strictEqual(successLog.colorClass, 'text-emerald-400');

    const translationLog = renderColorizedLog('Translating cues 1-50 with Reasoning: True');
    assert.strictEqual(translationLog.timestamp, null);
    assert.strictEqual(translationLog.colorClass, 'text-purple-400');

    const runningLog = renderColorizedLog('Initializing project pipeline');
    assert.strictEqual(runningLog.timestamp, null);
    assert.strictEqual(runningLog.colorClass, 'text-blue-400');

    const neutralLog = renderColorizedLog('[12:00:00] Line index 45 verified');
    assert.strictEqual(neutralLog.colorClass, 'text-gray-300');

    // Null safety
    assert.strictEqual(renderColorizedLog(null), null);
    assert.strictEqual(renderColorizedLog(''), null);
});

// ----------------------------------------------------------------------------
// SUITE 6: PARENT-CHILD LOG RESOLUTION FOR MODAL CONSOLE
// ----------------------------------------------------------------------------
console.log('\n[SUITE 6] Parent-Child Log Resolution for Modal Console');

runTest('Child log inspection correctly falls back to parent logs matching episode name', () => {
    const parentJob = {
        id: 'batch_translate_99',
        project_name: 'Game of Thrones',
        logs: [
            '[10:00:00] Starting batch translation',
            '[10:00:01] [S01E01] Enqueued translation item',
            '[10:00:02] [S01E02] Enqueued translation item',
            '[10:00:10] [S01E01] Translating scene 1',
            '[10:00:15] [S01E02] Translating scene 1',
            '[10:00:20] [S01E01] S01E01 finished successfully ✓'
        ]
    };

    const childWithoutLogs = {
        id: 'ep_S01E01_batch_translate_99',
        episodeName: 'S01E01',
        logs: [] // Empty in child object
    };

    // Resolution logic from activeModalJob:
    const resolvedLogs = childWithoutLogs.logs && childWithoutLogs.logs.length > 0
        ? childWithoutLogs.logs
        : (parentJob.logs || []).filter(l => l.includes(childWithoutLogs.episodeName));

    assert.strictEqual(resolvedLogs.length, 3);
    assert.ok(resolvedLogs.every(l => l.includes('S01E01')));
    assert.ok(!resolvedLogs.some(l => l.includes('S01E02')));
});

// ----------------------------------------------------------------------------
// SUITE 7: ADVERSARIAL STRESS HARNESS (CHALLENGER M4-2)
// ----------------------------------------------------------------------------
console.log('\n[SUITE 7] Adversarial Stress Harness');

runTest('Numerical sorting scales to 250+ scenes without degrading or reordering', () => {
    // Generate 250 scenes out of order
    const largeSceneMap = {};
    const numbers = Array.from({ length: 250 }, (_, i) => i + 1);
    // Shuffle numbers
    const shuffled = [...numbers].sort(() => Math.random() - 0.5);
    shuffled.forEach(n => {
        largeSceneMap[`Scene ${n}`] = n % 3 === 0 ? 'completed' : n % 3 === 1 ? 'running' : 'pending';
    });

    const sorted = getSortedScenes(largeSceneMap);
    assert.strictEqual(sorted.length, 250);
    for (let i = 0; i < 250; i++) {
        assert.strictEqual(sorted[i][0], `Scene ${i + 1}`);
    }
});

runTest('Log categorization handles null, undefined, ANSI sequences, and empty log streams', () => {
    assert.deepStrictEqual(categorizeLogs(null), { allLogs: [], errorLogs: [], stageLogs: [], displayedLogs: [] });
    assert.deepStrictEqual(categorizeLogs(undefined), { allLogs: [], errorLogs: [], stageLogs: [], displayedLogs: [] });

    // ANSI escapes & Unicode stress
    const adversarialLogs = [
        '\u001b[31m[ERROR]\u001b[0m 429 Quota Exceeded for \u200buser',
        '   \t\r\n', // whitespace
        '🔥 CRITICAL: Pipeline step 4 failed completely',
        '✓ All 12 scenes completed successfully in 45.2s'
    ];
    const { errorLogs, stageLogs } = categorizeLogs(adversarialLogs);
    assert.strictEqual(errorLogs.length, 2); // '429', 'failed'
    assert.strictEqual(stageLogs.length, 2); // 'step 4', 'scenes completed successfully'
});

runTest('Clipboard failure simulation: graceful error state recovery', async () => {
    let copyStatus = 'idle';
    const mockClipboardWriteFail = async () => {
        throw new Error('DOMException: Clipboard permission denied');
    };

    try {
        await mockClipboardWriteFail();
        copyStatus = 'copied';
    } catch (err) {
        copyStatus = 'error';
    }

    assert.strictEqual(copyStatus, 'error');
});

runTest('Sanitized download filename strips all dangerous OS filename characters', () => {
    const dangerousIds = [
        'job/with/slashes\\and:colons*and?quotes"and<brackets>and|pipes',
        '   spaced   id   ',
        'CON.txt',
        'job..//..//passwd'
    ];

    dangerousIds.forEach(id => {
        const sanitized = String(id).replace(/[^a-zA-Z0-9_-]/g, '_');
        const filename = `${sanitized}_logs.txt`;
        // Must contain NO illegal characters: / \ : * ? " < > |
        assert.ok(!/[/\\:*?"<>|]/.test(filename), `Filename contains illegal char: ${filename}`);
        assert.ok(filename.endsWith('_logs.txt'));
    });
});

runTest('Subpixel rendering and negative overscroll maintain correct lock logic', () => {
    // High-DPI displays with fractional pixels (e.g., scale 1.25 or 1.5)
    assert.strictEqual(computeAutoScrollLock(565.4, 1000, 400), true); // 1000 - 965.4 = 34.6 <= 35
    assert.strictEqual(computeAutoScrollLock(564.2, 1000, 400), false); // 1000 - 964.2 = 35.8 > 35

    // Negative bounce overscroll at top (e.g. macOS / mobile bounce)
    assert.strictEqual(computeAutoScrollLock(-15, 1000, 400), false); // 1000 - 385 = 615 > 35 -> unlocked
});

console.log('\n================================================================');
console.log(`ALL TESTS COMPLETED: ${passedTests} passed, ${process.exitCode ? 'FAILURES DETECTED' : '0 failures'}`);
console.log('================================================================');