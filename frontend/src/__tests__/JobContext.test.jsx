import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../api', () => ({
    api: {
        getActivity: vi.fn(),
        getJob: vi.fn(),
        cancelJob: vi.fn(() => Promise.resolve({ data: {} })),
        getEvents: vi.fn(() => Promise.resolve({ data: { events: [], latest_id: 0 } })),
    },
}));

import { api } from '../api';
import { JobProvider, useJobs, STARTED_KEY } from '../context/JobContext';

const emptySnapshot = (over = {}) => ({
    ts: 1,
    jobs: [],
    queue: [],
    runner: { status: 'idle', running: false, paused: false, message: '', lanes: [], config: {} },
    worker: { paused: false, pending: 0, running: 0 },
    summary: { pending: 0, running: 0, completed: 0, failed: 0 },
    translation_engine: 'agent',
    ...over,
});

const job = (over = {}) => ({
    id: 'j1', type: 'create_context', project: 'Show', episode: null, status: 'running', progress: 40,
    message: 'working', stage: null, eta_seconds: null, logs: ['[12:00:00] hi'], started_at: 1, parent_id: null,
    link: '/project/Show', ...over,
});

const wrapper = ({ children }) => <JobProvider>{children}</JobProvider>;

// Each getActivity call answers with the next snapshot in the list (the last one repeats).
const serveSnapshots = (list) => {
    let i = 0;
    api.getActivity.mockImplementation(() => {
        const snap = list[Math.min(i, list.length - 1)];
        i += 1;
        return Promise.resolve({ data: snap });
    });
};

const tick = async (ms) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };

