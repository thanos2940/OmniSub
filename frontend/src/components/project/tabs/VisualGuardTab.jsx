import React, { useState } from 'react';
import { ShieldCheck, ShieldAlert, Wrench, Search, Film, Loader2, CheckCircle2 } from 'lucide-react';
import { api } from '../../../api';

const VisualGuardTab = ({
    projectName,
    episodes = [],
    onReload,
    toast,
}) => {
    const [runningAction, setRunningAction] = useState(null);
    const [actionReport, setActionReport] = useState(null);

    const handleBatchScriptFix = async () => {
        setRunningAction('script_fix');
        setActionReport(null);
        try {
            let fixedTotal = 0;
            for (const ep of episodes) {
                if (ep.translated) {
                    const res = await api.fixScript(projectName, ep.name);
                    fixedTotal += (res.data?.repaired_count || 0);
                }
            }
            setActionReport({ action: 'ScriptGuard Repair', count: fixedTotal, message: `Repaired mixed-alphabet characters across translated episodes.` });
            if (toast?.success) toast.success(`ScriptGuard repaired ${fixedTotal} characters.`);
            if (onReload) onReload();
        } catch (err) {
            console.error("Batch script fix failed", err);
            if (toast?.error) toast.error("ScriptGuard batch repair encountered errors.");
        } finally {
            setRunningAction(null);
        }
    };

    const handleBatchSubtitleFixes = async () => {
        setRunningAction('subtitle_edit');
        setActionReport(null);
        try {
            let processed = 0;
            for (const ep of episodes) {
                if (ep.translated) {
                    await api.applyEpisodeFixes(projectName, ep.name, {
                        target_track: 'target',
                        fix_common_errors: true,
                        split_long_lines: true,
                    });
                    processed++;
                }
            }
            setActionReport({ action: 'SubtitleEdit Fixes', count: processed, message: `Applied "Fix Common Errors" & Line Splits to ${processed} episodes.` });
            if (toast?.success) toast.success(`SubtitleEdit fixes applied to ${processed} episodes.`);
            if (onReload) onReload();
        } catch (err) {
            console.error("SubtitleEdit fixes failed", err);
            if (toast?.error) toast.error("SubtitleEdit batch fixes failed.");
        } finally {
            setRunningAction(null);
        }
    };

    return (
        <div className="space-y-6">
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-5">
                <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-4">
                    <ShieldCheck className="text-purple-600 dark:text-purple-400" size={22} />
                    <div>
                        <h3 className="text-base font-bold text-gray-900 dark:text-white">VisualGuard &amp; Formatting Integrity Suite</h3>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                            Automated post-translation quality tools to fix mixed-alphabet character corruptions and formatting violations.
                        </p>
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {/* ScriptGuard Homoglyph Repair */}
                    <div className="p-5 bg-purple-50/50 dark:bg-purple-950/20 border border-purple-100 dark:border-purple-900/50 rounded-2xl space-y-3 flex flex-col justify-between">
                        <div className="space-y-1.5">
                            <div className="flex items-center gap-2">
                                <ShieldAlert className="text-purple-600 dark:text-purple-400" size={18} />
                                <h4 className="font-bold text-sm text-gray-900 dark:text-white">ScriptGuard Homoglyph Repair</h4>
                            </div>
                            <p className="text-xs text-gray-600 dark:text-gray-400 leading-relaxed">
                                Scans all translated subtitles for corrupt Latin or Cyrillic characters erroneously inserted into Greek/target words, and replaces them with pure target glyphs.
                            </p>
                        </div>

                        <button
                            type="button"
                            onClick={handleBatchScriptFix}
                            disabled={!!runningAction}
                            className="w-full py-2.5 px-4 bg-purple-600 hover:bg-purple-700 text-white rounded-xl text-xs font-bold flex items-center justify-center gap-2 shadow-sm transition-all disabled:opacity-50"
                        >
                            {runningAction === 'script_fix' ? <Loader2 size={14} className="animate-spin" /> : <ShieldAlert size={14} />}
                            <span>{runningAction === 'script_fix' ? 'Scanning & Repairing...' : 'Run Project-Wide ScriptGuard'}</span>
                        </button>
                    </div>

                    {/* SubtitleEdit Automated Fixes */}
                    <div className="p-5 bg-indigo-50/50 dark:bg-indigo-950/20 border border-indigo-100 dark:border-indigo-900/50 rounded-2xl space-y-3 flex flex-col justify-between">
                        <div className="space-y-1.5">
                            <div className="flex items-center gap-2">
                                <Wrench className="text-indigo-600 dark:text-indigo-400" size={18} />
                                <h4 className="font-bold text-sm text-gray-900 dark:text-white">SubtitleEdit Batch Fixes &amp; Splits</h4>
                            </div>
                            <p className="text-xs text-gray-600 dark:text-gray-400 leading-relaxed">
                                Runs SubtitleEdit CLI's "Fix Common Errors" and splits long lines (&gt;42 chars) across all translated episodes in this project.
                            </p>
                        </div>

                        <button
                            type="button"
                            onClick={handleBatchSubtitleFixes}
                            disabled={!!runningAction}
                            className="w-full py-2.5 px-4 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold flex items-center justify-center gap-2 shadow-sm transition-all disabled:opacity-50"
                        >
                            {runningAction === 'subtitle_edit' ? <Loader2 size={14} className="animate-spin" /> : <Wrench size={14} />}
                            <span>{runningAction === 'subtitle_edit' ? 'Applying Fixes...' : 'Apply SubtitleEdit Fixes'}</span>
                        </button>
                    </div>
                </div>

                {actionReport && (
                    <div className="p-4 bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-900 text-emerald-800 dark:text-emerald-300 rounded-xl text-xs flex items-center gap-2">
                        <CheckCircle2 size={16} className="shrink-0" />
                        <div>
                            <strong>{actionReport.action}:</strong> {actionReport.message}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};

export default VisualGuardTab;
