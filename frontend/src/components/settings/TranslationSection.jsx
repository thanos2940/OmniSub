import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Bot, Cpu } from 'lucide-react';
import { Card, Field, INPUT, StatusChip, Toggle } from '../ui';
import ModelCombobox from '../ModelCombobox';
import { cx, FieldGrid, Notice, NumberInput, Disclosure, Divider } from './parts';
import GeminiKeyField from './GeminiKeyField';
import ModelEngineSettings from './ModelEngineSettings';

const ENGINES = [
    { id: 'antigravity', label: 'Antigravity', command: 'agy', hint: 'Default. Models from `agy models`.' },
    { id: 'claude', label: 'Claude Code', command: 'claude', hint: 'Uses your Claude subscription.' },
    { id: 'gemini', label: 'Gemini CLI', command: 'gemini', hint: 'Uses the Gemini CLI sign-in.' },
];

// What `agy models` lists today; any other name can still be typed.
const AGY_MODELS = [
    'gemini-3.8-flash-low', 'gemini-3.8-flash-medium', 'gemini-3.8-flash-high',
    'gemini-4-argon-low', 'gemini-4-argon-medium', 'gemini-4-argon-high',
    'claude-sonnet-5-5-low', 'claude-sonnet-5-5-medium', 'claude-sonnet-5-5-high',
    'claude-opus-5-5-low', 'claude-opus-5-5-medium', 'claude-opus-5-5-high',
    'gpt-oss-120b-medium',
];
const CLAUDE_MODELS = ['claude-sonnet-5-5', 'claude-opus-5-5'];
const SUGGESTIONS = { antigravity: AGY_MODELS, claude: CLAUDE_MODELS, gemini: [] };

function EngineTile({ engine, selected, found, onSelect }) {
    return (
        <button
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={onSelect}
            className={cx(
                'rounded-lg border p-3 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500',
                selected
                    ? 'border-indigo-500 bg-indigo-50/60 dark:border-indigo-400 dark:bg-indigo-950/30'
                    : 'border-gray-200 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800',
            )}
        >
            <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-semibold text-gray-900 dark:text-white">{engine.label}</span>
                {found === undefined
                    ? null
                    : <StatusChip tone={found ? 'success' : 'warning'}>{found ? 'Found' : 'Not found'}</StatusChip>}
            </div>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                <code className="font-mono">{engine.command}</code> · {engine.hint}
            </p>
        </button>
    );
}

