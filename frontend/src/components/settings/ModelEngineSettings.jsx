import { Check } from 'lucide-react';
import { Field, INPUT, Toggle } from '../ui';
import ModelCombobox from '../ModelCombobox';
import { cx, Divider, FieldGrid, NumberInput, Slider } from './parts';
import { QUALITY_PRESETS, activePresetFor } from './presets';

// Everything that only applies when the Gemini worker (translation_engine = "model") translates:
// the model roster, quality presets, sampling, concurrency and the Batch API.

const STAGES = [
    ['glossary_enforce_enabled', 'Glossary enforcement', 'Checks that glossary terms and character titles are used.', true],
    ['character_profiles_enabled', 'Character speech profiles', 'Adds each character’s tone, formality and gender to the prompt.', false],
    ['episode_summaries_enabled', 'Episode summaries', 'Feeds earlier episodes’ plot summaries into the prompt.', false],
    ['repair_pass_enabled', 'Repair pass', 'Retranslates low-confidence or flagged lines in a separate round.', false],
    ['enable_reviewer', 'QC reviewer', 'A second model grades a sample of the translation.', false],
    ['conformance_enabled', 'Format conformance', 'Checks reading speed, line length and tag syntax.', false],
    ['new_terms_suggest_enabled', 'Suggest new terms', 'Flags recurring names and terms for the glossary.', true],
];

const ROSTER = [
    ['default_translation_model', 'Translation model'],
    ['fallback_translation_model', 'Fallback translation model'],
    ['default_context_model', 'Context analysis model'],
    ['default_glossary_model', 'Glossary model'],
    ['default_scan_model', 'Dialogue scan model'],
    ['default_review_model', 'QC reviewer model'],
];

