import { useState, useEffect } from 'react';
import { Bot, Sparkles } from 'lucide-react';
import { api } from '../../api';
import { Button, StatusChip } from '../ui';
import { cx, errorText, Notice } from '../settings/parts';
import GeminiKeyField from '../settings/GeminiKeyField';

const CLIS = [
    { id: 'antigravity', name: 'Antigravity', command: 'agy', note: 'The default. Sign in with a Google account.' },
    { id: 'claude', name: 'Claude Code', command: 'claude', note: 'Uses a Claude subscription.' },
    { id: 'gemini', name: 'Gemini CLI', command: 'gemini', note: 'Uses the Gemini CLI sign-in.' },
];

const ENGINES = [
    { id: 'agent', icon: Bot, name: 'An agent CLI on this machine',
      note: 'Antigravity, Claude Code or the Gemini CLI translates with your own plan. Needs the CLI installed and signed in on the server.' },
    { id: 'model', icon: Sparkles, name: 'The Gemini API',
      note: 'Translates with a Gemini API key from Google AI Studio. Nothing to install: the choice for Docker and servers without a CLI.' },
];

const Choice = ({ selected, onClick, children }) => (
    <button
        type="button"
        role="radio"
        aria-checked={selected}
        onClick={onClick}
        className={cx(
            'flex w-full items-center justify-between gap-3 rounded-xl border p-3 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500',
            selected
                ? 'border-indigo-500 bg-indigo-50/60 dark:border-indigo-400 dark:bg-indigo-950/30'
                : 'border-gray-200 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800',
        )}
    >
        {children}
    </button>
);

