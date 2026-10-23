import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import { Captions, Languages, Loader2, Lock, Plane, Plug, SlidersHorizontal } from 'lucide-react';
import { api } from '../api';
import { useToast } from '../context/ToastContext';
import { Button, Page, PageHeader } from './ui';
import { cx, errorText } from './settings/parts';
import TranslationSection from './settings/TranslationSection';
import ConnectionsSection from './settings/ConnectionsSection';
import SubtitlesSection from './settings/SubtitlesSection';
import AutopilotSettings from './settings/AutopilotSettings';
import SecuritySettings from './settings/SecuritySettings';
import GeneralSection, { validateGeneral } from './settings/GeneralSection';

const SECTIONS = [
    { id: 'translation', label: 'Translation', icon: Languages, blurb: 'Who translates, agy or the Gemini API, and how.' },
    { id: 'connections', label: 'Connections', icon: Plug, blurb: 'Sonarr, Radarr, Plex, path mappings and webhooks.' },
    { id: 'subtitles', label: 'Subtitles', icon: Captions, blurb: 'Source formats, extraction, formatting and SubtitleEdit.' },
    { id: 'automation', label: 'Automation', icon: Plane, blurb: 'The Autopilot and its notifications.' },
    { id: 'security', label: 'Security', icon: Lock, blurb: 'Login and the API key for other tools.' },
    { id: 'general', label: 'General', icon: SlidersHorizontal, blurb: 'Languages and network.' },
];

// Tab ids from before the redesign, still passed in router state by older links.
const LEGACY_TABS = { gemini: 'translation', presets: 'translation', media: 'connections', autopilot: 'automation' };

const NOT_SAVED_WITH_SETTINGS = ['secrets_set', 'autopilot_enabled_at'];

const runnerToForm = (cfg = {}) => ({
    engine: cfg.engine || 'antigravity',
    model: cfg.model || '',
    fallback_models: (cfg.fallback_models || []).join(', '),
    lanes: cfg.lanes ?? 3,
    per_show: cfg.per_show ?? 2,
    chunk_lines: cfg.chunk_lines ?? 250,
    fix_rounds: cfg.fix_rounds ?? 2,
    bootstrap: cfg.bootstrap ?? true,
    api_rescue: cfg.api_rescue ?? true,
    rescue_model: cfg.rescue_model || '',
});

const runnerToPayload = (form) => ({
    ...form,
    model: form.model.trim(),
    rescue_model: form.rescue_model.trim(),
    fallback_models: form.fallback_models.split(',').map(m => m.trim()).filter(Boolean),
});

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// What the server did when the translation engine changed (POST /api/settings `engine_change`).
const engineChangeText = (change) => {
    if (!change?.engine) return null;
    if (change.engine === 'model') {
        const parts = ['Saved. Translation now goes to the Gemini API.'];
        if (change.runner_stopping) parts.push('The agy runner finishes the episodes in hand, then stops.');
        if (change.handed_to_worker) parts.push(`${change.handed_to_worker} queued episode${change.handed_to_worker === 1 ? '' : 's'} moved to the Gemini API.`);
        return parts.join(' ');
    }
    return `Saved. Translation now goes to agy${change.runner_started ? '; the runner has started' : '. Start the runner on the Activity page'}.`;
};

const normalizeLoaded = (data) => ({
    ...data,
    arr_path_mappings: data.arr_path_mappings || data.path_mappings || [],
});

