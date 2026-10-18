// Shared names, colours and helpers for the Autopilot views (docs/PLAN_autopilot.md 4.2 / 4.10).

export const STATE_ORDER = [
    'waiting', 'checking', 'extracting', 'translating',
    'done', 'has_greek', 'skipped', 'no_source', 'failed', 'superseded', 'gone',
];

export const ACTIVE_STATES = ['waiting', 'checking', 'extracting', 'translating'];

export const STATE_LABEL = {
    waiting: 'Waiting for Bazarr',
    checking: 'Checking',
    extracting: 'Extracting',
    translating: 'Translating',
    done: 'Done',
    has_greek: 'Already has Greek',
    skipped: 'Skipped',
    no_source: 'No usable source',
    failed: 'Failed',
    superseded: 'Replaced by a newer file',
    gone: 'File deleted',
};

// Literal class names (Tailwind scans the source), so no dynamic construction here.
export const STATE_STYLE = {
    waiting: 'bg-amber-500/10 border-amber-500/30 text-amber-600 dark:text-amber-400',
    checking: 'bg-blue-500/10 border-blue-500/30 text-blue-600 dark:text-blue-400',
    extracting: 'bg-indigo-500/10 border-indigo-500/30 text-indigo-600 dark:text-indigo-400',
    translating: 'bg-purple-500/10 border-purple-500/30 text-purple-600 dark:text-purple-400',
    done: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400',
    has_greek: 'bg-teal-500/10 border-teal-500/30 text-teal-600 dark:text-teal-400',
    skipped: 'bg-gray-500/10 border-gray-500/30 text-gray-600 dark:text-gray-400',
    no_source: 'bg-orange-500/10 border-orange-500/30 text-orange-600 dark:text-orange-400',
    failed: 'bg-rose-500/10 border-rose-500/30 text-rose-600 dark:text-rose-400',
    superseded: 'bg-gray-500/10 border-gray-500/30 text-gray-500 dark:text-gray-400',
    gone: 'bg-gray-500/10 border-gray-500/30 text-gray-500 dark:text-gray-400',
};

export const SOURCE_KIND_LABEL = {
    embedded_ass: 'Embedded ASS',
    embedded_srt: 'Embedded SRT',
    external_ass: 'External ASS',
    external_srt: 'External SRT',
};

export const REASON_LABEL = {
    arr_disabled: 'project disabled',
    original_greek: 'original language is Greek',
    skip_tag: 'skip tag',
    unmonitored: 'not monitored',
    manual: 'skipped manually',
    media_missing: 'video file not found',
    image_only: 'only image-based subtitles',
    no_subtitles: 'no subtitles in the file',
    ffmpeg_missing: 'ffmpeg is missing',
    appeared_during_translation: 'a Greek file appeared meanwhile',
};

export const reasonText = (reason) => (reason ? (REASON_LABEL[reason] || String(reason).replace(/_/g, ' ')) : '');

export const canCheckNow = (s) => s === 'waiting';
export const canRetry = (s) => ['failed', 'no_source', 'skipped', 'has_greek'].includes(s);
export const canSkip = (s) => ACTIVE_STATES.includes(s);

// An older backend has no /api/autopilot/* routes.
export const isMissingEndpoint = (err) => err?.response?.status === 404;

export const errorText = (err) =>
    err?.response?.data?.detail || err?.response?.data?.message || err?.message || 'Request failed';

export const parseTime = (iso) => {
    if (!iso) return null;
    // The backend writes UTC ISO strings; treat zone-less ones as UTC.
    const t = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
    return Number.isNaN(t) ? null : t;
};

export const fmtDateTime = (iso) => {
    const t = parseTime(iso);
    return t == null ? '' : new Date(t).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};

export const fmtAgo = (iso, now = Date.now()) => {
    const t = parseTime(iso);
    if (t == null) return '';
    const s = Math.max(0, Math.round((now - t) / 1000));
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return `${Math.floor(s / 86400)} d ago`;
};

// "3:12" / "1:02:05"; null when the time is unknown, "now" once due.
export const fmtCountdown = (iso, now = Date.now()) => {
    const t = parseTime(iso);
    if (t == null) return null;
    const s = Math.ceil((t - now) / 1000);
    if (s <= 0) return 'now';
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = String(s % 60).padStart(2, '0');
    return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
};

export const itemTitle = (it) => {
    const base = it.title || it.project_name || it.episode_name || it.media_path || it.id;
    if (it.kind === 'episode' && it.season != null && it.episode != null) {
        return `${base} S${String(it.season).padStart(2, '0')}E${String(it.episode).padStart(2, '0')}`;
    }
    return base;
};

export const baseName = (p) => String(p || '').split(/[\\/]/).pop();

export const lastEvent = (it) => {
    const ev = asList(it.events).slice(-1)[0] || null;
    return ev?.message || it.detail || reasonText(it.reason) || '';
};

// `outputs` / `events` are JSON lists; tolerate a JSON string or null.
export const asList = (v) => {
    if (Array.isArray(v)) return v;
    if (typeof v === 'string' && v) {
        try { const p = JSON.parse(v); return Array.isArray(p) ? p : []; } catch { return []; }
    }
    return [];
};
