import { useState, useEffect, useCallback } from 'react';
import { ExternalLink, KeyRound } from 'lucide-react';
import { api } from '../../api';
import { useToast } from '../../context/ToastContext';
import { Button, INPUT, StatusChip } from '../ui';
import { TestResult, errorText } from './parts';

// The Gemini API key: stored on the server (never sent back), so it is set, tested and removed here
// directly rather than with the rest of the settings. `compact` is for the setup wizard.
export default function GeminiKeyField({ onStatus, compact = false }) {
    const toast = useToast();
    const [hasKey, setHasKey] = useState(null);
    const [input, setInput] = useState('');
    const [busy, setBusy] = useState(null); // 'test' | 'save' | 'remove'
    const [result, setResult] = useState(null);

    const refresh = useCallback(async () => {
        try {
            const res = await api.getApiKeyStatus();
            const has = !!res.data?.has_key;
            setHasKey(has);
            onStatus?.(has);
        } catch {
            setHasKey(false);
            onStatus?.(false);
        }
    }, [onStatus]);

    useEffect(() => { refresh(); }, [refresh]);

    const test = async () => {
        setBusy('test');
        setResult(null);
        try {
            const res = await api.testGeminiKey(input.trim() || null);
            setResult(res.data?.valid
                ? { ok: true, text: res.data.message || 'The Gemini API accepted this key.' }
                : { ok: false, text: res.data?.error || 'The key was not accepted.' });
        } catch (err) {
            setResult({ ok: false, text: errorText(err, 'Could not test the key') });
        } finally {
            setBusy(null);
        }
    };

    const save = async () => {
        if (!input.trim()) return;
        setBusy('save');
        try {
            await api.setApiKey(input.trim());
            setInput('');
            setResult({ ok: true, text: 'Key saved.' });
            toast.success('Gemini API key saved');
            await refresh();
        } catch (err) {
            setResult({ ok: false, text: errorText(err, 'Could not save the key') });
        } finally {
            setBusy(null);
        }
    };

    const remove = async () => {
        if (!window.confirm('Remove the stored Gemini API key? Lines the agent CLI refuses will no longer be retried through the API.')) return;
        setBusy('remove');
        try {
            await api.deleteApiKey();
            setResult(null);
            toast.info('Gemini API key removed');
            await refresh();
        } catch (err) {
            toast.error(errorText(err, 'Could not remove the key'));
        } finally {
            setBusy(null);
        }
    };

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <label htmlFor="gemini-key" className={compact ? 'sr-only' : 'text-sm font-medium text-gray-700 dark:text-gray-300'}>Gemini API key</label>
                {hasKey !== null && (
                    <StatusChip tone={hasKey ? 'success' : 'neutral'}>{hasKey ? 'Key stored' : 'No key'}</StatusChip>
                )}
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
                <input
                    id="gemini-key"
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    value={input}
                    onChange={(e) => { setInput(e.target.value); setResult(null); }}
                    placeholder={hasKey ? 'Stored. Type a new key to replace it.' : 'AIza...'}
                    className={`${INPUT} font-mono sm:flex-1`}
                />
                <div className="flex flex-wrap gap-2">
                    <Button onClick={test} loading={busy === 'test'} disabled={!input.trim() && !hasKey}>Test</Button>
                    {input.trim() && <Button variant="primary" icon={KeyRound} onClick={save} loading={busy === 'save'}>Save key</Button>}
                    {hasKey && !input.trim() && <Button variant="danger" onClick={remove} loading={busy === 'remove'}>Remove</Button>}
                </div>
            </div>
            <TestResult result={result} />
            {!compact && (
                <a
                    href="https://aistudio.google.com/app/apikey"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-xs font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                >
                    Get a key from Google AI Studio <ExternalLink size={12} />
                </a>
            )}
        </div>
    );
}
