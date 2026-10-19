// Translation actions shared by the project page, the bulk bar and the episode view: one place that knows
// the two engines. `agent` queues the work for the runner (and starts a stopped runner); `model` keeps the
// Gemini worker path. Also: the engine setting, and the live queue state of a project's episodes.
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { formatErrorMessage } from '../context/ToastContext';
import { useAdaptivePolling } from './useAdaptivePolling';

export const errorText = (err, fallback = 'Something went wrong.') =>
    formatErrorMessage(err?.response?.data?.detail ?? err?.response?.data?.message ?? err?.message) || fallback;

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// 'agent' | 'model' (null while the setting is loading).
export function useTranslationEngine() {
    const [engine, setEngine] = useState(null);
    useEffect(() => {
        let alive = true;
        api.getSettings()
            .then(res => { if (alive) setEngine(res.data?.translation_engine === 'model' ? 'model' : 'agent'); })
            .catch(() => { if (alive) setEngine('agent'); });
        return () => { alive = false; };
    }, []);
    return engine;
}

// A stopped runner would leave queued work waiting; start it (harmless when it is already running).
async function ensureRunnerStarted() {
    try {
        const res = await api.getAgentRunner();
        if (res.data?.status !== 'stopped') return { started: false };
        await api.startAgentRunner({});
        return { started: true };
    } catch (err) {
        return { error: errorText(err, 'The runner did not answer.') };
    }
}

function missingMessage(r) {
    const queued = r.queued_for_translation || 0;
    const extracting = r.queued_for_extraction || 0;
    const noSource = r.no_usable_source || 0;
    const parts = [];
    if (queued) parts.push(`${queued} for translation`);
    if (extracting) parts.push(`${extracting} for extraction`);
    let text = parts.length ? `Queued ${parts.join(', ')}` : '';
    if (noSource) text += `${text ? '; ' : ''}${noSource} without a usable subtitle`;
    if (!text) {
        text = r.already_available ? `Nothing to do: ${plural(r.already_available, 'episode already has', 'episodes already have')} a translation.` : 'Nothing to queue.';
    }
    const reasons = (r.unavailable || []).slice(0, 3).map(u => `${u.episode}: ${u.reason || 'no usable subtitle'}`);
    if (reasons.length) {
        const more = (r.unavailable || []).length - reasons.length;
        text += `. ${reasons.join(' · ')}${more > 0 ? ` · and ${more} more` : ''}`;
    }
    return text;
}

