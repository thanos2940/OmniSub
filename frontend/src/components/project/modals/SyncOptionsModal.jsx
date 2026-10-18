import React, { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, RefreshCw, CheckCircle2, Loader2, FileWarning, Languages, Bot, Wrench } from 'lucide-react';
import { api } from '../../../api';
import { brokenGroups, repairSummary } from '../../../utils/translationAudit';

const SyncOptionsModal = ({
    isOpen,
    onClose,
    projectName,
    onSuccess,
    toast,
}) => {
    const [syncing, setSyncing] = useState(false);
    const [extractAss, setExtractAss] = useState(true);
    const [scanMissing, setScanMissing] = useState(true);
    const [scanDiskSubs, setScanDiskSubs] = useState(true);
    const [reQueueRemoved, setReQueueRemoved] = useState(true);

    const [autoRenameMatchingMedia, setAutoRenameMatchingMedia] = useState(true);
    const [renaming, setRenaming] = useState(false);
    // Subtitles named after an outdated video file (dry run on open).
    const [nameCheck, setNameCheck] = useState(null);
    // Episodes whose stored translation still has English lines / untranslated signs.
    const [audit, setAudit] = useState(null);
    const [auditing, setAuditing] = useState(false);
    const [repairing, setRepairing] = useState(false);
    // A repair changed the project's data. The page behind the dialog is refreshed when the dialog closes:
    // refreshing right away would reload the whole page and reset the dialog, hiding what is left to fix.
    const [repaired, setRepaired] = useState(false);

    const checkNames = useCallback(async () => {
        try {
            const res = await api.renameProjectSubtitles(projectName, false);
            setNameCheck(res.data || null);
        } catch {
            setNameCheck(null);
        }
    }, [projectName]);

    useEffect(() => {
        if (isOpen) {
            setAudit(null);
            setRepaired(false);
            checkNames();
        }
    }, [isOpen, checkNames]);

    if (!isOpen) return null;

    const handleClose = () => {
        if (repaired && onSuccess) onSuccess();
        onClose();
    };

    const handleAudit = async (requeue = false) => {
        setAuditing(true);
        try {
            const res = await api.auditProjectTranslations(projectName, requeue);
            setAudit(res.data);
            if (requeue && toast?.success) {
                // Queuing repairs first, so say what it changed along with what it queued.
                const changes = repairSummary(res.data);
                toast.success((res.data?.enqueue?.message || 'Queued incomplete episodes for agent translation.') + (changes ? ` ${changes}.` : ''));
                if (onSuccess) onSuccess();
            }
        } catch (err) {
            if (toast?.error) toast.error(err.response?.data?.message || 'Translation check failed.');
        } finally {
            setAuditing(false);
        }
    };

    // Fixes garbled encoding and leftover prompt text in place (nothing is queued), then checks again so the
    // list shows what is left (lines with mixed alphabets, lines the repair cleared).
    const handleRepair = async () => {
        setRepairing(true);
        try {
            const res = await api.auditProjectTranslations(projectName, false, { repair: true });
            const summary = repairSummary(res.data);
            if (toast?.success) toast.success(summary ? `Repair done. ${summary}.` : 'Nothing needed repairing.');
            let next = res.data;
            try {
                next = (await api.auditProjectTranslations(projectName, false)).data;
            } catch {
                // keep the repair response; "Check again" refreshes the list
            }
            setAudit(next);
            setRepaired(true);
        } catch (err) {
            if (toast?.error) toast.error(err.response?.data?.message || err.response?.data?.detail || 'Repair failed.');
        } finally {
            setRepairing(false);
        }
    };

    // Episodes with lines that carry another line's translation (by the audit), with their line counts.
    const misplacedList = (eps) => eps.slice(0, 8).map((ep) => {
        const n = audit?.episodes?.find((e) => e.episode_name === ep)?.misplaced || 0;
        return `${ep} (${n})`;
    }).join(', ') + (eps.length > 8 ? '…' : '');
    const wrongEps = audit?.misplaced || [];
    const importedEps = audit?.misplaced_imported || [];
    // Episodes with broken text, split into what a repair can fix (garbled encoding, leftover prompt text)
    // and lines with mixed alphabets, which only a retranslation fixes. Without per-kind detail an episode
    // counts as repairable.
    const brokenRows = (audit?.broken || []).map((ep) => ({
        ep,
        ...brokenGroups(audit?.episodes?.find((e) => e.episode_name === ep)?.broken),
    }));
    const fixableEps = brokenRows.filter((r) => r.fixable > 0 || r.total === 0);
    const mixedEps = brokenRows.filter((r) => r.mixed > 0);
    const brokenList = (rows, pick) => rows.slice(0, 8).map((r) => (pick(r) > 0 ? `${r.ep} (${pick(r)})` : r.ep)).join(', ') + (rows.length > 8 ? '…' : '');
    const brokenCount = Math.max(audit?.broken_count || 0, brokenRows.length);
    const auditActionable = !!audit && (audit.incomplete_count > 0 || wrongEps.length > 0 || brokenCount > 0);

    const handleSync = async () => {
        setSyncing(true);
        try {
            await api.syncProject(projectName, {
                extract_ass: extractAss,
                scan_missing: scanMissing,
            });
            if (scanDiskSubs) {
                const diskRes = await api.syncProjectDisk(projectName, {
                    re_queue_removed: reQueueRemoved,
                    import_cues: true,
                    auto_add_new_episodes: scanMissing,
                    auto_rename_matching_media: autoRenameMatchingMedia,
                });
                const d = diskRes.data || {};
                if (toast?.success) {
                    toast.success(
                        `Project synced! Disk scan: ${d.translations_found || 0} translations found, ` +
                        `${d.translations_removed || 0} removed, ${d.queue_requeued || 0} re-queued.` +
                        (d.subtitles_renamed ? ` Renamed ${d.subtitles_renamed} subtitle(s) to match video.` : '')
                    );
                }
            } else {
                if (toast?.success) toast.success(`Project synchronized with media server!`);
            }
            if (onSuccess) onSuccess();
            onClose();
        } catch (err) {
            console.error("Sync failed", err);
            // If arr sync failed, attempt direct disk sync
            if (scanDiskSubs) {
                try {
                    const diskRes = await api.syncProjectDisk(projectName, {
                        re_queue_removed: reQueueRemoved,
                        import_cues: true,
                        auto_add_new_episodes: scanMissing,
                        auto_rename_matching_media: autoRenameMatchingMedia,
                    });
                    const d = diskRes.data || {};
                    if (toast?.success) {
                        toast.success(
                            `Direct disk sync complete: ${d.translations_found || 0} found, ` +
                            `${d.translations_removed || 0} removed, ${d.queue_requeued || 0} re-queued.` +
                            (d.subtitles_renamed ? ` Renamed ${d.subtitles_renamed} subtitle(s).` : '')
                        );
                    }
                    if (onSuccess) onSuccess();
                    onClose();
                    return;
                } catch {
                    // fall through to error
                }
            }
            if (toast?.error) toast.error("Failed to sync project.");
        } finally {
            setSyncing(false);
        }
    };

    const handleDiskSyncOnly = async () => {
        setSyncing(true);
        try {
            const diskRes = await api.syncProjectDisk(projectName, {
                re_queue_removed: reQueueRemoved,
                import_cues: true,
                auto_add_new_episodes: scanMissing,
                auto_rename_matching_media: autoRenameMatchingMedia,
            });
            const d = diskRes.data || {};
            if (toast?.success) {
                toast.success(
                    `Disk sync complete: ${d.translations_found || 0} translations found, ` +
                    `${d.translations_removed || 0} removed, ${d.queue_requeued || 0} re-queued.` +
                    (d.subtitles_renamed ? ` Renamed ${d.subtitles_renamed} subtitle(s) to match video.` : '')
                );
            }
            if (onSuccess) onSuccess();
            onClose();
        } catch (err) {
            console.error("Disk sync failed", err);
            if (toast?.error) toast.error("Failed to sync disk subtitles.");
        } finally {
            setSyncing(false);
        }
    };

    const handleRenameSubtitlesOnly = async () => {
        setRenaming(true);
        try {
            const res = await api.renameProjectSubtitles(projectName, true);
            const data = res.data || {};
            if (toast?.success) {
                toast.success(`Renamed ${data.renamed_count || 0} subtitle(s) to match their video file.` +
                    (data.conflicts?.length ? ` ${data.conflicts.length} left alone (a file with the new name already exists).` : ''));
            }
            await checkNames();
            if (onSuccess) onSuccess();
        } catch (err) {
            console.error("Rename subtitles failed", err);
            if (toast?.error) toast.error(err.response?.data?.detail || "Failed to rename subtitles.");
        } finally {
            setRenaming(false);
        }
    };

    return (
        <AnimatePresence>
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
                <motion.div
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-lg max-h-[90vh] flex flex-col overflow-hidden border border-gray-200 dark:border-gray-700"
                >
                    <div className="shrink-0 p-6 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/50">
                        <div className="flex items-center gap-2">
                            <RefreshCw className="text-indigo-600 dark:text-indigo-400" size={20} />
                            <h3 className="text-lg font-bold text-gray-900 dark:text-white">
                                Sync Project & Disk Subtitles
                            </h3>
                        </div>
                        <button onClick={handleClose} className="p-1 text-gray-400 hover:text-gray-600 rounded-lg">
                            <X size={20} />
                        </button>
                    </div>

                    <div className="p-6 space-y-4 overflow-y-auto min-h-0">
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                            Scan the filesystem and media server for subtitle files (.el.ass, .el.srt, .en.ass), detect new translations or deletions, rename subtitles to match Plex video stems, and synchronize the queue.
                        </p>

                        {nameCheck && (nameCheck.mismatched_count > 0 || nameCheck.conflicts?.length > 0) && (
                            <div className="p-3 rounded-xl border border-amber-200 dark:border-amber-900/50 bg-amber-50/60 dark:bg-amber-950/20 text-xs">
                                <div className="flex items-center justify-between gap-2">
                                    <span className="font-bold text-amber-800 dark:text-amber-300 flex items-center gap-1.5">
                                        <FileWarning size={14} />
                                        {nameCheck.mismatched_count} subtitle(s) don&rsquo;t match their video file
                                    </span>
                                    {nameCheck.mismatched_count > 0 && (
                                        <button
                                            type="button"
                                            onClick={handleRenameSubtitlesOnly}
                                            disabled={syncing || renaming}
                                            className="px-2.5 py-1 font-bold text-white bg-amber-600 hover:bg-amber-700 rounded-lg disabled:opacity-50"
                                        >
                                            {renaming ? 'Renaming…' : 'Rename to match'}
                                        </button>
                                    )}
                                </div>
                                <p className="text-[11px] text-amber-700/80 dark:text-amber-400/80 mt-1">
                                    Plex only shows subtitles named after the current video (e.g. after an upgrade from AAC to Opus).
                                </p>
                                <ul className="mt-1.5 max-h-24 overflow-y-auto space-y-0.5 font-mono text-[10px] text-amber-900/80 dark:text-amber-200/70">
                                    {(nameCheck.mismatched || []).slice(0, 20).map((m) => (
                                        <li key={m.old_name} className="truncate" title={`${m.old_name} → ${m.new_name}`}>{m.old_name} → {m.new_name}</li>
                                    ))}
                                    {(nameCheck.conflicts || []).map((c) => (
                                        <li key={c} className="truncate text-red-600 dark:text-red-400" title={c}>{c} (a file with the new name already exists)</li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        <div className="p-3 rounded-xl border border-purple-100 dark:border-purple-900/40 bg-purple-50/30 dark:bg-purple-950/10 text-xs">
                            <div className="flex items-center justify-between gap-2">
                                <span className="font-bold text-gray-900 dark:text-white flex items-center gap-1.5">
                                    <Languages size={14} className="text-purple-500" /> Untranslated lines
                                </span>
                                <button
                                    type="button"
                                    onClick={() => handleAudit(false)}
                                    disabled={auditing || repairing}
                                    className="px-2.5 py-1 font-bold text-purple-700 dark:text-purple-300 border border-purple-200 dark:border-purple-800 rounded-lg hover:bg-purple-50 dark:hover:bg-purple-900/30 disabled:opacity-50 flex items-center gap-1"
                                >
                                    {auditing && <Loader2 size={12} className="animate-spin" />}
                                    {audit ? 'Check again' : 'Check episodes'}
                                </button>
                            </div>
                            <p className="text-[11px] text-gray-400 mt-0.5">
                                Finds translated episodes that still contain English dialogue or untranslated signs and info cards,
                                lines that carry the translation of another line, and garbled or leftover prompt text.
                            </p>
                            {audit && (
                                <div className="mt-2">
                                    {!auditActionable && importedEps.length === 0 ? (
                                        <span className="text-emerald-600 dark:text-emerald-400 font-semibold">Every translated episode is complete.</span>
                                    ) : (
                                        <div className="flex items-start justify-between gap-2">
                                            <div className="space-y-1 text-gray-700 dark:text-gray-300">
                                                {audit.incomplete_count > 0 && (
                                                    <div>
                                                        <strong>{audit.incomplete_count}</strong> episode(s) incomplete:{' '}
                                                        <span className="font-mono text-[10px]">{audit.incomplete.slice(0, 8).join(', ')}{audit.incomplete.length > 8 ? '…' : ''}</span>
                                                    </div>
                                                )}
                                                {wrongEps.length > 0 && (
                                                    <div title="Queuing clears these lines so they are translated again (the old text is kept in misplaced_backup.json next to the episode)">
                                                        <strong>{wrongEps.length}</strong> episode(s) with lines on the wrong line:{' '}
                                                        <span className="font-mono text-[10px]">{misplacedList(wrongEps)}</span>
                                                    </div>
                                                )}
                                                {fixableEps.length > 0 && (
                                                    <div title="Garbled encoding is repaired without losing anything. Leftover prompt text (numbered lines, echoed context, review notes or summaries) is cleaned up or cleared so it can be translated again.">
                                                        <strong>{fixableEps.length}</strong> episode(s) with garbled or leftover prompt text:{' '}
                                                        <span className="font-mono text-[10px]">{brokenList(fixableEps, (r) => r.fixable)}</span>
                                                    </div>
                                                )}
                                                {mixedEps.length > 0 && (
                                                    <div title="Foreign letters inside Greek words. Repair leaves these lines in place; queuing for the agent clears them so they are translated again.">
                                                        <strong>{mixedEps.length}</strong> episode(s) with mixed alphabets (need retranslation):{' '}
                                                        <span className="font-mono text-[10px]">{brokenList(mixedEps, (r) => r.mixed)}</span>
                                                    </div>
                                                )}
                                                {importedEps.length > 0 && (
                                                    <div className="text-gray-500 dark:text-gray-400" title="Imported subtitle files that do not line up with this source (another cut or timing). They are left as they are; retranslate them in full if needed.">
                                                        <strong>{importedEps.length}</strong> imported subtitle(s) do not match the source:{' '}
                                                        <span className="font-mono text-[10px]">{misplacedList(importedEps)}</span>
                                                    </div>
                                                )}
                                            </div>
                                            {auditActionable && (
                                                <div className="shrink-0 flex flex-col items-stretch gap-1.5">
                                                    {fixableEps.length > 0 && (
                                                        <button
                                                            type="button"
                                                            onClick={handleRepair}
                                                            disabled={auditing || repairing}
                                                            className="px-2.5 py-1 font-bold text-amber-700 dark:text-amber-300 border border-amber-300 dark:border-amber-800 rounded-lg hover:bg-amber-50 dark:hover:bg-amber-900/30 disabled:opacity-50 flex items-center justify-center gap-1"
                                                            title="Fix garbled encoding and clear leftover prompt text in place. Nothing is queued; lines with mixed alphabets are left as they are."
                                                        >
                                                            {repairing ? <Loader2 size={12} className="animate-spin" /> : <Wrench size={12} />} Repair
                                                        </button>
                                                    )}
                                                    <button
                                                        type="button"
                                                        onClick={() => handleAudit(true)}
                                                        disabled={auditing || repairing}
                                                        className="px-2.5 py-1 font-bold text-white bg-purple-600 hover:bg-purple-700 rounded-lg disabled:opacity-50 flex items-center justify-center gap-1"
                                                        title="Repair garbled text first, then queue these episodes so an AI agent translates only their missing, misplaced and broken lines (including mixed alphabets)"
                                                    >
                                                        <Bot size={12} /> Queue for agent
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>

                        <div className="space-y-3 pt-2">
                            <label className="flex items-start gap-3 p-3 rounded-xl border border-indigo-100 dark:border-indigo-900/40 bg-indigo-50/30 dark:bg-indigo-950/10 hover:bg-indigo-50/60 cursor-pointer transition-colors">
                                <input
                                    type="checkbox"
                                    checked={scanDiskSubs}
                                    onChange={(e) => setScanDiskSubs(e.target.checked)}
                                    className="mt-0.5 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                                />
                                <div>
                                    <span className="text-xs font-bold text-gray-900 dark:text-white">Scan Actual Files on Disk (.el.ass, .el.srt, .en.ass)</span>
                                    <p className="text-[11px] text-gray-400 mt-0.5">Detects existing target translations on disk, imports cues, and marks completed.</p>
                                </div>
                            </label>

                            {scanDiskSubs && (
                                <div className="space-y-2 ml-4">
                                    <label className="flex items-start gap-3 p-3 rounded-xl border border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                                        <input
                                            type="checkbox"
                                            checked={autoRenameMatchingMedia}
                                            onChange={(e) => setAutoRenameMatchingMedia(e.target.checked)}
                                            className="mt-0.5 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                                        />
                                        <div>
                                            <span className="text-xs font-bold text-gray-900 dark:text-white">Rename Subtitles to Match Video (Plex/Jellyfin)</span>
                                            <p className="text-[11px] text-gray-400 mt-0.5">Renames subtitles still named after an older release of the video. Never overwrites an existing file.</p>
                                        </div>
                                    </label>

                                    <label className="flex items-start gap-3 p-3 rounded-xl border border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                                        <input
                                            type="checkbox"
                                            checked={reQueueRemoved}
                                            onChange={(e) => setReQueueRemoved(e.target.checked)}
                                            className="mt-0.5 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                                        />
                                        <div>
                                            <span className="text-xs font-bold text-gray-900 dark:text-white">Re-queue Deleted Translations</span>
                                            <p className="text-[11px] text-gray-400 mt-0.5">If a subtitle was deleted from a reachable media folder, mark it untranslated and re-queue it.</p>
                                        </div>
                                    </label>
                                </div>
                            )}

                            <label className="flex items-start gap-3 p-3 rounded-xl border border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                                <input
                                    type="checkbox"
                                    checked={extractAss}
                                    onChange={(e) => setExtractAss(e.target.checked)}
                                    className="mt-0.5 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                                />
                                <div>
                                    <span className="text-xs font-bold text-gray-900 dark:text-white">Auto-Probe Embedded ASS Subtitles</span>
                                    <p className="text-[11px] text-gray-400 mt-0.5">Extracts styled ASS tracks if video contains embedded subtitles.</p>
                                </div>
                            </label>

                            <label className="flex items-start gap-3 p-3 rounded-xl border border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                                <input
                                    type="checkbox"
                                    checked={scanMissing}
                                    onChange={(e) => setScanMissing(e.target.checked)}
                                    className="mt-0.5 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                                />
                                <div>
                                    <span className="text-xs font-bold text-gray-900 dark:text-white">Scan for New Episodes on Disk</span>
                                    <p className="text-[11px] text-gray-400 mt-0.5">Imports any new season episodes added since last synchronization.</p>
                                </div>
                            </label>
                        </div>
                    </div>

                    <div className="shrink-0 p-6 border-t border-gray-100 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                            <button
                                type="button"
                                onClick={handleRenameSubtitlesOnly}
                                disabled={syncing || renaming}
                                className="px-3.5 py-2 text-xs font-bold text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-950/20 border border-amber-200 dark:border-amber-900/40 rounded-xl transition-all disabled:opacity-50"
                                title="Rename subtitle files to match the exact filename stem of their video files (Plex match)"
                            >
                                {renaming ? 'Renaming...' : 'Rename Subs to Video'}
                            </button>
                            <button
                                type="button"
                                onClick={handleDiskSyncOnly}
                                disabled={syncing || renaming}
                                className="px-3.5 py-2 text-xs font-bold text-emerald-600 dark:text-emerald-400 hover:bg-emerald-50 dark:hover:bg-emerald-950/20 border border-emerald-200 dark:border-emerald-900/40 rounded-xl transition-all disabled:opacity-50"
                                title="Only scan physical disk files without contacting Sonarr/Radarr"
                            >
                                Sync Disk Only
                            </button>
                        </div>
                        <div className="flex items-center gap-2">
                            <button
                                type="button"
                                onClick={handleClose}
                                className="px-4 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 rounded-xl"
                            >
                                Cancel
                            </button>
                            <button
                                type="button"
                                onClick={handleSync}
                                disabled={syncing || renaming}
                                className="px-6 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-sm font-bold flex items-center gap-2 shadow-md transition-all disabled:opacity-50"
                            >
                                {syncing ? <Loader2 size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}
                                <span>{syncing ? 'Syncing...' : 'Start Full Sync'}</span>
                            </button>
                        </div>
                    </div>
                </motion.div>
            </div>
        </AnimatePresence>
    );
};

export default SyncOptionsModal;
