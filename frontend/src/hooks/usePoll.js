import { useCallback, useEffect, useRef } from 'react';

// The one poller every periodic request in the UI goes through
// (docs/REVIEW_activity_runner_polling_2026-10-26.md, Step 3).
//
//   usePoll(fn, { intervalMs, hiddenIntervalMs, enabled, refreshOn })
//
// - runs `fn({ signal })` at once and then every `intervalMs` (`hiddenIntervalMs` while the tab is hidden,
//   and at once when it becomes visible again);
// - never overlaps itself: a tick that lands while a call is in flight is skipped, a refresh asked for
//   while one is in flight runs once after it (coalesced);
// - backs off x2 per consecutive error, up to 60 s;
// - refreshes (throttled to one per second, trailing call kept) on every `omnisub:event` whose
//   `detail.type` is in `refreshOn`;
// - aborts the in-flight request on unmount so a late answer is ignored.
//
// Returns { refresh }: refresh() resolves once a fresh call has completed.

const MAX_BACKOFF_MS = 60000;
const EVENT_THROTTLE_MS = 1000;

export function usePoll(fn, {
    intervalMs = 5000,
    hiddenIntervalMs = intervalMs * 3,
    enabled = true,
    refreshOn = [],
} = {}) {
    const fnRef = useRef(fn);
    fnRef.current = fn;
    const refreshOnKey = refreshOn.join('|');

    // Everything the timer loop needs, outside React state so no re-render is caused by bookkeeping.
    const st = useRef({
        alive: false,
        inFlight: null,      // promise of the call in flight, or null
        again: false,        // a refresh was asked for while a call was in flight
        errors: 0,
        timer: null,
        controller: null,
        lastEvent: 0,
        eventTimer: null,
        intervalMs,
        hiddenIntervalMs,
    }).current;
    st.intervalMs = intervalMs;
    st.hiddenIntervalMs = hiddenIntervalMs;

    const runOnce = useCallback(async () => {
        if (!st.alive) return;
        if (st.inFlight) {
            // Coalesce: one more call after the current one, shared by everyone who asked meanwhile.
            st.again = true;
            if (!st.againWait) st.againWait = new Promise(resolve => { st.againResolve = resolve; });
            return st.againWait;
        }
        if (st.timer) {
            clearTimeout(st.timer);
            st.timer = null;
        }
        const controller = typeof AbortController === 'function' ? new AbortController() : null;
        st.controller = controller;
        let ok = true;
        const call = (async () => {
            try {
                await fnRef.current({ signal: controller?.signal });
                st.errors = 0;
            } catch {
                if (!controller?.signal?.aborted) {
                    ok = false;
                    st.errors += 1;
                }
            }
        })();
        st.inFlight = call;
        await call;
        st.inFlight = null;
        st.controller = null;
        if (!st.alive) return;
        if (st.again) {
            st.again = false;
            const resolve = st.againResolve;
            st.againWait = null;
            st.againResolve = null;
            st.timer = setTimeout(async () => {
                await runOnce();
                resolve?.();
            }, 0);
            return;
        }
        const base = (typeof document !== 'undefined' && document.hidden) ? st.hiddenIntervalMs : st.intervalMs;
        const delay = ok ? base : Math.min(base * (2 ** st.errors), MAX_BACKOFF_MS);
        st.timer = setTimeout(runOnce, delay);
    }, [st]);

    const refresh = useCallback(() => runOnce(), [runOnce]);

    useEffect(() => {
        if (!enabled) return undefined;
        st.alive = true;
        st.errors = 0;
        st.again = false;

        const onVisible = () => {
            if (typeof document !== 'undefined' && !document.hidden) runOnce();
        };
        const onEvent = (e) => {
            const type = e?.detail?.type;
            if (!type || !refreshOnKey.split('|').includes(type)) return;
            const now = Date.now();
            const wait = EVENT_THROTTLE_MS - (now - st.lastEvent);
            if (wait <= 0) {
                st.lastEvent = now;
                runOnce();
            } else if (!st.eventTimer) {
                st.eventTimer = setTimeout(() => {
                    st.eventTimer = null;
                    st.lastEvent = Date.now();
                    runOnce();
                }, wait);
            }
        };

        document.addEventListener('visibilitychange', onVisible);
        if (refreshOnKey) window.addEventListener('omnisub:event', onEvent);
        runOnce();

        return () => {
            st.alive = false;
            document.removeEventListener('visibilitychange', onVisible);
            if (refreshOnKey) window.removeEventListener('omnisub:event', onEvent);
            if (st.timer) clearTimeout(st.timer);
            if (st.eventTimer) clearTimeout(st.eventTimer);
            st.timer = null;
            st.eventTimer = null;
            if (st.controller) st.controller.abort();
        };
    }, [enabled, refreshOnKey, runOnce, st]);

    return { refresh };
}

export default usePoll;