function Heading({ children, hint }) {
    return (
        <div>
            <h3 className="text-sm font-semibold text-gray-900 dark:text-white">{children}</h3>
            {hint && <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">{hint}</p>}
        </div>
    );
}

export default function ModelEngineSettings({ settings, onChange }) {
    const active = activePresetFor(settings);
    const applyPreset = (values) => Object.entries(values).forEach(([k, v]) => onChange(k, v));
    return (
        <div className="space-y-6">
            <Divider />
            <Heading hint="Which Gemini model does each job. Type a name or pick from the list.">Models</Heading>
            <FieldGrid>
                {ROSTER.map(([key, label]) => (
                    <Field key={key} label={label}>
                        <ModelCombobox value={settings[key]} onChange={(v) => onChange(key, v)} placeholder="e.g. gemini-2.5-flash" />
                    </Field>
                ))}
            </FieldGrid>

            <Divider />
            <Heading hint="A starting point for the checks below.">Quality preset</Heading>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                {Object.entries(QUALITY_PRESETS).map(([key, preset]) => (
                    <button
                        key={key}
                        type="button"
                        onClick={() => applyPreset(preset.values)}
                        aria-pressed={active === key}
                        className={cx(
                            'rounded-lg border p-3 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500',
                            active === key
                                ? 'border-indigo-500 bg-indigo-50/60 dark:border-indigo-400 dark:bg-indigo-950/30'
                                : 'border-gray-200 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800',
                        )}
                    >
                        <span className="flex items-center justify-between text-sm font-semibold text-gray-900 dark:text-white">
                            {preset.label}
                            {active === key && <Check size={14} className="text-indigo-600 dark:text-indigo-400" />}
                        </span>
                        <span className="mt-1 block text-xs text-gray-500 dark:text-gray-400">{preset.desc}</span>
                    </button>
                ))}
            </div>
            <div className="grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-2">
                {STAGES.map(([key, label, description, fallback]) => (
                    <Toggle key={key} label={label} description={description} checked={settings[key] ?? fallback} onChange={(v) => onChange(key, v)} />
                ))}
            </div>

            <Divider />
            <Heading hint="How the transcript is grouped and sampled.">Requests</Heading>
            <FieldGrid>
                <Field label="Episode request mode" htmlFor="request-mode" hint="Whole-episode sends one request per episode when the model has room for it.">
                    <select
                        id="request-mode"
                        className={INPUT}
                        value={settings.episode_request_mode || 'auto'}
                        onChange={(e) => onChange('episode_request_mode', e.target.value)}
                    >
                        <option value="auto">Automatic</option>
                        <option value="chunked">Scene by scene</option>
                        <option value="per_episode">Whole episode</option>
                    </select>
                </Field>
                <div className="space-y-4">
                    <Toggle label="Sequential scenes" description="Passes earlier translated scenes into the next prompt." checked={!!settings.sequential_scenes} onChange={(v) => onChange('sequential_scenes', v)} />
                    <Toggle label="Prompt caching" description="Caches the glossary and guide on Gemini’s servers." checked={!!settings.prompt_caching_enabled} onChange={(v) => onChange('prompt_caching_enabled', v)} />
                </div>
            </FieldGrid>
            <FieldGrid cols={3}>
                <Slider label="Temperature" min={0} max={2} step={0.05} value={settings.temperature} onChange={(v) => onChange('temperature', v)} format={(v) => Number(v).toFixed(2)} unsetLabel="Google default" unsetAt={1} hint="Google recommends its default (1.0) for Gemini 3 models." />
                <Slider label="Top-P" min={0.1} max={1} step={0.01} value={settings.top_p} onChange={(v) => onChange('top_p', v)} format={(v) => Number(v).toFixed(2)} unsetLabel="Google default" unsetAt={0.95} />
                <Slider label="Top-K" min={1} max={100} step={1} value={settings.top_k} onChange={(v) => onChange('top_k', v)} unsetLabel="Google default" unsetAt={64} />
            </FieldGrid>
            <FieldGrid>
                <Slider label="Chunk size" min={50} max={400} step={10} value={settings.chunk_size ?? 150} onChange={(v) => onChange('chunk_size', v)} format={(v) => `${v} lines`} hint="Most cues in one chunk when chunking." />
            </FieldGrid>

            <Divider />
            <Heading hint="Parallel work and the discounted Batch API for backlog items.">Concurrency and batch</Heading>
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                <Field label="Episodes at once" htmlFor="me-conc-ep">
                    <NumberInput id="me-conc-ep" min={1} max={16} value={settings.concurrent_episodes ?? 2} onChange={(v) => onChange('concurrent_episodes', v)} />
                </Field>
                <Field label="Scenes at once" htmlFor="me-conc-sc">
                    <NumberInput id="me-conc-sc" min={1} max={16} value={settings.concurrent_scenes ?? 3} onChange={(v) => onChange('concurrent_scenes', v)} />
                </Field>
                <Field label="Batch window" htmlFor="me-batch-win" hint="Lines per batch request.">
                    <NumberInput id="me-batch-win" min={20} max={2000} step={10} value={settings.batch_window_size ?? 200} onChange={(v) => onChange('batch_window_size', v)} />
                </Field>
                <Field label="Batch poll" htmlFor="me-batch-poll" hint="Seconds between checks.">
                    <NumberInput id="me-batch-poll" min={10} max={3600} value={settings.batch_poll_seconds ?? 120} onChange={(v) => onChange('batch_poll_seconds', v)} />
                </Field>
            </div>
            <div className="grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-2">
                <Toggle label="Adaptive concurrency" description="Lowers parallelism when Gemini starts rate limiting." checked={!!settings.adaptive_concurrency_enabled} onChange={(v) => onChange('adaptive_concurrency_enabled', v)} />
                <Toggle label="Use the Batch API for backlog" description="Slower, about half the price." checked={!!settings.batch_api_enabled} onChange={(v) => onChange('batch_api_enabled', v)} />
            </div>
        </div>
    );
}