const StepProvider = ({ onNext, onBack, onSkip }) => {
    const [engines, setEngines] = useState(null); // { antigravity: path | null, ... }
    const [loadError, setLoadError] = useState(null);
    const [mode, setMode] = useState(null);       // 'agent' | 'model'
    const [engine, setEngine] = useState('antigravity');
    const [hasKey, setHasKey] = useState(false);
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState(null);

    useEffect(() => {
        Promise.all([api.getAgentRunner(), api.getSettings().catch(() => null)])
            .then(([res, settings]) => {
                const found = res.data?.engines || {};
                setEngines(found);
                const current = res.data?.config?.engine;
                setEngine(found[current] ? current : (CLIS.find(c => found[c.id])?.id || current || 'antigravity'));
                const saved = settings?.data?.translation_engine;
                // Without any agent CLI on this machine the Gemini API is the only way that works out of the box.
                setMode(saved === 'model' || !CLIS.some(c => found[c.id]) ? 'model' : 'agent');
            })
            .catch(err => {
                setLoadError(errorText(err, 'Could not look for the agent CLIs'));
                setMode('model');
            });
    }, []);

    const anyFound = !!engines && CLIS.some(c => engines[c.id]);

    const handleNext = async () => {
        setSaving(true);
        setSaveError(null);
        try {
            if (mode === 'agent') await api.saveAgentRunnerConfig({ engine });
            await api.updateSettings({ translation_engine: mode });
            onNext();
        } catch (err) {
            setSaveError(errorText(err, 'Could not save the choice'));
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="space-y-6">
            <div>
                <div className="mb-1 flex items-center gap-2">
                    <Bot className="text-indigo-600 dark:text-indigo-400" size={22} />
                    <h2 className="text-xl font-bold text-gray-900 dark:text-white">How translation runs</h2>
                </div>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                    Pick who translates the queue. You can switch later in Settings › Translation.
                </p>
            </div>

            <div role="radiogroup" aria-label="Translate with" className="space-y-2">
                {ENGINES.map(e => (
                    <Choice key={e.id} selected={mode === e.id} onClick={() => setMode(e.id)}>
                        <span className="flex min-w-0 items-start gap-3">
                            <e.icon size={18} className="mt-0.5 shrink-0 text-indigo-600 dark:text-indigo-400" />
                            <span className="min-w-0">
                                <span className="block text-sm font-semibold text-gray-900 dark:text-white">{e.name}</span>
                                <span className="block text-xs text-gray-500 dark:text-gray-400">{e.note}</span>
                            </span>
                        </span>
                        {e.id === 'agent' && engines && (
                            <StatusChip tone={anyFound ? 'success' : 'neutral'} className="shrink-0 whitespace-nowrap">{anyFound ? 'CLI found' : 'No CLI found'}</StatusChip>
                        )}
                    </Choice>
                ))}
            </div>

            {mode === 'agent' && (
                <>
                    <div role="radiogroup" aria-label="Agent CLI" className="space-y-2">
                        {CLIS.map(c => {
                            const found = engines ? !!engines[c.id] : null;
                            return (
                                <Choice key={c.id} selected={engine === c.id} onClick={() => setEngine(c.id)}>
                                    <span className="min-w-0">
                                        <span className="block text-sm font-semibold text-gray-900 dark:text-white">{c.name} <code className="font-mono text-xs font-normal text-gray-500 dark:text-gray-400">{c.command}</code></span>
                                        <span className="block text-xs text-gray-500 dark:text-gray-400">{c.note}</span>
                                    </span>
                                    {found !== null && <StatusChip tone={found ? 'success' : 'neutral'}>{found ? 'Found' : 'Not found'}</StatusChip>}
                                </Choice>
                            );
                        })}
                    </div>
                    {loadError && <Notice tone="warning">{loadError}. You can set the CLI later in Settings.</Notice>}
                    <Notice tone={anyFound ? 'info' : 'warning'}>
                        <p className="font-medium">{anyFound ? 'Sign in once' : 'No agent CLI was found on this machine'}</p>
                        <p>
                            {anyFound
                                ? <>Open a terminal as the account Omnisub runs as, run <code className="font-mono">agy</code> (or <code className="font-mono">claude</code>) once and sign in. Omnisub cannot do this step for you.</>
                                : <>Install Antigravity, then run <code className="font-mono">agy</code> once in a terminal as the account Omnisub runs as and sign in. Until then nothing is translated — or choose the Gemini API above.</>}
                        </p>
                    </Notice>
                    <div className="space-y-2 rounded-xl border border-gray-200 p-4 dark:border-gray-700">
                        <div>
                            <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Gemini API key <span className="font-normal text-gray-500 dark:text-gray-400">(optional)</span></h3>
                            <p className="text-xs text-gray-500 dark:text-gray-400">Only used for lines the CLI refuses, such as violent or crude dialogue. Without a key those lines stay untranslated and are reported.</p>
                        </div>
                        <GeminiKeyField compact onStatus={setHasKey} />
                    </div>
                </>
            )}

            {mode === 'model' && (
                <div className="space-y-3 rounded-xl border border-gray-200 p-4 dark:border-gray-700">
                    <div>
                        <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Gemini API key</h3>
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                            Every translation, the show context and the glossary are made with this key. Google AI Studio gives a free key with daily limits; a billed key has higher ones.
                        </p>
                    </div>
                    <GeminiKeyField compact onStatus={setHasKey} />
                    {!hasKey && <Notice tone="warning">Without a key nothing is translated. You can add it now or later in Settings › Translation.</Notice>}
                </div>
            )}

            {saveError && <Notice tone="danger">{saveError}</Notice>}
            <div className="flex items-center justify-between pt-1">
                <button type="button" onClick={onBack} disabled={!onBack} className="text-sm text-gray-400 hover:text-gray-600 disabled:opacity-0 dark:hover:text-gray-300">Back</button>
                <div className="flex items-center gap-3">
                    <button type="button" onClick={onSkip} className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">Configure later</button>
                    <Button variant="primary" onClick={handleNext} loading={saving} disabled={!mode}>Continue</Button>
                </div>
            </div>
        </div>
    );
};

export default StepProvider;
