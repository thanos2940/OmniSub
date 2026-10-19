import { api } from '../../api';

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

// After a restart or an update: wait until the server answers again with a new start time.
// Resolves with the fresh /api/system/status data, or null if it did not come back in time.
export async function waitForServer({ startedBefore, timeoutMs = 120000, cancelled = () => false } = {}) {
    const began = Date.now();
    let sawDown = false;
    await sleep(2500); // the server stops a moment after it answers the restart request
    while (!cancelled() && Date.now() - began < timeoutMs) {
        try {
            await api.getHealth();
            const status = await api.getSystemStatus();
            if (sawDown || status.data?.started_at !== startedBefore) return status.data;
        } catch {
            sawDown = true;
        }
        await sleep(1500);
    }
    return null;
}
