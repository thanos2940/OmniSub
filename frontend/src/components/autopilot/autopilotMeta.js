// Shared names, tones and helpers for the Autopilot views (docs/PLAN_autopilot.md 4.2 / 4.10).

export const STATE_ORDER = [
    'waiting', 'checking', 'extracting', 'translating',
    'done', 'has_greek', 'skipped', 'no_source', 'failed', 'superseded', 'gone',
];

export const ACTIVE_STATES = ['waiting', 'checking', 'extracting', 'translating'];

export const STATE_LABEL = {
    waiting: 'Waiting',
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

// Tone of each state's chip (see StatusChip in components/ui).
export const STATE_TONE = {
    waiting: 'warning',
    checking: 'info',
    extracting: 'info',
    translating: 'accent',
    done: 'success',
    has_greek: 'success',
    skipped: 'neutral',
    no_source: 'warning',
    failed: 'danger',
    superseded: 'neutral',
    gone: 'neutral',
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
    cancelled: 'cancelled in the queue',
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
