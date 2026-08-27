import React, { useState } from 'react';
import { FolderOpen, CheckCircle2, XCircle, Loader2 } from 'lucide-react';
import { api } from '../../api';

// Only shown when the previous step actually connected to Sonarr or Radarr
// (SetupWizard filters this step out otherwise — see arrConfiguredThisRun).
const StepPathCheck = ({ onNext, onBack, onSkip }) => {
    const [samplePath, setSamplePath] = useState('');
    const [testing, setTesting] = useState(false);
    const [result, setResult] = useState(null);
    const [mappings, setMappings] = useState([]);
    const [saving, setSaving] = useState(false);

    const runTest = async () => {
        if (!samplePath.trim()) return;
        setTesting(true);
        try {
            const res = await api.testPath(samplePath.trim(), mappings);
            setResult(res.data);
        } catch {
            setResult({ exists: false, error: true });
        } finally {
            setTesting(false);
        }
    };

    const addMapping = () => setMappings(m => [...m, { remote: '', local: '' }]);
    const updateMapping = (i, field, value) => setMappings(m => m.map((row, idx) => idx === i ? { ...row, [field]: value } : row));
    const removeMapping = (i) => setMappings(m => m.filter((_, idx) => idx !== i));

    const handleNext = async () => {
        setSaving(true);
        try {
            if (mappings.some(m => m.remote.trim() && m.local.trim())) {
                await api.updateSettings({ arr_path_mappings: mappings.filter(m => m.remote.trim() && m.local.trim()) });
            }
        } catch (e) {
            console.error('Failed to save path mappings', e);
        } finally {
            setSaving(false);
            onNext();
        }
    };

    return (
        <div className="space-y-5">
            <div>
                <h2 className="text-lg font-bold text-gray-900 dark:text-white">Check your media paths</h2>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                    Sonarr/Radarr often see a different path than this machine does (e.g. Docker's
                    <code className="mx-1 px-1 bg-gray-100 dark:bg-gray-700 rounded text-xs">/tv/Show/...</code>
                    vs. <code className="mx-1 px-1 bg-gray-100 dark:bg-gray-700 rounded text-xs">D:\Media\...</code> here).
                    Paste one path Sonarr/Radarr reports to check it resolves.
                </p>
            </div>

            <div className="flex gap-2">
                <div className="relative flex-1">
                    <FolderOpen className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 w-4 h-4" />
                    <input
                        type="text"
                        value={samplePath}
                        onChange={(e) => { setSamplePath(e.target.value); setResult(null); }}
                        placeholder="/tv/My Show/Season 01"
                        className="w-full pl-10 pr-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none text-sm font-mono"
                    />
                </div>
                <button onClick={runTest} disabled={!samplePath.trim() || testing} className="px-4 py-2 rounded-lg bg-gray-100 dark:bg-gray-700 text-sm font-medium flex items-center gap-1.5 disabled:opacity-50">
                    {testing && <Loader2 size={14} className="animate-spin" />}
                    Test
                </button>
            </div>

            {result && (
                <div className={`flex items-start gap-2 text-sm rounded-lg p-3 ${result.exists ? 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400' : 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400'}`}>
                    {result.exists ? <CheckCircle2 size={16} className="flex-shrink-0 mt-0.5" /> : <XCircle size={16} className="flex-shrink-0 mt-0.5" />}
                    <div>
                        <p>{result.exists ? 'Resolved successfully.' : "Couldn't find that path from this server."}</p>
                        {result.resolved_path && <code className="text-xs block mt-1 opacity-80">{result.resolved_path}</code>}
                    </div>
                </div>
            )}

            {result && !result.exists && (
                <div className="space-y-2">
                    <p className="text-sm font-medium text-gray-700 dark:text-gray-300">Add a path mapping and try again:</p>
                    {mappings.map((m, i) => (
                        <div key={i} className="flex gap-2 items-center">
                            <input type="text" value={m.remote} onChange={(e) => updateMapping(i, 'remote', e.target.value)} placeholder="Remote prefix (e.g. /tv)" className="flex-1 px-2 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none font-mono" />
                            <input type="text" value={m.local} onChange={(e) => updateMapping(i, 'local', e.target.value)} placeholder="Local prefix (e.g. D:\Media\TV)" className="flex-1 px-2 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none font-mono" />
                            <button onClick={() => removeMapping(i)} className="text-gray-400 hover:text-red-500 text-xs px-1">✕</button>
                        </div>
                    ))}
                    <div className="flex gap-2">
                        <button onClick={addMapping} className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline">+ Add mapping</button>
                        {mappings.length > 0 && <button onClick={runTest} className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline">Re-test</button>}
                    </div>
                </div>
            )}

            <div className="flex items-center justify-between pt-2">
                <button onClick={onBack} disabled={!onBack} className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 disabled:opacity-0">Back</button>
                <div className="flex gap-3">
                    <button onClick={onSkip} className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">Skip</button>
                    <button onClick={handleNext} disabled={saving} className="px-5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-medium disabled:opacity-50">
                        {saving ? 'Saving...' : 'Continue'}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default StepPathCheck;
