import React, { useState, useEffect, useMemo } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import EditorView from './EditorView';
import { Languages, Loader2, RotateCcw, Sparkles, Trash2, Wand2, Clock } from 'lucide-react';
import { useJobs } from '../context/JobContext';
import { useToast } from '../context/ToastContext';
import TermHarvestModal from './TermHarvestModal';
import { Button, ConfirmDialog, Notice, OverflowMenu, PageHeader, ProgressBar, StatusChip } from './ui';
import { errorText, useEpisodeQueue, useTranslate, useTranslationEngine } from '../hooks/useTranslate';

const LANG_MAP = {
    "Greek": "el", "English": "en", "Spanish": "es", "French": "fr",
    "German": "de", "Italian": "it", "Portuguese": "pt", "Russian": "ru",
    "Japanese": "ja", "Korean": "ko", "Chinese": "zh"
};
const getLangCode = (langName) => LANG_MAP[langName] || "en";

const isTranslatableLine = (item) => item.translatable !== false && item._ass?.translatable !== false;

const EpisodeView = () => {
    const { projectName, episodeName } = useParams();
    const navigate = useNavigate();
    const { addJob, activeJobs, isLive } = useJobs();
    const toast = useToast();
    const [data, setData] = useState([]);
    const [project, setProject] = useState(null);
    const [glossary, setGlossary] = useState({ terms: [] });
    const [loading, setLoading] = useState(true);
    const [processing, setProcessing] = useState(false);
    const [originalFilename, setOriginalFilename] = useState(null);
    const [episodeMetadata, setEpisodeMetadata] = useState(null);
    const [dirty, setDirty] = useState(false);
    const [harvestModalOpen, setHarvestModalOpen] = useState(false);
    const [confirm, setConfirm] = useState(null); // 'retranslate' | 'clear'
    const dirtyRef = React.useRef(false);
    useEffect(() => { dirtyRef.current = dirty; }, [dirty]);
    const handledJobsRef = React.useRef(new Set());
    // ETag from the last successful GET (docs/PLAN_structural_polish.md, Part A)
    // — the 3s live-view poll below sends it back as If-None-Match so an
    // unchanged episode short-circuits to a 304 instead of re-transferring the
    // whole (potentially thousands-of-lines) payload.
    const etagRef = React.useRef(null);

    const [selectedLang, setSelectedLang] = useState('');

    const primaryLang = project?.target_language || "English";
    const extraLangs = project?.target_languages || [];
    const targetLanguages = Array.from(new Set([primaryLang, ...extraLangs])).filter(Boolean);

    useEffect(() => {
        if (primaryLang) {
            setSelectedLang(primaryLang);
        }
    }, [primaryLang]);

    const activeLangCode = getLangCode(selectedLang || primaryLang);
    const primaryLangCode = getLangCode(primaryLang);

    const editorData = useMemo(() => {
        return (data || []).map(item => {
            const resolvedText = item.translations?.[activeLangCode] || (activeLangCode === primaryLangCode ? item.translated : '') || '';
            if (item.translated === resolvedText) {
                return item;
            }
            return {
                ...item,
                translated: resolvedText
            };
        });
    }, [data, activeLangCode, primaryLangCode]);

    const handleEditorDataUpdate = (updatedEditorData) => {
        const newData = (data || []).map((item, idx) => {
            const updatedItem = (updatedEditorData && updatedEditorData[idx]) || {};
            const translations = { ...(item.translations || {}) };
            translations[activeLangCode] = updatedItem.translated ?? '';

            const mapped = {
                ...item,
                translations,
                is_edited: updatedItem.is_edited,
                update_tm: updatedItem.update_tm,
                needs_review: updatedItem.needs_review,
                review_issues: updatedItem.review_issues,
                review_scores: updatedItem.review_scores,
                previous_translated: updatedItem.previous_translated !== undefined ? updatedItem.previous_translated : item.previous_translated,
            };

            if (activeLangCode === primaryLangCode) {
                mapped.translated = updatedItem.translated ?? '';
            }
            return mapped;
        });
        setData(newData);
        setDirty(true);
    };

    // Guard page unload when unsaved changes exist
    useEffect(() => {
        const handleBeforeUnload = (e) => {
            if (dirty) {
                e.preventDefault();
                e.returnValue = '';
            }
        };
        window.addEventListener('beforeunload', handleBeforeUnload);
        return () => window.removeEventListener('beforeunload', handleBeforeUnload);
    }, [dirty]);

    const handleSwitchEpisode = (targetEp) => {
        if (!targetEp || targetEp === episodeName) return;
        if (dirty && !window.confirm("You have unsaved changes in this episode. Discard changes and switch episodes?")) {
            return;
        }
        navigate(`/project/${encodeURIComponent(projectName)}/episode/${encodeURIComponent(targetEp)}`);
    };

    useEffect(() => {
        etagRef.current = null; // switching episodes — the old ETag doesn't apply
        loadData();
    }, [projectName, episodeName]);

    // Watch for completed translation jobs (model engine)
    const completedJob = useMemo(() => {
        const jobs = Object.values(activeJobs);
        return jobs.find(job =>
            job.status === 'completed' &&
            ['translate_episode', 'batch_translate', 'auto_translate', 'pipeline', 'retranslate_compare', 'retranslate_invalidated'].includes(job.type) &&
            (job.metadata?.episodeName === episodeName || job.metadata?.projectId === projectName)
        );
    }, [activeJobs, episodeName, projectName]);

    useEffect(() => {
        if (completedJob && !handledJobsRef.current.has(completedJob.id)) {
            if (!dirtyRef.current) {
                etagRef.current = null;
                loadData();
            } else {
                toast.info("Translation finished. Save your changes to load the new translation.");
            }
            handledJobsRef.current.add(completedJob.id);
        }
    }, [completedJob]);

    // Live View Polling: reload progressively while the server lists work on THIS episode (a job or a
    // running queue row whose project and episode both match).
    const isTranslating = isLive(projectName, episodeName);

    useEffect(() => {
        let interval;
        if (isTranslating) {
            // Load once immediately to catch any scenes finished before effect triggered
            // (skipped while the user has unsaved edits so polling can't clobber them)
            if (!dirtyRef.current) loadData(true);
            interval = setInterval(() => {
                if (!dirtyRef.current) loadData(true); // Silent reload
            }, 3000);
        }

        return () => {
            if (interval) clearInterval(interval);
        };
    }, [isTranslating, projectName, episodeName]);

    // Live update on agent task submission or disk sync completion
    useEffect(() => {
        const handleRefresh = (e) => {
            const detail = e?.detail || {};
            if (
                (!detail.project_name || detail.project_name.toLowerCase() === projectName.toLowerCase()) &&
                (!detail.episode_name || detail.episode_name.toLowerCase() === episodeName.toLowerCase())
            ) {
                if (!dirtyRef.current) loadData(true);
            }
        };
        window.addEventListener('omnisub:agent-submitted', handleRefresh);
        window.addEventListener('omnisub:sync-completed', handleRefresh);
        return () => {
            window.removeEventListener('omnisub:agent-submitted', handleRefresh);
            window.removeEventListener('omnisub:sync-completed', handleRefresh);
        };
    }, [projectName, episodeName]);

    const loadData = async (silent = false) => {
        if (!episodeName || !projectName) {
            console.error("Missing projectName or episodeName");
            if (!silent) setLoading(false);
            return;
        }

        try {
            if (!silent) setLoading(true);
            const [epRes, projRes] = await Promise.all([
                api.getEpisode(projectName, episodeName, etagRef.current),
                api.getProject(projectName)
            ]);
            setProject(projRes.data);
            setGlossary(projRes.data.glossary || { terms: [] });
            if (epRes.status === 304) {
                // Unchanged since the last fetch — project data above may still
                // have moved, but the (expensive) episode payload didn't.
                setDirty(false);
            } else {
                etagRef.current = epRes.headers?.etag || null;
                const epData = Array.isArray(epRes.data?.data)
                    ? epRes.data.data
                    : (Array.isArray(epRes.data) ? epRes.data : []);
                setData(epData);
                setOriginalFilename(epRes.data?.metadata?.original_filename || null);
                setEpisodeMetadata(epRes.data?.metadata || null);
                setDirty(false);
            }
        } catch (err) {
            console.error("Failed to load episode data", err);
        } finally {
            if (!silent) setLoading(false);
        }
    };

    // The runner / worker state of this episode, and the one translate action shared with the project page.
    const engine = useTranslationEngine();
    const { translate, busy: queueing, notice, clearNotice } = useTranslate({ projectName, engine, toast, addJob });
    const { active: queue, refresh: refreshQueue } = useEpisodeQueue(projectName, () => {
        if (!dirtyRef.current) {
            etagRef.current = null;
            loadData(true);
        }
    });
    const queueEntry = queue.get(episodeName);

    const handleUpdateTermsInScenes = async (selectedTermsArray) => {
        try {
            const res = await api.fixConsistency(projectName, selectedTermsArray);
            if (res.data?.job_id) {
                addJob(res.data.job_id, 'retranslate_invalidated', `Updating scenes with specific glossary terms...`, {
                    projectId: projectName,
                    episodeName: episodeName,
                    link: `/project/${encodeURIComponent(projectName)}`
                });
                toast.info(`Updating scenes with selected terms...`);
            }
        } catch (err) {
            console.error('Failed to update scenes', err);
            toast.error(errorText(err, 'Could not update the scenes.'));
        }
    };

    const startTranslate = async (retranslate) => {
        const result = await translate([episodeName], { retranslate });
        if (result) refreshQueue();
    };

    const handleApplyFixes = async (track) => {
        const isSource = track === 'source';
        const label = isSource ? 'source' : `${selectedLang} translation`;
        const msg = isSource
            ? `Apply SubtitleEdit "Fix common errors" + "Split long lines" to the SOURCE subtitles?\n\n`
            + `This may change the number of lines (long lines get split into new cues) and the app re-reads `
            + `the result. Translations of unchanged lines are kept; lines created by a split are flagged for review.\n\n`
            + `Best run BEFORE translating. SubtitleEdit must be configured in Settings.`
            : `Apply SubtitleEdit "Fix common errors" + "Split long lines" to the ${selectedLang} TRANSLATION?\n\n`
            + `This may change the number of lines and the app re-reads the result, then re-exports the subtitle file.\n\n`
            + `Best run AFTER translation as a final polish. SubtitleEdit must be configured in Settings.`;
        if (!window.confirm(msg)) return;
        if (dirty && !window.confirm('You have unsaved edits that will be discarded by re-reading the fixed file. Continue?')) return;

        setProcessing(true);
        try {
            const res = await api.applyCommonFixes(projectName, episodeName, track, isSource ? null : selectedLang);
            const s = res.data?.stats || {};
            const delta = (s.after ?? 0) - (s.before ?? 0);
            const deltaMsg = delta === 0
                ? 'line count unchanged'
                : delta > 0 ? `+${delta} line${delta !== 1 ? 's' : ''}` : `${delta} line${delta !== -1 ? 's' : ''}`;
            toast.success(`Applied SubtitleEdit fixes to ${label}: ${s.before ?? '?'} → ${s.after ?? '?'} (${deltaMsg})`);
            if (res.data?.etag) {
                etagRef.current = res.data.etag;
            } else {
                etagRef.current = null;
            }
            await loadData();
        } catch (err) {
            console.error('Failed to apply SubtitleEdit fixes', err);
            toast.error(errorText(err, 'Could not apply the SubtitleEdit fixes.'));
        } finally {
            setProcessing(false);
        }
    };

    const handleClear = async (deleteDiskFile) => {
        setConfirm(null);
        try {
            await api.deleteEpisodeTranslation(projectName, episodeName, deleteDiskFile);
            toast.success('Translation deleted.');
            loadData();
        } catch (err) {
            console.error('Failed to clear translation', err);
            toast.error(errorText(err, 'Could not delete the translation.'));
        }
    };

    // One line again through the runner's agent (10-30 s). The server stores the line and re-exports the
    // file; only that line is merged into what is on screen, so unsaved edits elsewhere stay untouched.
    const handleRetranslateLine = async (idx) => {
        try {
            const res = await api.retranslateLine(projectName, episodeName, idx, selectedLang && selectedLang !== primaryLang ? selectedLang : null);
            const text = res.data?.translated ?? '';
            if (res.data?.etag) etagRef.current = res.data.etag;
            setData(prev => prev.map((item, i) => {
                if (i !== idx) return item;
                const next = { ...item, translations: { ...(item.translations || {}), [activeLangCode]: text } };
                if (activeLangCode === primaryLangCode) {
                    next.translated = text;
                    next.previous_translated = res.data?.previous_translated ?? item.previous_translated;
                }
                return next;
            }));
            toast.success(`Line ${idx + 1} translated again. Revert brings the old text back.`);
        } catch (err) {
            console.error('Failed to retranslate line', err);
            toast.error(errorText(err, 'The line could not be translated again.'));
        }
    };

    const handleSave = async (updatedData = null) => {
        const payload = updatedData || data;
        setProcessing(true);
        try {
            const res = await api.saveEpisode(projectName, episodeName, payload);
            if (res.data?.etag) {
                etagRef.current = res.data.etag;
            }
            const exportedPath = res.data?.exported_path;
            if (exportedPath) {
                const filename = exportedPath.split(/[\\/]/).pop();
                toast.success(`Saved & exported to media folder (${filename})`);
            } else {
                toast.success('Subtitle changes saved successfully!');
            }
            setDirty(false);
            loadData(true);
        } catch (err) {
            console.error('Failed to save episode data', err);
            toast.error(errorText(err, 'Could not save the subtitle changes.'));
        } finally {
            setProcessing(false);
        }
    };

    const handleUpdateGlossary = async (newGlossary, globalSave = false) => {
        try {
            await api.updateProject(projectName, { glossary: newGlossary }, globalSave);
            // Reload project data to reflect resolved inherited settings
            const projRes = await api.getProject(projectName);
            setProject(projRes.data);
            setGlossary(projRes.data.glossary || { terms: [] });
            toast.success('Glossary saved successfully!');
        } catch (err) {
            console.error('Failed to save glossary', err);
            toast.error(errorText(err, 'Could not save the glossary.'));
        }
    };

    const handleDeleteLines = async (indexes) => {
        setProcessing(true);
        try {
            const res = await api.deleteLines(projectName, episodeName, indexes);
            const n = res.data?.deleted ?? indexes.length;
            if (res.data?.exported_path) {
                toast.success(`Deleted ${n} line${n !== 1 ? 's' : ''} — exported subtitle updated`);
            } else {
                toast.success(`Deleted ${n} line${n !== 1 ? 's' : ''}`);
            }
            await loadData(true);
        } catch (err) {
            console.error('Failed to delete lines', err);
            toast.error(errorText(err, 'Could not delete the lines.'));
        } finally {
            setProcessing(false);
        }
    };

    const handleExportToPath = async () => {
        setProcessing(true);
        try {
            const res = await api.exportToPath(projectName, episodeName);
            const filename = (res.data?.path || '').split(/[\\/]/).pop();
            toast.success(filename ? `Exported ${filename} next to media file` : 'Exported next to media file');
        } catch (err) {
            console.error('Failed to export to media path', err);
            toast.error(errorText(err, 'Could not export next to the video.'));
        } finally {
            setProcessing(false);
        }
    };

    const activeJob = Object.values(activeJobs).find(job =>
        job.status === 'running' &&
        job.type === 'translate_episode' &&
        job.metadata?.episodeName === episodeName
    );
    const running = !!activeJob || queueEntry?.phase === 'running';
    const runProgress = activeJob ? activeJob.progress : queueEntry?.progress;

    if (loading) return <div className="flex h-[60vh] items-center justify-center gap-2 text-sm text-gray-500"><Loader2 size={18} className="animate-spin text-indigo-600" /> Loading {episodeName}…</div>;

    // SubtitleEdit fixes are SRT-only; disable them for ASS/SSA episodes (the backend
    // also refuses them, but hide the affordance so the intent is clear).
    const isAssFormat = ['ass', 'ssa'].includes((episodeMetadata?.original_format || episodeMetadata?.original_extension || '').toLowerCase());
    const hasTranslation = editorData.some(d => d.translated);
    const missingLines = editorData.filter(d => isTranslatableLine(d) && !d.translated).length;
    const complete = hasTranslation && missingLines === 0;
    const busy = processing || running || queueing;
    const subtitle = [
        `${data.length} lines`,
        hasTranslation ? (missingLines === 0 ? 'fully translated' : `${missingLines} not translated yet`) : 'not translated',
    ].join(' · ');
    const fixesTitle = isAssFormat ? "SubtitleEdit fixes aren't available for ASS/SSA subtitles (they would corrupt styling and karaoke)." : undefined;

    // Fills the viewport under the TopBar (h-14 plus its 1px border); dvh keeps mobile browser bars out.
    return (
        <div className="flex h-[calc(100dvh-3.5rem-1px)] min-h-[32rem] flex-col bg-slate-50 dark:bg-gray-900">
            <div className="shrink-0 border-b border-gray-200 bg-white px-4 pb-1 pt-4 dark:border-gray-750 dark:bg-gray-850 sm:px-6 [&>header]:mb-3">
                <PageHeader
                    breadcrumb={[
                        { label: 'Library', to: '/' },
                        { label: project?.name || projectName, to: `/project/${encodeURIComponent(projectName)}` },
                        { label: episodeName },
                    ]}
                    title={episodeName}
                    subtitle={subtitle}
                    actions={(
                        <>
                            {queueEntry && !activeJob ? (
                                <StatusChip tone={queueEntry.phase === 'queued' ? 'info' : 'accent'} icon={queueEntry.phase === 'queued' ? Clock : Loader2}
                                    className={`px-3 py-1.5 text-sm ${queueEntry.phase === 'queued' ? '' : '[&>svg]:animate-spin'}`}>
                                    {queueEntry.phase === 'running' ? 'Translating…' : queueEntry.phase === 'extracting' ? 'Extracting the subtitle…' : 'Queued'}
                                </StatusChip>
                            ) : complete ? (
                                <Button icon={RotateCcw} disabled={busy} onClick={() => setConfirm('retranslate')}>Retranslate</Button>
                            ) : (
                                <Button variant="primary" icon={Languages} disabled={busy} loading={queueing} onClick={() => startTranslate(false)}
                                    title={hasTranslation ? 'Translate the lines that are still missing' : undefined}>
                                    Translate
                                </Button>
                            )}
                            <OverflowMenu items={[
                                { label: 'Fix source with SubtitleEdit', icon: Wand2, disabled: busy || isAssFormat, onClick: () => handleApplyFixes('source') },
                                { label: 'Fix translation with SubtitleEdit', icon: Wand2, hidden: !hasTranslation, disabled: busy || isAssFormat, onClick: () => handleApplyFixes('target') },
                                { label: 'Find terms in this episode', icon: Sparkles, disabled: busy, onClick: () => setHarvestModalOpen(true) },
                                { label: 'Delete translation', icon: Trash2, danger: true, hidden: !hasTranslation, disabled: busy, onClick: () => setConfirm('clear') },
                            ]} />
                        </>
                    )}
                >
                    {(project?.episodes?.length > 1 || targetLanguages.length > 1 || fixesTitle) && (
                        <div className="flex flex-wrap items-center gap-3 text-sm text-gray-600 dark:text-gray-300">
                            {project?.episodes?.length > 1 && (
                                <label className="flex items-center gap-2">
                                    Episode
                                    <select value={episodeName} onChange={(e) => handleSwitchEpisode(e.target.value)}
                                        className="rounded-lg border border-gray-250 bg-white px-2 py-1 text-sm dark:border-gray-700 dark:bg-gray-900">
                                        {project.episodes.map(ep => {
                                            const epN = typeof ep === 'string' ? ep : ep.name;
                                            return <option key={epN} value={epN}>{epN}</option>;
                                        })}
                                    </select>
                                </label>
                            )}
                            {targetLanguages.length > 1 && (
                                <label className="flex items-center gap-2">
                                    Language
                                    <select value={selectedLang} onChange={(e) => setSelectedLang(e.target.value)}
                                        className="rounded-lg border border-gray-250 bg-white px-2 py-1 text-sm dark:border-gray-700 dark:bg-gray-900">
                                        {targetLanguages.map(lang => <option key={lang} value={lang}>{lang}</option>)}
                                    </select>
                                </label>
                            )}
                            {fixesTitle && <span className="text-xs text-gray-500">ASS/SSA subtitle: SubtitleEdit fixes are off.</span>}
                        </div>
                    )}
                </PageHeader>

                {(running || notice) && (
                    <div className="mb-3 space-y-2">
                        {running && (
                            <div className="space-y-1" role="status">
                                <div className="flex justify-between text-xs text-gray-600 dark:text-gray-300">
                                    <span>{activeJob?.message || 'Translating this episode…'}</span>
                                    {runProgress != null && <span className="tabular-nums">{Math.round(runProgress)}%</span>}
                                </div>
                                <ProgressBar value={runProgress ?? 0} />
                            </div>
                        )}
                        {notice && (
                            <Notice tone={notice.tone} title={notice.text}
                                actions={(
                                    <>
                                        {notice.activity && <Link to="/activity" className="text-sm font-medium underline">Open Activity</Link>}
                                        <button type="button" onClick={clearNotice} className="text-sm underline opacity-80 hover:opacity-100">Dismiss</button>
                                    </>
                                )} />
                        )}
                    </div>
                )}
            </div>

            {/* Editor */}
            <div className="min-h-0 flex-1 overflow-hidden">
                <EditorView
                    data={editorData}
                    glossary={glossary}
                    project={project}
                    projectName={projectName}
                    episodeName={episodeName}
                    activeLanguage={selectedLang || primaryLang}
                    originalFilename={originalFilename}
                    onDataUpdate={handleEditorDataUpdate}
                    onSave={handleSave}
                    onUpdateGlossary={handleUpdateGlossary}
                    onDeleteLines={handleDeleteLines}
                    onExportToPath={handleExportToPath}
                    onRetranslateLine={handleRetranslateLine}
                    hasMediaPath={!!(episodeMetadata?.arr_media_path || episodeMetadata?.bazarr_media_path)}
                    dirty={dirty}
                    isLoading={running || processing}
                    onUpdateTermsInScenes={handleUpdateTermsInScenes}
                />
            </div>

            <ConfirmDialog
                open={confirm === 'retranslate'} title={`Retranslate ${episodeName}?`} confirmLabel="Retranslate"
                onCancel={() => setConfirm(null)}
                onConfirm={() => { setConfirm(null); startTranslate(true); }}
            >
                <p>The whole episode is translated again and replaces the current translation, including lines you edited by hand.</p>
                {dirty && <p className="text-amber-700 dark:text-amber-300">You have unsaved edits; they will be replaced too.</p>}
            </ConfirmDialog>
            <ConfirmDialog
                open={confirm === 'clear'} danger title={`Delete the translation of ${episodeName}?`} confirmLabel="Delete translation"
                checkbox={{ label: 'Also delete the translated subtitle file next to the video' }}
                onCancel={() => setConfirm(null)}
                onConfirm={handleClear}
            >
                <p>The source subtitle stays, so the episode can be translated again.</p>
            </ConfirmDialog>

            <TermHarvestModal
                isOpen={harvestModalOpen}
                onClose={() => setHarvestModalOpen(false)}
                projectName={projectName}
                parentProjectName={project?.parent_project}
                episodeName={episodeName}
                onTermsAdded={async (newTerms, directToUniverse) => {
                    if (!directToUniverse) {
                        const current = glossary?.terms || [];
                        const merged = [...newTerms, ...current];
                        await api.updateProject(projectName, { glossary: { terms: merged } });
                        loadData();
                    }
                }}
            />
        </div>
    );
};

export default EpisodeView;
