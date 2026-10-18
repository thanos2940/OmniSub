/**
 * Utility formatters for subtitle translation telemetry and time intervals.
 * Location: frontend/src/utils/formatters.js
 */

/**
 * Formats a duration in seconds into a clean human-readable ETA string.
 *
 * @param {number|string|null|undefined} seconds - The duration in seconds.
 * @param {Object} [options] - Formatting options.
 * @param {boolean} [options.approximate=false] - Whether to prefix with '~'.
 * @param {string} [options.fallback='—'] - Fallback string when seconds is null, undefined, or NaN.
 * @returns {string} Formatted duration (e.g. '< 5s', '45s', '2m 15s', '1h 05m', '1d 2h').
 */
export function formatEta(seconds, options = {}) {
    const { approximate = false, fallback = '—' } = options;

    if (seconds == null || seconds === '' || Number.isNaN(Number(seconds))) {
        return fallback;
    }

    const sec = Number(seconds);

    // Guard infinite or negative values
    if (!Number.isFinite(sec)) {
        return fallback;
    }

    if (sec <= 0) {
        return '< 5s';
    }

    // Days (>= 86400 seconds)
    if (sec >= 86400) {
        const days = Math.floor(sec / 86400);
        const hours = Math.floor((sec % 86400) / 3600);
        const prefix = approximate ? '~' : '';
        return hours > 0 ? `${prefix}${days}d ${hours}h` : `${prefix}${days}d`;
    }

    // Hours (>= 3600 seconds)
    if (sec >= 3600) {
        const hours = Math.floor(sec / 3600);
        const mins = Math.floor((sec % 3600) / 60);
        const prefix = approximate ? '~' : '';
        return `${prefix}${hours}h ${mins.toString().padStart(2, '0')}m`;
    }

    // Minutes (>= 60 seconds)
    if (sec >= 60) {
        const mins = Math.floor(sec / 60);
        const remSec = Math.floor(sec % 60);
        const prefix = approximate ? '~' : '';
        return `${prefix}${mins}m ${remSec.toString().padStart(2, '0')}s`;
    }

    // Sub-minute (< 60 seconds)
    const roundedSec = Math.round(sec);
    if (roundedSec < 5) {
        return '< 5s';
    }
    const prefix = approximate ? '~' : '';
    return `${prefix}${roundedSec}s`;
}

/**
 * Formats scene progress telemetry into a string (e.g. 'Scene 3 / 8').
 *
 * @param {number|null|undefined} done - Scenes completed.
 * @param {number|null|undefined} total - Total scenes.
 * @returns {string} Formatted scene progress or empty string.
 */
export function formatSceneProgress(done, total) {
    if (!total || total <= 0) return '';
    const completed = Math.max(0, done || 0);
    return `Scene ${completed} / ${total}`;
}

/**
 * Formats cue progress telemetry into a string (e.g. 'Cues: 142 / 350').
 *
 * @param {number|null|undefined} done - Cues completed.
 * @param {number|null|undefined} total - Total cues.
 * @returns {string} Formatted cue progress or empty string.
 */
export function formatCueProgress(done, total) {
    const completed = Math.max(0, done || 0);
    if (total && total > 0) {
        return `Cues: ${completed} / ${total}`;
    }
    if (completed > 0) {
        return `${completed} cues`;
    }
    return '';
}

/**
 * Formats an ISO timestamp into local time and date.
 *
 * @param {string|null|undefined} isoString - ISO date string.
 * @returns {string} Formatted date time string.
 */
export function formatTimestamp(isoString) {
    if (!isoString) return '—';
    try {
        const date = new Date(isoString);
        if (Number.isNaN(date.getTime())) return '—';
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' ' + date.toLocaleDateString();
    } catch {
        return '—';
    }
}
