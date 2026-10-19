// The library-scan options the user last chose (kept in this browser). "Sync now" uses them as they are;
// "Sync options..." opens the modal to change them.
export const SYNC_OPTIONS_KEY = 'omnisub_sync_options';

export const DEFAULT_SYNC_OPTIONS = {
    scan_ass: true,
    extract_embedded_ass: false,
    source: 'both',
};

export function getSavedSyncOptions() {
    try {
        const saved = localStorage.getItem(SYNC_OPTIONS_KEY);
        if (saved) return { ...DEFAULT_SYNC_OPTIONS, ...JSON.parse(saved) };
    } catch {
        // storage unavailable or corrupt: fall back to the defaults
    }
    return { ...DEFAULT_SYNC_OPTIONS };
}

export function saveSyncOptions(options) {
    try {
        localStorage.setItem(SYNC_OPTIONS_KEY, JSON.stringify(options));
    } catch {
        // not critical
    }
}