// translate(names, { retranslate }) and translateMissing(). `notice` describes the last queued batch for an
// inline strip with a link to Activity; `busy` is true while a request is in flight.
export function useTranslate({ projectName, engine, toast, addJob }) {
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState(null);
    const inFlight = useRef(false);

    const run = useCallback(async (work) => {
        if (inFlight.current) return null;
        inFlight.current = true;
        setBusy(true);
        try {
            return await work();
        } finally {
            inFlight.current = false;
            setBusy(false);
        }
    }, []);

    const afterQueued = useCallback(async (message, count) => {
        const runner = engine === 'model' ? {} : await ensureRunnerStarted();
        if (runner.error) {
            toast?.error?.(`${message}, but the runner could not be started: ${runner.error} Start it in Activity.`);
            setNotice({ tone: 'warning', text: `${message}. The runner is stopped and could not be started: ${runner.error}`, count });
            return;
        }
        const text = runner.started ? `${message}. The runner was stopped, so it has been started.` : message;
        toast?.success?.(text);
        setNotice({ tone: 'success', text, count, activity: engine !== 'model' });
    }, [engine, toast]);

    const translate = useCallback((names, { retranslate = false } = {}) => run(async () => {
        const list = [...new Set((names || []).map(n => String(n || '').trim()).filter(Boolean))];
        if (!list.length) return null;
        try {
            if (engine === 'model') {
                const link = `/project/${encodeURIComponent(projectName)}`;
                const options = { force: retranslate, translation_type: retranslate ? 'original' : 'saved' };
                const res = list.length === 1
                    ? await api.translateEpisode(projectName, list[0], options)
                    : await api.batchTranslate(projectName, { episode_names: list, ...options });
                const jobId = res.data?.job_id;
                if (jobId && addJob) {
                    addJob(jobId, list.length === 1 ? 'translate_episode' : 'batch_translate',
                        list.length === 1 ? `Translating ${projectName} - ${list[0]}` : `Translating ${projectName} (${list.length} episodes)`,
                        { projectId: projectName, episodeName: list.length === 1 ? list[0] : undefined, episodeNames: list, link });
                }
                const text = list.length === 1 ? `Translation started for ${list[0]}.` : `Translation started for ${list.length} episodes.`;
                toast?.success?.(text);
                setNotice({ tone: 'success', text, count: list.length, activity: false });
                return { queued: list.length };
            }
            const res = await api.enqueueForAgent(projectName, list, 1, retranslate ? { full: true, include_translated: true } : {});
            const queued = res.data?.enqueued_count ?? 0;
            if (!queued) {
                const text = list.length === 1 ? `${list[0]} is already waiting in the queue.` : 'These episodes are already waiting in the queue.';
                toast?.info?.(text);
                setNotice({ tone: 'info', text, count: 0, activity: true });
                return { queued: 0 };
            }
            const verb = retranslate ? 'for retranslation' : '';
            const message = queued === 1
                ? `Queued ${list.length === 1 ? list[0] : '1 episode'}${verb ? ` ${verb}` : ''} — the runner translates it next`
                : `Queued ${queued} episodes${verb ? ` ${verb}` : ''} — the runner works through them in order`;
            await afterQueued(message, queued);
            return { queued };
        } catch (err) {
            toast?.error?.(errorText(err, 'Could not start the translation.'));
            return null;
        }
    }), [run, engine, projectName, addJob, toast, afterQueued]);

    const translateMissing = useCallback(() => run(async () => {
        try {
            const res = await api.enqueueMissing(projectName);
            const r = res.data || {};
            if (r.error) {
                toast?.error?.(r.error);
                return r;
            }
            const text = missingMessage(r);
            const queued = (r.queued_for_translation || 0) + (r.queued_for_extraction || 0);
            if (queued > 0) await afterQueued(text, queued);
            else {
                toast?.info?.(text);
                setNotice({ tone: 'info', text, count: 0, activity: false });
            }
            return r;
        } catch (err) {
            toast?.error?.(errorText(err, 'Could not queue the missing episodes.'));
            return null;
        }
    }), [run, projectName, toast, afterQueued]);

    const clearNotice = useCallback(() => setNotice(null), []);
    return { translate, translateMissing, busy, notice, clearNotice };
}

// Live queue state of one project's episodes: Map(episodeName -> {phase: 'running'|'queued'|'extracting', progress}).
// `onSettled` fires when an episode that was in the queue is no longer there (so the page can reload it).
export function useEpisodeQueue(projectName, onSettled) {
    const [active, setActive] = useState(() => new Map());
    const previous = useRef(new Set());
    const settledRef = useRef(onSettled);
    settledRef.current = onSettled;

    const poll = useCallback(async () => {
        if (!projectName) return '';
        const res = await api.getQueue();
        const mine = String(projectName).toLowerCase();
        const next = new Map();
        const consider = (item, kind) => {
            if (!item?.episode_name || String(item.project_name || '').toLowerCase() !== mine) return;
            const status = item.status;
            if (!['running', 'pending', 'queued'].includes(status)) return;
            const phase = kind === 'extraction' ? 'extracting' : (status === 'running' ? 'running' : 'queued');
            if (next.has(item.episode_name) && phase !== 'running') return;
            next.set(item.episode_name, { phase, progress: Number.isFinite(item.progress) ? item.progress : null });
        };
        (res.data?.items || []).forEach(i => consider(i, i.kind === 'extraction' ? 'extraction' : 'translation'));
        (res.data?.extractions || []).forEach(i => consider(i, 'extraction'));
        const gone = [...previous.current].some(name => !next.has(name));
        previous.current = new Set(next.keys());
        setActive(next);
        if (gone) settledRef.current?.();
        return JSON.stringify([...next.entries()]);
    }, [projectName]);

    useAdaptivePolling({ enabled: !!projectName, pollFn: poll, baseInterval: 3000, maxInterval: 12000, backoffThreshold: 2 });
    return { active, refresh: poll };
}
