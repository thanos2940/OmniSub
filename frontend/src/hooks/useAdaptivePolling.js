import { useEffect, useRef } from 'react';

/**
 * Custom hook for adaptive polling with exponential backoff and tab visibility throttling.
 *
 * @param {Object} options
 * @param {boolean} options.enabled - Whether polling is active
 * @param {Function} options.pollFn - Async function returning a progress signature or result string.
 * @param {number} [options.baseInterval=1500] - Base polling interval in ms
 * @param {number} [options.maxInterval=5000] - Max backoff interval in ms
 * @param {number} [options.backgroundInterval=10000] - Interval when tab is hidden in ms
 * @param {number} [options.backoffThreshold=3] - Number of consecutive unchanged ticks before backoff starts
 */
export const useAdaptivePolling = ({
    enabled,
    pollFn,
    baseInterval = 1500,
    maxInterval = 5000,
    backgroundInterval = 10000,
    backoffThreshold = 3,
}) => {
    const isPollingRef = useRef(false);
    const pollTimerRef = useRef(null);
    const unchangedCountRef = useRef(0);
    const lastProgressSigRef = useRef('');
    const pollFnRef = useRef(pollFn);

    useEffect(() => {
        pollFnRef.current = pollFn;
    }, [pollFn]);

    useEffect(() => {
        if (!enabled) {
            if (pollTimerRef.current) {
                clearTimeout(pollTimerRef.current);
                pollTimerRef.current = null;
            }
            return;
        }

        let isCancelled = false;

        const scheduleNextPoll = (delay) => {
            if (isCancelled) return;
            if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
            pollTimerRef.current = setTimeout(executePoll, delay);
        };

        const executePoll = async () => {
            if (isCancelled || isPollingRef.current) return;
            isPollingRef.current = true;

            let nextDelay = baseInterval;

            try {
                if (document.hidden) {
                    nextDelay = backgroundInterval;
                }

                if (pollFnRef.current) {
                    const currentProgressSig = await pollFnRef.current();

                    if (!isCancelled && typeof currentProgressSig === 'string') {
                        if (currentProgressSig === lastProgressSigRef.current) {
                            unchangedCountRef.current += 1;
                            if (unchangedCountRef.current > backoffThreshold && !document.hidden) {
                                const backoffFactor = Math.min(unchangedCountRef.current - backoffThreshold, 4);
                                nextDelay = Math.min(baseInterval + backoffFactor * 1000, maxInterval);
                            }
                        } else {
                            unchangedCountRef.current = 0;
                            lastProgressSigRef.current = currentProgressSig;
                            if (!document.hidden) {
                                nextDelay = baseInterval;
                            }
                        }
                    }
                }
            } catch (err) {
                console.error("Adaptive polling error:", err);
            } finally {
                isPollingRef.current = false;
                if (!isCancelled) {
                    scheduleNextPoll(nextDelay);
                }
            }
        };

        executePoll();

        const handleVisibilityChange = () => {
            if (!document.hidden) {
                unchangedCountRef.current = 0;
                if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
                executePoll();
            }
        };

        document.addEventListener('visibilitychange', handleVisibilityChange);

        return () => {
            isCancelled = true;
            document.removeEventListener('visibilitychange', handleVisibilityChange);
            if (pollTimerRef.current) {
                clearTimeout(pollTimerRef.current);
                pollTimerRef.current = null;
            }
        };
    }, [enabled, baseInterval, maxInterval, backgroundInterval, backoffThreshold]);
};

export default useAdaptivePolling;
