import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// The top-bar indicator and the helpers it shares with the Activity page. The snapshot is the one
// JobContext polls; here it is handed in directly, and the API is mocked (network-free).

const jobsState = { current: null };
vi.mock('../context/JobContext', () => ({ useJobs: () => jobsState.current }));
vi.mock('../context/ToastContext', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }) }));
vi.mock('../api', () => ({
    api: {
        cancelQueueItem: vi.fn(() => Promise.resolve({ data: {} })),
        releaseQueueItem: vi.fn(() => Promise.resolve({ data: {} })),
        getJob: vi.fn(() => Promise.resolve({ data: { logs: ['line one', 'line two'] } })),
        getAgentRunner: vi.fn(),
    },
}));

import { api } from '../api';
import ActivityIndicator from '../components/activity/ActivityIndicator';
import { backgroundJobs, liveQueueRows } from '../components/activity/liveWork';

const snapshot = (over = {}) => ({
    jobs: [],
    queue: [
        { id: 'q1', project: 'Harbour Lights', episode: 'S01E02', kind: 'translation', engine: 'agent', status: 'running',
            message: 'Translating with gemini', units_done: 4, units_total: 8, link: '/project/Harbour%20Lights/episode/S01E02' },
        { id: 'q2', project: 'Harbour Lights', episode: 'S01E03', kind: 'translation', engine: 'agent', status: 'orphaned',
            message: 'Marked running but nothing is working on it', units_done: null, units_total: null, link: null },
    ],
    runner: { status: 'running', running: true, paused: false, message: '', config: { engine: 'antigravity' },
        lanes: [{ lane: 1, task: 't1' }] },
    worker: { paused: false },
    summary: { pending: 3, running: 2, completed_today: 5, failed: 0 },
    translation_engine: 'agent',
    ...over,
});

const card = (over = {}) => ({
    id: 'arr_sync_1', type: 'arr_sync', status: 'running', progress: 30, message: 'Reading Sonarr', logs: ['a'],
    project_name: null, episode_name: null, metadata: { link: '/' }, started_at: 1, ...over,
});

const setJobs = (snap, activeJobs = {}) => {
    jobsState.current = {
        snapshot: snap, activeJobs, cancelJob: vi.fn(() => Promise.resolve()), removeJob: vi.fn(), refresh: vi.fn(),
    };
};

const renderIndicator = () => render(<MemoryRouter><ActivityIndicator /></MemoryRouter>);

describe('liveWork helpers', () => {
    it('turns running queue rows into progress, and marks orphans', () => {
        const rows = liveQueueRows(snapshot(), {});
        expect(rows.map(r => [r.id, r.progress, r.orphaned])).toEqual([['q1', 50, false], ['q2', 0, true]]);
    });

    it('lists background jobs without queue rows, extractions or pipeline children', () => {
        const active = {
            arr_sync_1: card(),
            bg_q1: card({ id: 'bg_q1', type: 'translate_episode' }),
            extract_q9: card({ id: 'extract_q9', type: 'extraction' }),
            pipeline_2: card({ id: 'pipeline_2', type: 'pipeline', sub_jobs: ['child_3'] }),
            child_3: card({ id: 'child_3', type: 'create_context' }),
            create_glossary_4: card({ id: 'create_glossary_4', type: 'create_glossary', status: 'failed', finished_at: 5 }),
        };
        const { live, finished } = backgroundJobs(active);
        expect(live.map(j => j.id).sort()).toEqual(['arr_sync_1', 'pipeline_2']);
        expect(finished.map(j => j.id)).toEqual(['create_glossary_4']);
    });
});

describe('ActivityIndicator', () => {
    beforeEach(() => vi.clearAllMocks());

    it('shows the runner state with the number of background jobs, and the live work on click', async () => {
        setJobs(snapshot(), { arr_sync_1: card() });
        renderIndicator();
        const pill = screen.getByRole('button', { name: /show what is running/i });
        expect(pill).toHaveTextContent('agy · translating 1');
        expect(pill).toHaveTextContent('+1');
        fireEvent.click(pill);
        const panel = screen.getByRole('dialog', { name: 'What is running' });
        expect(panel).toHaveTextContent('S01E02');
        expect(panel).toHaveTextContent('Translating with gemini');
        expect(panel).toHaveTextContent('Nothing is working on it');
        expect(panel).toHaveTextContent('Library sync');
        expect(panel).toHaveTextContent('3 waiting in the queue · 5 done today');
    });

    it('cancels an episode and puts an orphan back from the panel', async () => {
        setJobs(snapshot());
        renderIndicator();
        fireEvent.click(screen.getByRole('button', { name: /show what is running/i }));
        fireEvent.click(screen.getByRole('button', { name: 'Cancel S01E02' }));
        await waitFor(() => expect(api.cancelQueueItem).toHaveBeenCalledWith('q1'));
        fireEvent.click(screen.getByRole('button', { name: 'Put back in the queue' }));
        await waitFor(() => expect(api.releaseQueueItem).toHaveBeenCalledWith('q2'));
    });

    it('closes on Escape and says so when nothing runs', () => {
        setJobs(snapshot({ queue: [], runner: { status: 'idle', running: true, message: '', config: {}, lanes: [] },
            summary: { pending: 0 } }));
        renderIndicator();
        fireEvent.click(screen.getByRole('button', { name: /show what is running/i }));
        expect(screen.getByRole('dialog')).toHaveTextContent('Nothing is running right now.');
        fireEvent.keyDown(document, { key: 'Escape' });
        expect(screen.queryByRole('dialog')).toBeNull();
    });
});