export default function SettingsPage() {
    const toast = useToast();
    const toastRef = useRef(toast);
    toastRef.current = toast;
    const location = useLocation();
    const [params, setParams] = useSearchParams();

    const requested = params.get('section') || LEGACY_TABS[location.state?.tab] || location.state?.tab;
    const section = SECTIONS.some(s => s.id === requested) ? requested : 'translation';
    const goTo = (id) => setParams({ section: id }, { replace: true });

    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [settings, setSettings] = useState({});
    const [saved, setSaved] = useState({});
    const [runnerInfo, setRunnerInfo] = useState(null);
    const [runnerForm, setRunnerForm] = useState(runnerToForm());
    const [savedRunnerForm, setSavedRunnerForm] = useState(runnerToForm());
    const [status, setStatus] = useState(null);

    const load = useCallback(async () => {
        const [s, r, st] = await Promise.allSettled([api.getSettings(), api.getAgentRunner(), api.getSystemStatus()]);
        if (s.status === 'fulfilled') {
            const data = normalizeLoaded(s.value.data || {});
            setSettings(data);
            setSaved(data);
        } else {
            toastRef.current.error(`Could not load the settings: ${errorText(s.reason)}`);
        }
        if (r.status === 'fulfilled') {
            const info = r.value.data;
            setRunnerInfo(info);
            const form = runnerToForm(info?.config);
            setRunnerForm(form);
            setSavedRunnerForm(form);
        } else {
            setRunnerInfo(null);
        }
        setStatus(st.status === 'fulfilled' ? st.value.data : null);
        setLoading(false);
    }, []);

    useEffect(() => { load(); }, [load]);

    const onChange = useCallback((key, value) => setSettings(prev => ({ ...prev, [key]: value })), []);
    const runner = useMemo(() => ({
        info: runnerInfo,
        form: runnerForm,
        set: (key, value) => setRunnerForm(prev => ({ ...prev, [key]: value })),
    }), [runnerInfo, runnerForm]);

    const changedKeys = useMemo(
        () => Object.keys(settings).filter(k => !NOT_SAVED_WITH_SETTINGS.includes(k) && !same(settings[k], saved[k])),
        [settings, saved],
    );
    const runnerDirty = !!runnerInfo && !same(runnerForm, savedRunnerForm);
    const dirty = changedKeys.length > 0 || runnerDirty;
    const problem = validateGeneral(settings);

    useEffect(() => {
        if (!dirty) return undefined;
        const warn = (e) => { e.preventDefault(); e.returnValue = ''; };
        window.addEventListener('beforeunload', warn);
        return () => window.removeEventListener('beforeunload', warn);
    }, [dirty]);

    const discard = () => {
        setSettings(saved);
        setRunnerForm(savedRunnerForm);
    };

    const save = async () => {
        setSaving(true);
        try {
            // Runner first: switching the engine to agy starts the runner with the config saved here.
            if (runnerDirty) await api.saveAgentRunnerConfig(runnerToPayload(runnerForm));
            let change = null;
            if (changedKeys.length) {
                const payload = Object.fromEntries(changedKeys.map(k => [k, settings[k]]));
                change = (await api.updateSettings(payload)).data?.engine_change || null;
            }
            toast.success(engineChangeText(change) || 'Settings saved');
            await load();
        } catch (err) {
            toast.error(`Could not save: ${errorText(err)}`);
        } finally {
            setSaving(false);
        }
    };

    const current = SECTIONS.find(s => s.id === section);

    return (
        <Page>
            <PageHeader title="Settings" subtitle="Changes apply when you press Save." />

            {loading ? (
                <div className="flex min-h-[320px] flex-col items-center justify-center gap-3 text-sm text-gray-500 dark:text-gray-400">
                    <Loader2 className="animate-spin text-indigo-600" size={28} />
                    Loading settings
                </div>
            ) : (
                <div className="grid grid-cols-1 gap-6 md:grid-cols-[13rem_minmax(0,1fr)]">
                    <div>
                        <label htmlFor="settings-section" className="sr-only">Section</label>
                        <select id="settings-section" className="block w-full rounded-lg border border-gray-250 bg-white px-3 py-2 text-sm text-gray-900 md:hidden dark:border-gray-700 dark:bg-gray-900 dark:text-white" value={section} onChange={(e) => goTo(e.target.value)}>
                            {SECTIONS.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
                        </select>
                        <nav aria-label="Settings sections" className="sticky top-20 hidden flex-col gap-0.5 md:flex">
                            {SECTIONS.map(s => {
                                const Icon = s.icon;
                                const active = s.id === section;
                                return (
                                    <button
                                        key={s.id}
                                        type="button"
                                        onClick={() => goTo(s.id)}
                                        aria-current={active ? 'page' : undefined}
                                        className={cx(
                                            'flex items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500',
                                            active
                                                ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-300'
                                                : 'text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800',
                                        )}
                                    >
                                        <Icon size={16} />
                                        {s.label}
                                    </button>
                                );
                            })}
                        </nav>
                    </div>

                    <div className="min-w-0 max-w-4xl">
                        <div className="mb-4">
                            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">{current.label}</h2>
                            <p className="text-sm text-gray-500 dark:text-gray-400">{current.blurb}</p>
                        </div>
                        {section === 'translation' && <TranslationSection settings={settings} onChange={onChange} runner={runner} savedEngine={saved.translation_engine} />}
                        {section === 'connections' && <ConnectionsSection settings={settings} onChange={onChange} />}
                        {section === 'subtitles' && <SubtitlesSection settings={settings} onChange={onChange} />}
                        {section === 'automation' && <AutopilotSettings settings={settings} onChange={onChange} />}
                        {section === 'security' && <SecuritySettings />}
                        {section === 'general' && <GeneralSection settings={settings} onChange={onChange} status={status} />}

                        {dirty && (
                            <div role="region" aria-label="Unsaved changes" className="sticky bottom-4 z-20 mt-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-lg dark:border-gray-700 dark:bg-gray-850">
                                <p className="text-sm text-gray-700 dark:text-gray-200">
                                    {problem || 'You have unsaved changes.'}
                                </p>
                                <div className="flex gap-2">
                                    <Button variant="ghost" onClick={discard} disabled={saving}>Discard</Button>
                                    <Button variant="primary" onClick={save} loading={saving} disabled={!!problem}>Save</Button>
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </Page>
    );
}