describe('JobContext', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.stubGlobal('WebSocket', undefined); // no socket in jsdom; the events poll is mocked
        localStorage.clear();
        api.getJob.mockReset();
        api.getActivity.mockReset();
    });
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.useRealTimers();
    });

    it('drops a card once the snapshot stops listing it, after the grace period', async () => {
        serveSnapshots([
            emptySnapshot({ jobs: [job()], summary: { completed: 0 } }),
            emptySnapshot({ jobs: [], summary: { completed: 1 } }),
        ]);
        const { result } = renderHook(() => useJobs(), { wrapper });
        await tick(0);
        expect(result.current.activeJobs.j1).toMatchObject({ status: 'running', project_name: 'Show', metadata: { link: '/project/Show' } });

        await tick(5000); // next poll: gone from the snapshot -> grace with its final state
        expect(result.current.activeJobs.j1).toMatchObject({ status: 'completed', message: 'Finished' });
        expect(result.current.activeJobs.j1.finished_at).toBeTypeOf('number');

        await tick(10200);
        expect(result.current.activeJobs.j1).toBeUndefined();
    });

    it('shows a queue row as a bg_ card and marks an orphan', async () => {
        serveSnapshots([emptySnapshot({
            queue: [
                { id: 'q1', project: 'Show', episode: 'S01E01', kind: 'translation', status: 'running', engine: 'agent', progress: 12.5, units_done: 40, units_total: 320, lane: 1, message: '', link: '/project/Show/episode/S01E01' },
                { id: 'q2', project: 'Show', episode: 'S01E02', kind: 'translation', status: 'orphaned', engine: 'model', progress: 0, units_done: null, units_total: null, lane: null, message: '', link: null },
            ],
        })]);
        const { result } = renderHook(() => useJobs(), { wrapper });
        await tick(0);
        expect(result.current.activeJobs.bg_q1).toMatchObject({
            type: 'translate_episode', status: 'running', progress: 12.5, episode_name: 'S01E01',
            metadata: { source: 'agent', units_done: 40, units_total: 320, lane: 1 },
        });
        expect(result.current.activeJobs.bg_q1.message).toBe('40/320 lines');
        expect(result.current.activeJobs.bg_q2).toMatchObject({ status: 'orphaned', metadata: { source: 'queue_worker', link: '/project/Show/episode/S01E02' } });
    });

    it('fetches a started-here job once when the snapshot never lists it, and keeps its final status', async () => {
        serveSnapshots([emptySnapshot()]);
        api.getJob.mockResolvedValue({ data: { status: 'completed', progress: 100, message: 'Guide written', logs: ['done'], result: { ok: true } } });
        const { result } = renderHook(() => useJobs(), { wrapper });
        await tick(0);

        act(() => { result.current.addJob('j9', 'create_context', 'Generating Context Guide for Show', { projectId: 'Show', link: '/project/Show' }); });
        await tick(0);
        expect(result.current.activeJobs.j9).toMatchObject({ status: 'pending', message: 'Starting…', type: 'create_context' });
        expect(JSON.parse(localStorage.getItem(STARTED_KEY)).j9).toMatchObject({ type: 'create_context' });
        expect(api.getJob).not.toHaveBeenCalled();

        await tick(31000);
        expect(api.getJob).toHaveBeenCalledTimes(1);
        expect(api.getJob).toHaveBeenCalledWith('j9');
        expect(result.current.activeJobs.j9).toMatchObject({ status: 'completed', message: 'Guide written', result: { ok: true } });
        expect(result.current.activeJobs.j9.finished_at).toBeTypeOf('number');

        await tick(60000);
        expect(api.getJob).toHaveBeenCalledTimes(1);
        expect(result.current.activeJobs.j9.status).toBe('completed');
    });

    it('fetches the final state when a started-here job leaves the snapshot; a 404 is a cancellation', async () => {
        serveSnapshots([
            emptySnapshot(),
            emptySnapshot({ jobs: [job({ id: 'j2', status: 'running', progress: 10 })] }),
            emptySnapshot(),
        ]);
        api.getJob.mockRejectedValue({ response: { status: 404 } });
        const { result } = renderHook(() => useJobs(), { wrapper });
        await tick(0);
        act(() => { result.current.addJob('j2', 'create_context', 'Guide', { projectId: 'Show' }); });
        await tick(0); // addJob refreshes: the second snapshot lists it
        expect(result.current.activeJobs.j2.status).toBe('running');
        await tick(5000); // third snapshot: gone
        expect(api.getJob).toHaveBeenCalledTimes(1);
        expect(result.current.activeJobs.j2).toMatchObject({ status: 'cancelled', message: 'No longer on the server' });
    });

    it('isLive needs both project and episode to match (project alone when no episode is given)', async () => {
        serveSnapshots([emptySnapshot({
            jobs: [job({ id: 'j3', project: 'Show', episode: 'S01E01' })],
            queue: [{ id: 'q3', project: 'Other', episode: 'S02E02', kind: 'translation', status: 'running', engine: 'model', progress: 0 }],
        })]);
        const { result } = renderHook(() => useJobs(), { wrapper });
        await tick(0);
        expect(result.current.isLive('Show', 'S01E01')).toBe(true);
        expect(result.current.isLive('show', 's01e01')).toBe(true);
        expect(result.current.isLive('Show', 'S01E02')).toBe(false);
        expect(result.current.isLive('Show')).toBe(true);
        expect(result.current.isLive('Other', 'S02E02')).toBe(true);
        expect(result.current.isLive('Other', 'S01E01')).toBe(false);
        expect(result.current.isLive('Nope')).toBe(false);
    });

    it('clearAll empties the panel without touching the server', async () => {
        serveSnapshots([emptySnapshot({ jobs: [job()] })]);
        const { result } = renderHook(() => useJobs(), { wrapper });
        await tick(0);
        act(() => { result.current.addJob('j5', 'arr_sync', 'Syncing', { link: '/' }); });
        await tick(0);
        expect(Object.keys(result.current.activeJobs).sort()).toEqual(['j1', 'j5']);

        act(() => { result.current.clearAll(); });
        await tick(0);
        expect(result.current.activeJobs).toEqual({});
        expect(JSON.parse(localStorage.getItem(STARTED_KEY))).toEqual({});
        expect(api.cancelJob).not.toHaveBeenCalled();
        // still live on the server: isLive keeps answering from the snapshot
        expect(result.current.isLive('Show')).toBe(true);
    });

    it('expires started-here ids from localStorage after 10 minutes on load', async () => {
        const old = Date.now() - 11 * 60 * 1000;
        localStorage.setItem(STARTED_KEY, JSON.stringify({
            stale: { id: 'stale', type: 'x', description: 'x', metadata: {}, started_at: old },
            fresh: { id: 'fresh', type: 'x', description: 'x', metadata: {}, started_at: Date.now() },
        }));
        serveSnapshots([emptySnapshot()]);
        const { result } = renderHook(() => useJobs(), { wrapper });
        await tick(0);
        expect(result.current.activeJobs.stale).toBeUndefined();
        expect(result.current.activeJobs.fresh).toMatchObject({ status: 'pending' });
    });
});
