// Pure helpers that turn the episode list payload into what the Episodes table shows.

export const hasSourceSubtitle = (ep) => {
    if (ep.has_source === true) return true;
    if (ep.has_source === false && ep.line_count === 0 && !ep.metadata?.arr_sub_path && !ep.has_ass) return false;
    return (ep.line_count > 0 || !!ep.metadata?.arr_sub_path || !!ep.has_ass || ep.has_source !== false);
};

export const canTranslate = (ep) => hasSourceSubtitle(ep) || !!ep.has_media || !!ep.metadata?.arr_media_path;

const SEASON_RE = /S(\d+)E(\d+)/i;

export function seasonNumber(ep) {
    const fromName = ep.name.match(SEASON_RE) || ep.name.match(/Season\s*(\d+)/i);
    if (fromName) return parseInt(fromName[1], 10);
    const fromMeta = Number(ep.season ?? ep.metadata?.season);
    return Number.isFinite(fromMeta) && fromMeta > 0 ? fromMeta : 0;
}

export const seasonLabel = (n) => (n > 0 ? `Season ${n}` : 'Episodes');

// "S01E03" when the name has it, else the name itself.
export function episodeCode(ep) {
    const m = ep.name.match(SEASON_RE);
    return m ? `S${m[1].padStart(2, '0')}E${m[2].padStart(2, '0')}` : ep.name;
}

// The readable part after the code ("Pilot"), when the sync stored the episode title.
export function episodeTitle(ep) {
    const title = ep.metadata?.episode_title;
    if (title) return title;
    const m = ep.name.match(SEASON_RE);
    if (!m) return '';
    return ep.name.replace(/^.*?S\d+E\d+[\s._-]*/i, '').replace(/[._]+/g, ' ').trim();
}

export function gapInfo(ep) {
    const g = ep.metadata?.translation_gaps || {};
    const missing = (Number(g.dialogue) || 0) + (Number(g.signs) || 0);
    return { missing, misplaced: Number(g.misplaced) || 0, broken: Number(g.broken) || 0 };
}

// {done, total} when the number of translated lines is known, else null.
export function lineProgress(ep) {
    const total = Number(ep.line_count) || 0;
    if (!ep.translated || total <= 0) return null;
    const { missing } = gapInfo(ep);
    return { done: Math.max(0, total - missing), total };
}

export function lastTranslated(ep) {
    const raw = ep.metadata?.last_translated_at;
    if (!raw) return null;
    const d = new Date(raw);
    return Number.isNaN(d.getTime()) ? null : d;
}

export const formatDate = (d) => (d ? d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '');

// Which filter chips an episode belongs to. `queueEntry` is the live queue state or undefined.
export function matchesFilter(ep, filter, queueEntry) {
    switch (filter) {
        case 'translated': return !!ep.translated;
        case 'missing': return !ep.translated && hasSourceSubtitle(ep);
        case 'progress': return !!queueEntry;
        case 'nosub': return !hasSourceSubtitle(ep);
        default: return true;
    }
}
