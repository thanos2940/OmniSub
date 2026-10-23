import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Bot, Send, Sparkles } from 'lucide-react';
import { api } from '../../api';
import { Button, Card, Field, INPUT, StatusChip, Toggle } from '../ui';
import ModelCombobox from '../ModelCombobox';
import { cx, errorText, FieldGrid, Notice, NumberInput, Disclosure, Divider } from './parts';
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
                <Field label="Release an idle claim after" htmlFor="claim-ttl" hint="An episode an agent claimed but has not touched for this long goes back to the queue.">
                    <div className="flex items-center gap-2">
                        <NumberInput id="claim-ttl" min={1} max={168} value={settings.agent_claim_ttl_hours ?? 12} onChange={(v) => onChange('agent_claim_ttl_hours', v)} />
                        <span className="text-sm text-gray-500 dark:text-gray-400">hours</span>
                    </div>
                </Field>
            </div>
        </Card>
    );
}

// Sends one line to a Gemini model through the same request path translations use, and shows what
// answered: proof that a model name (a -latest alias included) works with the stored key.
function TestModelLine({ defaultModel }) {
    const [model, setModel] = useState('');
    const [busy, setBusy] = useState(false);
    const [result, setResult] = useState(null);
    const chosen = model.trim() || defaultModel || '';
    const run = async () => {
        setBusy(true);
        setResult(null);
        try {
            setResult((await api.testGeminiModel(chosen || null)).data);
        } catch (err) {
            setResult({ ok: false, error: errorText(err, 'The test did not run') });
        } finally {
            setBusy(false);
        }
    };
    return (
        <div className="space-y-2">
            <Field label="Try a model" hint="Translates one short line with this model. Uses one small request of its quota.">
                <div className="flex flex-col gap-2 sm:flex-row">
                    <div className="sm:flex-1">
                        <ModelCombobox value={model} onChange={setModel} placeholder={defaultModel || 'Default Gemini model'} />
                    </div>
                    <Button icon={Send} loading={busy} onClick={run}>Send a test line</Button>
                </div>
            </Field>
            {result && (result.ok ? (
                <Notice tone="success">
                    <p>
                        <code className="font-mono">{result.model}</code> answered in {result.seconds} s
                        {result.answered_by && result.answered_by !== result.model && <> (served by <code className="font-mono">{result.answered_by}</code>)</>}
                        {result.thinking_budget === 0 ? ', thinking off' : result.thinking_budget == null ? ', model’s own thinking' : `, thinking budget ${result.thinking_budget}`}:
                    </p>
                    <p className="mt-1 italic">{result.answer}</p>
                </Notice>
            ) : (
                <Notice tone="danger">{result.model ? <><code className="font-mono">{result.model}</code>: </> : null}{result.error}</Notice>
            ))}
        </div>
    );
}

function SafetyFilterField({ settings, onChange }) {
    return (
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
    );
}

// The Gemini API with agy translating: only for the lines agy's safety filter refuses.
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
                    <SafetyFilterField settings={settings} onChange={onChange} />
                </FieldGrid>
                <Divider />
                <GeminiKeyField onStatus={setHasKey} />
                {hasKey && <TestModelLine defaultModel={form.rescue_model || settings.default_translation_model} />}
            </div>
        </Card>
    );
}

// The Gemini API translating everything.
function GeminiApiCard({ settings, onChange }) {
    const [hasKey, setHasKey] = useState(null);
    return (
        <Card title="Gemini API" subtitle="Your key, the safety filter, and a quick check that a model answers.">
            <div className="space-y-5">
                {hasKey === false && (
                    <Notice tone="warning">No Gemini API key is stored, so nothing can be translated. Add one below.</Notice>
                )}
                <GeminiKeyField onStatus={setHasKey} />
                <SafetyFilterField settings={settings} onChange={onChange} />
                {hasKey && <TestModelLine defaultModel={settings.default_translation_model} />}
            </div>
        </Card>
    );
}

