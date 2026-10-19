import React, { useState } from 'react';
import { CheckCircle2, ShieldAlert, Wrench } from 'lucide-react';
import { api } from '../../api';
import { Button } from '../ui';
import { errorText } from '../../hooks/useTranslate';

// VisualGuard & formatting: project-wide post-translation clean-up, run on every translated episode.
const QualityTools = ({ projectName, episodes = [], onReload, toast }) => {
    const [running, setRunning] = useState(null);
    const [progress, setProgress] = useState('');
    const [report, setReport] = useState(null);
    const translated = episodes.filter(ep => ep.translated);

    const runOnEach = async (kind, work, summarise) => {
        setRunning(kind);
        setReport(null);
        let done = 0;
        let total = 0;
        try {
            for (const ep of translated) {
                setProgress(`${done + 1} / ${translated.length}`);
                total += await work(ep);
                done += 1;
            }
            const message = summarise(total, done);
            setReport(message);
            toast?.success?.(message);
            onReload?.(true);
        } catch (err) {
            toast?.error?.(`Stopped after ${done} of ${translated.length} episodes: ${errorText(err)}`);
        } finally {
            setRunning(null);
            setProgress('');
        }
    };

    const scriptGuard = () => runOnEach('script',
        async (ep) => (await api.fixScript(projectName, ep.name)).data?.repaired_count || 0,
        (n, eps) => `ScriptGuard repaired ${n} characters in ${eps} episodes.`);

    const subtitleEdit = () => runOnEach('fixes',
        async (ep) => {
            await api.applyEpisodeFixes(projectName, ep.name, { target_track: 'target' });
            return 1;
        },
        (n) => `SubtitleEdit fixes and line splits applied to ${n} episodes.`);

    const disabled = !!running || translated.length === 0;
    return (
        <div className="space-y-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="space-y-2 rounded-lg border border-gray-200 p-3 dark:border-gray-700">
                    <div className="text-sm font-medium text-gray-900 dark:text-white">ScriptGuard</div>
                    <p className="text-xs text-gray-500 dark:text-gray-400">Finds Latin or Cyrillic look-alike letters inside target-language words and puts the right letters back.</p>
                    <Button size="sm" icon={ShieldAlert} loading={running === 'script'} disabled={disabled} onClick={scriptGuard}>
                        {running === 'script' ? `Checking ${progress}` : 'Check all episodes'}
                    </Button>
                </div>
                <div className="space-y-2 rounded-lg border border-gray-200 p-3 dark:border-gray-700">
                    <div className="text-sm font-medium text-gray-900 dark:text-white">SubtitleEdit fixes</div>
                    <p className="text-xs text-gray-500 dark:text-gray-400">Runs “Fix common errors” and splits long lines in every translated episode.</p>
                    <Button size="sm" icon={Wrench} loading={running === 'fixes'} disabled={disabled} onClick={subtitleEdit}>
                        {running === 'fixes' ? `Applying ${progress}` : 'Apply to all episodes'}
                    </Button>
                </div>
            </div>
            {translated.length === 0 && <p className="text-xs text-gray-500 dark:text-gray-400">Available once an episode has a translation.</p>}
            {report && (
                <div className="flex items-center gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300">
                    <CheckCircle2 size={14} className="shrink-0" /> {report}
                </div>
            )}
        </div>
    );
};

export default QualityTools;