function RunnerCard({ settings, onChange, runner }) {
    const { form, info, set } = runner;
    const found = info?.engines;
    const suggestions = SUGGESTIONS[form.engine] || [];
    const running = !!info?.running;
    const missing = found && form.engine && !found[form.engine];
    return (
        <Card
            title="Translation runner"
            subtitle="The agent CLI that translates queued episodes."
            actions={info && (
                <>
                    <StatusChip tone={running ? 'success' : 'neutral'}>{running ? 'Running' : 'Stopped'}</StatusChip>
                    {info.paused && <StatusChip tone="warning">Paused</StatusChip>}
                </>
            )}
        >
            <div className="space-y-5">
                <div>
                    <div className="mb-1.5 text-sm font-medium text-gray-700 dark:text-gray-300" id="runner-cli-label">Agent CLI</div>
                    <div role="radiogroup" aria-labelledby="runner-cli-label" className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                        {ENGINES.map(e => (
                            <EngineTile
                                key={e.id}
                                engine={e}
                                selected={form.engine === e.id}
                                found={found ? !!found[e.id] : undefined}
                                onSelect={() => set('engine', e.id)}
                            />
                        ))}
                    </div>
                    {missing && (
                        <Notice tone="warning" className="mt-3">
                            <p>This CLI was not found on the server, so translation cannot start with it.</p>
                            {form.engine === 'antigravity' && (
                                <p>Install Antigravity, then run <code className="font-mono">agy</code> once in a terminal as the account OmbiSub runs as and sign in.</p>
                            )}
                        </Notice>
                    )}
                    {running && <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">A running runner switches CLI and lane count the next time it starts.</p>}
                </div>

                <FieldGrid>
                    <Field label="Model" htmlFor="runner-model" hint={form.engine === 'antigravity' ? 'Pick one or type another. Applies at once.' : 'Leave empty for the CLI’s own default. Applies at once.'}>
                        <input
                            id="runner-model"
                            list="runner-model-options"
                            value={form.model}
                            onChange={(e) => set('model', e.target.value)}
                            placeholder="CLI default"
                            className={cx(INPUT, 'font-mono')}
                        />
                        <datalist id="runner-model-options">{suggestions.map(m => <option key={m} value={m} />)}</datalist>
                    </Field>
                    <Field label="Fallback models" htmlFor="runner-fallbacks" hint="Comma separated, tried in order while the main model’s quota is used up.">
                        <input
                            id="runner-fallbacks"
                            list="runner-model-options"
                            value={form.fallback_models}
                            onChange={(e) => set('fallback_models', e.target.value)}
                            placeholder="claude-sonnet-5-5-low"
                            className={cx(INPUT, 'font-mono')}
                        />
                    </Field>
                </FieldGrid>

                <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                    <Field label="Lanes" htmlFor="runner-lanes" hint="Episodes at once (1 to 8).">
                        <NumberInput id="runner-lanes" min={1} max={8} value={form.lanes} onChange={(v) => set('lanes', v)} />
                    </Field>
                    <Field label="Per show" htmlFor="runner-per-show" hint="Episodes of one show at once.">
                        <NumberInput id="runner-per-show" min={1} max={8} value={form.per_show} onChange={(v) => set('per_show', v)} />
                    </Field>
                    <Field label="Lines per request" htmlFor="runner-chunk" hint="Longer episodes are split at scene changes.">
                        <NumberInput id="runner-chunk" min={40} max={600} step={10} value={form.chunk_lines} onChange={(v) => set('chunk_lines', v)} />
                    </Field>
                    <Field label="Fix rounds" htmlFor="runner-fix" hint="Follow-ups for missing or wrong lines.">
                        <NumberInput id="runner-fix" min={0} max={4} value={form.fix_rounds} onChange={(v) => set('fix_rounds', v)} />
                    </Field>
                </div>

                <Divider />
                <Toggle
                    label="Prepare a new show first"
                    description="Build the context guide and glossary before the first episode of a show that has none."
                    checked={form.bootstrap}
                    onChange={(v) => set('bootstrap', v)}
                />
                <Toggle
                    label="Reuse earlier translations"
                    description="Lines that match something already translated in the same show are taken from translation memory."
                    checked={settings.tm_enabled ?? true}
                    onChange={(v) => onChange('tm_enabled', v)}
                />
                <Toggle
                    label="Start with OmbiSub"
                    description="The runner starts when the server starts and keeps watching the queue. Pause and resume it on the Activity page."
                    checked={settings.agent_runner_autostart ?? true}
                    onChange={(v) => onChange('agent_runner_autostart', v)}
                />
            </div>
        </Card>
    );
}

function ApiRescueCard({ settings, onChange, runner }) {
    const { form, set } = runner;
    const [hasKey, setHasKey] = useState(null);
    return (
        <Card
            title="Gemini API for refused lines"
            subtitle="A safety filter in the agent CLI can refuse violent or crude dialogue. Those lines are retried through the Gemini API."
        >
            <div className="space-y-5">
                <Toggle
                    label="Retry refused lines through the Gemini API"
                    description="Runs at the end of each episode, only for the lines the CLI refused."
                    checked={form.api_rescue}
                    onChange={(v) => set('api_rescue', v)}
                />
                {form.api_rescue && hasKey === false && (
                    <Notice tone="warning">Refused lines cannot be retried until a Gemini API key is stored below. They stay untranslated and are reported.</Notice>
                )}
                <FieldGrid>
                    <Field label="Model for refused lines" hint="Empty uses the default Gemini model.">
                        <ModelCombobox
                            value={form.rescue_model}
                            onChange={(v) => set('rescue_model', v)}
                            placeholder="Default Gemini model"
                        />
                    </Field>
                    <Field
                        label="Safety filter"
                        htmlFor="gemini-safety"
                        hint="Off lets violent and crude dialogue through. Applies to every Gemini request. Google’s prohibited-content and child-safety blocks stay on whatever you choose."
                    >
                        <select
                            id="gemini-safety"
                            value={settings.gemini_safety_filter || 'off'}
                            onChange={(e) => onChange('gemini_safety_filter', e.target.value)}
                            className={INPUT}
                        >
                            <option value="off">Off (recommended)</option>
                            <option value="only_high">Block only high-risk content</option>
                            <option value="default">Google default</option>
                        </select>
                    </Field>
                </FieldGrid>
                <Divider />
                <GeminiKeyField onStatus={setHasKey} />
            </div>
        </Card>
    );
}

function EngineChoice({ settings, onChange }) {
    const engine = settings.translation_engine || 'agent';
    const options = [
        { id: 'agent', icon: Bot, title: 'Agent CLI (recommended)', body: 'Queued episodes are translated by the runner above through agy, Claude Code or the Gemini CLI.' },
        { id: 'model', icon: Cpu, title: 'Model API', body: 'A background worker calls a Gemini model (or a local model) directly. Needs an API key and uses its quota.' },
    ];
    return (
        <div className="space-y-4">
            <div>
                <div className="mb-1.5 text-sm font-medium text-gray-700 dark:text-gray-300" id="engine-label">Who translates the queue</div>
                <div role="radiogroup" aria-labelledby="engine-label" className="grid grid-cols-1 gap-2 md:grid-cols-2">
                    {options.map(o => {
                        const Icon = o.icon;
                        const selected = engine === o.id;
                        return (
                            <button
                                key={o.id}
                                type="button"
                                role="radio"
                                aria-checked={selected}
                                onClick={() => onChange('translation_engine', o.id)}
                                className={cx(
                                    'rounded-lg border p-3 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500',
                                    selected
                                        ? 'border-indigo-500 bg-indigo-50/60 dark:border-indigo-400 dark:bg-indigo-950/30'
                                        : 'border-gray-200 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800',
                                )}
                            >
                                <span className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white"><Icon size={16} />{o.title}</span>
                                <span className="mt-1 block text-xs text-gray-500 dark:text-gray-400">{o.body}</span>
                            </button>
                        );
                    })}
                </div>
            </div>
            {engine === 'model' ? (
                <Notice tone="warning">
                    With the model API the runner above does not translate. Extraction and export keep running. Context, glossary and single-line
                    retranslation also use the model chosen below.
                </Notice>
            ) : (
                <Field label="Release an idle claim after" htmlFor="claim-ttl" hint="An episode an agent claimed but has not touched for this long goes back to the queue.">
                    <div className="flex items-center gap-2">
                        <NumberInput id="claim-ttl" min={1} max={168} value={settings.agent_claim_ttl_hours ?? 12} onChange={(v) => onChange('agent_claim_ttl_hours', v)} />
                        <span className="text-sm text-gray-500 dark:text-gray-400">hours</span>
                    </div>
                </Field>
            )}
        </div>
    );
}

export default function TranslationSection({ settings, onChange, runner }) {
    return (
        <div className="space-y-5">
            <Notice tone="info">
                <p className="font-medium">How translation works</p>
                <p>
                    Everything that is queued, by you, by the Autopilot or from Sonarr and Radarr, is translated by the runner through an agent CLI,
                    Antigravity (<code className="font-mono">agy</code>) by default. Lines the CLI refuses on safety grounds go to the Gemini API.
                    The runner runs all the time and starts with OmbiSub. <Link to="/activity" className="font-medium underline">Activity</Link> shows
                    its progress and has Pause and Resume.
                </p>
            </Notice>
            {!runner.info && (
                <Notice tone="warning">The runner’s state could not be read, so its settings cannot be saved right now. Reload the page to try again.</Notice>
            )}
            <RunnerCard settings={settings} onChange={onChange} runner={runner} />
            <ApiRescueCard settings={settings} onChange={onChange} runner={runner} />
            <Disclosure
                title="Advanced: translate with a model API instead"
                subtitle={`Currently: ${(settings.translation_engine || 'agent') === 'model' ? 'model API' : 'agent CLI'}`}
                defaultOpen={(settings.translation_engine || 'agent') === 'model'}
            >
                <EngineChoice settings={settings} onChange={onChange} />
                {settings.translation_engine === 'model' && <ModelEngineSettings settings={settings} onChange={onChange} />}
            </Disclosure>
        </div>
    );
}