const CHOICES = [
    {
        id: 'agent', icon: Bot, title: 'Antigravity (agy)', badge: 'Recommended',
        body: 'The runner translates through the agy CLI and your Antigravity plan. The Gemini API only gets the lines agy’s safety filter refuses.',
    },
    {
        id: 'model', icon: Sparkles, title: 'Gemini API',
        body: 'Episodes go straight to the Gemini API with your key and its quota. agy is not used.',
    },
];

// Who translates the queue. Everything else on this page follows from it.
function EngineChoice({ settings, onChange, savedEngine, runner }) {
    const engine = settings.translation_engine || 'agent';
    const agyFound = runner.info?.engines ? !!runner.info.engines.antigravity : undefined;
    return (
        <Card title="Translate with" subtitle="Applies to every Translate button, the Autopilot, and context, glossary and single-line requests.">
            <div className="space-y-3">
                <div role="radiogroup" aria-label="Translate with" className="grid grid-cols-1 gap-2 md:grid-cols-2">
                    {CHOICES.map(o => {
                        const Icon = o.icon;
                        const selected = engine === o.id;
                        return (
                            <button
                                key={o.id}
                                type="button"
                                role="radio"
                                aria-checked={selected}
                                aria-label={o.title}
                                aria-describedby={`engine-${o.id}-desc`}
                                onClick={() => onChange('translation_engine', o.id)}
                                className={cx(
                                    'rounded-lg border p-3 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500',
                                    selected
                                        ? 'border-indigo-500 bg-indigo-50/60 dark:border-indigo-400 dark:bg-indigo-950/30'
                                        : 'border-gray-200 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800',
                                )}
                            >
                                <span className="flex flex-wrap items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
                                    <Icon size={16} />{o.title}
                                    {o.badge && <StatusChip tone="info">{o.badge}</StatusChip>}
                                    {o.id === 'agent' && agyFound === false && <StatusChip tone="warning">agy not found</StatusChip>}
                                </span>
                                <span id={`engine-${o.id}-desc`} className="mt-1 block text-xs text-gray-500 dark:text-gray-400">{o.body}</span>
                            </button>
                        );
                    })}
                </div>
                {engine !== savedEngine && (
                    <Notice tone="info">
                        {engine === 'model'
                            ? 'When you save, the agy runner finishes the episodes in hand and stops, and the waiting episodes go to the Gemini API.'
                            : (settings.agent_runner_autostart ?? true)
                                ? 'When you save, the Gemini worker takes no new episodes and the agy runner starts on the queue.'
                                : 'When you save, the Gemini worker takes no new episodes. “Start with OmbiSub” is off, so start the runner on the Activity page.'}
                    </Notice>
                )}
            </div>
        </Card>
    );
}

export default function TranslationSection({ settings, onChange, runner, savedEngine }) {
    const engine = settings.translation_engine || 'agent';
    return (
        <div className="space-y-5">
            <EngineChoice settings={settings} onChange={onChange} savedEngine={savedEngine || 'agent'} runner={runner} />
            {engine === 'agent' ? (
                <>
                    <Notice tone="info">
                        Everything that is queued, by you, by the Autopilot or from Sonarr and Radarr, is translated by the runner through an
                        agent CLI, Antigravity (<code className="font-mono">agy</code>) by default. <Link to="/activity" className="font-medium underline">Activity</Link> shows
                        its progress and has Pause and Resume.
                    </Notice>
                    {!runner.info && (
                        <Notice tone="warning">The runner’s state could not be read, so its settings cannot be saved right now. Reload the page to try again.</Notice>
                    )}
                    <RunnerCard settings={settings} onChange={onChange} runner={runner} />
                    <ApiRescueCard settings={settings} onChange={onChange} runner={runner} />
                </>
            ) : (
                <>
                    <GeminiApiCard settings={settings} onChange={onChange} />
                    <Card title="Models and quality" subtitle="Which Gemini models translate, and how carefully.">
                        <ModelEngineSettings settings={settings} onChange={onChange} />
                    </Card>
                    <Disclosure title="agy runner settings" subtitle="Not used while the Gemini API translates. Kept for when you switch back.">
                        <RunnerCard settings={settings} onChange={onChange} runner={runner} />
                    </Disclosure>
                </>
            )}
        </div>
    );
}
