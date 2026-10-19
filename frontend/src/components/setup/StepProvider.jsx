import { useState, useEffect } from 'react';
import { Bot } from 'lucide-react';
import { api } from '../../api';
import { Button, StatusChip } from '../ui';
import { cx, errorText, Notice } from '../settings/parts';
import GeminiKeyField from '../settings/GeminiKeyField';

const CLIS = [
    { id: 'antigravity', name: 'Antigravity', command: 'agy', note: 'The default. Sign in with a Google account.' },
    { id: 'claude', name: 'Claude Code', command: 'claude', note: 'Uses a Claude subscription.' },
    { id: 'gemini', name: 'Gemini CLI', command: 'gemini', note: 'Uses the Gemini CLI sign-in.' },
];

const StepProvider = ({ onNext, onBack, onSkip }) => {
    const [engines, setEngines] = useState(null); // { antigravity: path | null, ... }
    const [loadError, setLoadError] = useState(null);
    const [engine, setEngine] = useState('antigravity');
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState(null);

    useEffect(() => {
        api.getAgentRunner()
            .then(res => {
                const found = res.data?.engines || {};
                setEngines(found);
                const current = res.data?.config?.engine;
                setEngine(found[current] ? current : (CLIS.find(c => found[c.id])?.id || current || 'antigravity'));
            })
            .catch(err => setLoadError(errorText(err, 'Could not look for the agent CLIs')));
    }, []);

    const anyFound = !!engines && CLIS.some(c => engines[c.id]);

    const handleNext = async () => {
        setSaving(true);
        setSaveError(null);
        try {
            await api.saveAgentRunnerConfig({ engine });
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
                    OmbiSub hands your queue to an agent CLI that runs on this machine. Antigravity (<code className="font-mono">agy</code>) is
                    tried first. Pick the one you use.
                </p>
            </div>

            <div role="radiogroup" aria-label="Agent CLI" className="space-y-2">
                {CLIS.map(c => {
                    const found = engines ? !!engines[c.id] : null;
                    const selected = engine === c.id;
                    return (
                        <button
                            key={c.id}
                            type="button"
                            role="radio"
                            aria-checked={selected}
                            onClick={() => setEngine(c.id)}
                            className={cx(
                                'flex w-full items-center justify-between gap-3 rounded-xl border p-3 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500',
                                selected
                                    ? 'border-indigo-500 bg-indigo-50/60 dark:border-indigo-400 dark:bg-indigo-950/30'
                                    : 'border-gray-200 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800',
                            )}
                        >
                            <span className="min-w-0">
                                <span className="block text-sm font-semibold text-gray-900 dark:text-white">{c.name} <code className="font-mono text-xs font-normal text-gray-500 dark:text-gray-400">{c.command}</code></span>
                                <span className="block text-xs text-gray-500 dark:text-gray-400">{c.note}</span>
                            </span>
                            {found !== null && <StatusChip tone={found ? 'success' : 'neutral'}>{found ? 'Found' : 'Not found'}</StatusChip>}
                        </button>
                    );
                })}
            </div>
            {loadError && <Notice tone="warning">{loadError}. You can set the CLI later in Settings.</Notice>}

            <Notice tone={anyFound ? 'info' : 'warning'}>
                <p className="font-medium">{anyFound ? 'Sign in once' : 'No agent CLI was found on this machine'}</p>
                <p>
                    {anyFound
                        ? <>Open a terminal as the account OmbiSub runs as, run <code className="font-mono">agy</code> (or <code className="font-mono">claude</code>) once and sign in. OmbiSub cannot do this step for you.</>
                        : <>Install Antigravity, then run <code className="font-mono">agy</code> once in a terminal as the account OmbiSub runs as and sign in. The runner starts by itself once the CLI is there; you can finish this wizard now.</>}
                </p>
            </Notice>

            <div className="space-y-2 rounded-xl border border-gray-200 p-4 dark:border-gray-700">
                <div>
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Gemini API key <span className="font-normal text-gray-500 dark:text-gray-400">(optional)</span></h3>
                    <p className="text-xs text-gray-500 dark:text-gray-400">Only used for lines the CLI refuses, such as violent or crude dialogue. Without a key those lines stay untranslated and are reported.</p>
                </div>
                <GeminiKeyField compact />
            </div>

            {saveError && <Notice tone="danger">{saveError}</Notice>}
            <div className="flex items-center justify-between pt-1">
                <button type="button" onClick={onBack} disabled={!onBack} className="text-sm text-gray-400 hover:text-gray-600 disabled:opacity-0 dark:hover:text-gray-300">Back</button>
                <div className="flex items-center gap-3">
                    <button type="button" onClick={onSkip} className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">Configure later</button>
                    <Button variant="primary" onClick={handleNext} loading={saving}>Continue</Button>
                </div>
            </div>
        </div>
    );
};

export default StepProvider;
