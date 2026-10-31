// What is live on the server, from the activity snapshot that JobContext polls: the queue rows being worked
// on, the background jobs (library sync, context and glossary builds, summaries, library-wide searches) and
// what finished a moment ago. The top-bar indicator and the Activity page read the same lists.

const LIVE = ['running', 'pending', 'paused', 'awaiting_review'];
const FINISHED = ['completed', 'failed', 'cancelled'];

const JOB_LABEL = {
    arr_sync: 'Library sync',
    sonarr_sync: 'Sonarr sync',
    radarr_sync: 'Radarr sync',
    project_sync: 'Title sync',
    translate_missing: 'Translate missing',
    translate_missing_library: 'Looking for subtitles inside videos',
    batch_extract_embedded: 'Extracting subtitles from videos',
    create_context: 'Building the context guide',
    enhance_context: 'Improving the context guide',
    create_glossary: 'Building the glossary',
    enhance_glossary: 'Improving the glossary',
    generate_summary: 'Episode summaries',
    episode_summary: 'Episode summary',
    generate_profiles: 'Character profiles',
    character_profiles: 'Character profiles',
    retranslate_invalidated: 'Updating scenes',
    script_repair: 'Script repair',
    apply_fixes: 'SubtitleEdit fixes',
    consistency_fix: 'Consistency fixes',
    scan_episode: 'Scanning an episode',
    batch_translate: 'Batch translation',
    translate_episode: 'Translation',
};

// "Building the glossary · Show" for a background job card (JobContext's activeJobs entry).
export function jobTitle(card) {
    const label = JOB_LABEL[card.type] || String(card.type || 'Job').replace(/_/g, ' ');
    const where = [card.project_name, card.episode_name].filter(Boolean).join(' · ');
    return where ? `${label} · ${where}` : label;
}

// Queue rows the snapshot lists as running (translation and extraction), each with the extraction job's
// progress folded in when there is one: { id, project, episode, kind, engine, status, message, progress,
// unitsDone, unitsTotal, link, orphaned }.
export function liveQueueRows(snapshot, activeJobs = {}) {
    return (snapshot?.queue || []).map((row) => {
        const extraction = activeJobs[`extract_${row.id}`];
        const unitsTotal = Number.isFinite(row.units_total) ? row.units_total : null;
        const unitsDone = Number.isFinite(row.units_done) ? row.units_done : null;
        let progress = typeof row.progress === 'number' ? row.progress : 0;
        if (unitsTotal) progress = Math.round((100 * (unitsDone || 0)) / unitsTotal);
        if (extraction && typeof extraction.progress === 'number') progress = extraction.progress;
        return {
            id: row.id,
            project: row.project || null,
            episode: row.episode || null,
            kind: row.kind || 'translation',
            engine: row.engine || 'model',
            status: row.status,
            orphaned: row.status === 'orphaned',
            message: (extraction?.message) || row.message || '',
            progress,
            unitsDone,
            unitsTotal,
            link: row.link || null,
        };
    });
}

// Background jobs (everything that is not a queue row), split into live and finished. A child job of a
// pipeline is shown through its parent; an extraction job through its queue row.
export function backgroundJobs(activeJobs = {}) {
    const cards = Object.values(activeJobs || {}).filter(Boolean);
    const children = new Set();
    for (const card of cards) {
        for (const id of card.sub_jobs || []) {
            children.add(String(id));
            children.add(`bg_${id}`);
        }
    }
    const own = cards.filter(card => {
        const id = String(card.id);
        return !id.startsWith('bg_') && !id.startsWith('extract_') && !card.parentId && !children.has(id);
    });
    const byStart = (a, b) => (b.started_at || 0) - (a.started_at || 0);
    return {
        live: own.filter(card => LIVE.includes(card.status)).sort(byStart),
        finished: own.filter(card => FINISHED.includes(card.status)).sort((a, b) => (b.finished_at || 0) - (a.finished_at || 0)),
    };
}

// Everything the indicator counts as "working right now".
export function liveCount(snapshot, activeJobs) {
    return liveQueueRows(snapshot, activeJobs).length + backgroundJobs(activeJobs).live.length;
}
