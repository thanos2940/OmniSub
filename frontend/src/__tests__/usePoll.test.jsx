import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { usePoll } from '../hooks/usePoll';

const fire = (type) => window.dispatchEvent(new CustomEvent('omnisub:event', { detail: { type } }));

describe('usePoll', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });
    afterEach(() => {
        vi.useRealTimers();
    });

    it('runs at once, then on the interval, and never overlaps itself', async () => {
        let release = null;
        const fn = vi.fn(() => new Promise(resolve => { release = resolve; }));
        renderHook(() => usePoll(fn, { intervalMs: 1000 }));
        expect(fn).toHaveBeenCalledTimes(1);

        // The first call is still in flight: ticks are skipped, not stacked.
        await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
        expect(fn).toHaveBeenCalledTimes(1);

        await act(async () => { release(); await vi.advanceTimersByTimeAsync(0); });
        await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
        expect(fn).toHaveBeenCalledTimes(2);
    });

    it('refreshes on a listed event, throttled to one call per second', async () => {
        const fn = vi.fn(() => Promise.resolve());
        renderHook(() => usePoll(fn, { intervalMs: 60000, refreshOn: ['queue_updated'] }));
        await act(async () => { await vi.advanceTimersByTimeAsync(0); });
        expect(fn).toHaveBeenCalledTimes(1);

        await act(async () => {
            for (let i = 0; i < 5; i++) fire('queue_updated');
            fire('something_else');
            await vi.advanceTimersByTimeAsync(0);
        });
        expect(fn).toHaveBeenCalledTimes(2); // one immediate call for the burst

        await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
        expect(fn).toHaveBeenCalledTimes(3); // and one trailing call, coalescing the rest

        await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
        expect(fn).toHaveBeenCalledTimes(3); // nothing more until the next interval
    });

    it('backs off after errors and resumes at once when the tab becomes visible', async () => {
        const fn = vi.fn(() => Promise.reject(new Error('down')));
        renderHook(() => usePoll(fn, { intervalMs: 1000 }));
        await act(async () => { await vi.advanceTimersByTimeAsync(0); });
        expect(fn).toHaveBeenCalledTimes(1);
        await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
        expect(fn).toHaveBeenCalledTimes(1); // first retry waits 2 s, not 1
        await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
        expect(fn).toHaveBeenCalledTimes(2);
        await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
        expect(fn).toHaveBeenCalledTimes(2); // second retry waits 4 s

        await act(async () => {
            document.dispatchEvent(new Event('visibilitychange'));
            await vi.advanceTimersByTimeAsync(0);
        });
        expect(fn).toHaveBeenCalledTimes(3);
    });

    it('stops and aborts on unmount', async () => {
        const seen = [];
        const fn = vi.fn(({ signal }) => { seen.push(signal); return Promise.resolve(); });
        const { unmount } = renderHook(() => usePoll(fn, { intervalMs: 1000 }));
        await act(async () => { await vi.advanceTimersByTimeAsync(0); });
        unmount();
        await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
        expect(fn).toHaveBeenCalledTimes(1);
        expect(seen[0]).toBeDefined();
    });
});
