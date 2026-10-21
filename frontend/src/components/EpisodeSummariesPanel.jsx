import { useState, useEffect, useRef, useCallback } from 'react';
import { Film, Sparkles, Edit2, Trash2, Check, RefreshCw, Loader2 } from 'lucide-react';
import { api } from '../api';
import { useToast } from '../context/ToastContext';
import { useJobs } from '../context/JobContext';
import { Button, Card, EmptyState, INPUT } from './ui';
import { errorText } from '../hooks/useTranslate';

// Episode summaries of one project. `embedded` renders the body only (the caller provides the card).
const EpisodeSummariesPanel = ({ projectName, projectSettings, embedded = false }) => {
    const [summaries, setSummaries] = useState({});
    const [loading, setLoading] = useState(true);
    const [generatingEp, setGeneratingEp] = useState(null);
    const [generatingJobId, setGeneratingJobId] = useState(null);
    const [editingEp, setEditingEp] = useState(null);
    const [editContent, setEditContent] = useState('');
    const [adding, setAdding] = useState(false);
    const [newEpisode, setNewEpisode] = useState('');
    const [episodeNames, setEpisodeNames] = useState([]);

    const toast = useToast();
    const { activeJobs, addJob } = useJobs();
    const isMounted = useRef(true);

    useEffect(() => {
        isMounted.current = true;
        return () => {
            isMounted.current = false;
        };
    }, []);

    const loadSummaries = useCallback(async () => {
        if (!isMounted.current) return;
        setLoading(true);
        try {
            const res = await api.getSummaries(projectName);
            if (isMounted.current) {
                setSummaries(res.data || {});
            }
        } catch (err) {
            console.error("Failed to load summaries", err);
        } finally {
            if (isMounted.current) {
                setLoading(false);
            }
        }
    }, [projectName]);

    useEffect(() => {
        loadSummaries();
    }, [loadSummaries]);

    // Track active summary generation job through JobContext
    useEffect(() => {
        if (!generatingJobId) return;
        const job = activeJobs[generatingJobId];
        if (!job) return;

        if (job.status === 'completed') {
            if (isMounted.current) {
                toast.success(`Summary for ${generatingEp || 'episode'} ready.`);
                loadSummaries();
                setGeneratingJobId(null);
                setGeneratingEp(null);
            }
        } else if (job.status === 'failed' || job.status === 'cancelled') {
            if (isMounted.current) {
                toast.error(`Summary generation failed: ${job.message || 'Error'}`);
                setGeneratingJobId(null);
                setGeneratingEp(null);
            }
        }
    }, [activeJobs, generatingJobId, generatingEp, loadSummaries, toast]);

    const openAdding = async () => {
        setAdding(true);
        if (episodeNames.length) return;
        try {
            const res = await api.getEpisodes(projectName);
            if (isMounted.current) setEpisodeNames((res.data || []).map(e => e.name));
        } catch {
            // The field still accepts a typed name.
        }
    };

    const handleGenerate = async (episodeName) => {
        const epToGenerate = (episodeName || '').trim();
        if (!epToGenerate) return;

        setGeneratingEp(epToGenerate);
        try {
            const model = projectSettings?.context_model || null;
            const res = await api.generateSummary(projectName, epToGenerate, model);
            const jobId = res.data?.job_id;
            toast.success(`Writing the summary for ${epToGenerate}…`);
            setAdding(false);
            setNewEpisode('');
            if (jobId) {
                addJob(jobId, 'episode_summary', `Generating summary for ${epToGenerate} (${projectName})`, {
                    projectId: projectName,
                    episodeName: epToGenerate,
                    link: `/project/${encodeURIComponent(projectName)}`
                });
                setGeneratingJobId(jobId);
            } else {
                setTimeout(() => {
                    if (isMounted.current) {
                        loadSummaries();
                        setGeneratingEp(null);
                    }
                }, 2000);
            }
        } catch (err) {
            console.error("Failed to generate summary", err);
            if (isMounted.current) {
                toast.error(errorText(err, 'Could not generate the summary.'));
                setGeneratingEp(null);
            }
        }
    };

    const handleSave = async (episodeName) => {
        if (!editContent.trim()) return toast.error("Summary cannot be empty.");
        try {
            await api.updateSummary(projectName, episodeName, editContent);
            toast.success(`Summary for ${episodeName} saved.`);
            setEditingEp(null);
            loadSummaries();
        } catch (err) {
            console.error("Failed to save summary", err);
            toast.error(errorText(err, 'Could not save the summary.'));
        }
    };

    const handleDelete = async (episodeName) => {
        if (!window.confirm(`Delete summary for ${episodeName}?`)) return;
        try {
            await api.deleteSummary(projectName, episodeName);
            toast.success("Summary deleted.");
            loadSummaries();
        } catch (err) {
            console.error("Failed to delete summary", err);
            toast.error(errorText(err, 'Could not delete the summary.'));
        }
    };

    const openEdit = (episodeName, content) => {
        setEditingEp(episodeName);
        setEditContent(content || '');
    };

    const epsList = Object.keys(summaries).sort();

    const addForm = adding && (
        <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); handleGenerate(newEpisode); }}>
            <input list={`summary-episodes-${projectName}`} value={newEpisode} onChange={(e) => setNewEpisode(e.target.value)}
                placeholder="Episode, e.g. S01E01" aria-label="Episode to summarise" className={`${INPUT} w-56`} autoFocus />
            <datalist id={`summary-episodes-${projectName}`}>
                {episodeNames.map(n => <option key={n} value={n} />)}
            </datalist>
            <Button type="submit" size="sm" variant="primary" icon={Sparkles} disabled={!newEpisode.trim() || !!generatingEp}>Write summary</Button>
            <Button size="sm" variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
        </form>
    );

    const toolbar = (
        <div className="flex flex-wrap items-center gap-2">
            {!adding && <Button size="sm" icon={Sparkles} onClick={openAdding} disabled={!!generatingEp}>New summary</Button>}
            <Button size="sm" variant="ghost" icon={RefreshCw} onClick={loadSummaries} aria-label="Refresh summaries" title="Refresh summaries" />
        </div>
    );

    const body = loading ? (
        <div className="flex items-center justify-center gap-2 py-8 text-sm text-gray-500"><Loader2 size={16} className="animate-spin" /> Loading summaries…</div>
    ) : (
        <div className="space-y-3">
            {addForm}
            {epsList.length === 0 ? (
                <EmptyState icon={Film} title="No summaries yet"
                    description="They are written automatically after each translated episode, so the next one knows what happened." />
            ) : (
                <div className="max-h-[600px] divide-y divide-gray-100 overflow-y-auto rounded-lg border border-gray-100 dark:divide-gray-750 dark:border-gray-750">
                    {epsList.map(epName => (
                        <div key={epName} className="p-4">
                            <div className="mb-1 flex items-start justify-between gap-2">
                                <span className="rounded bg-indigo-50 px-2 py-0.5 text-xs font-semibold uppercase text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-300">{epName}</span>
                                <div className="flex items-center gap-1">
                                    {editingEp !== epName && (
                                        <Button size="sm" variant="ghost" icon={Edit2} onClick={() => openEdit(epName, summaries[epName])} aria-label={`Edit summary of ${epName}`} title="Edit" />
                                    )}
                                    <Button size="sm" variant="ghost" icon={Sparkles} loading={generatingEp === epName} onClick={() => handleGenerate(epName)} aria-label={`Rewrite summary of ${epName}`} title="Write it again" />
                                    <Button size="sm" variant="ghost" icon={Trash2} onClick={() => handleDelete(epName)} aria-label={`Delete summary of ${epName}`} title="Delete" />
                                </div>
                            </div>
                            {editingEp === epName ? (
                                <div className="mt-2 space-y-2">
                                    <textarea value={editContent} onChange={(e) => setEditContent(e.target.value)} placeholder="Write the summary here…"
                                        className={`${INPUT} min-h-[120px] resize-y leading-relaxed`} />
                                    <div className="flex justify-end gap-2">
                                        <Button size="sm" onClick={() => setEditingEp(null)}>Cancel</Button>
                                        <Button size="sm" variant="primary" icon={Check} onClick={() => handleSave(epName)}>Save</Button>
                                    </div>
                                </div>
                            ) : (
                                <p className="whitespace-pre-wrap text-sm leading-relaxed text-gray-600 dark:text-gray-400">{summaries[epName]}</p>
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );

    if (embedded) {
        return (
            <div className="space-y-3">
                <div className="flex justify-end">{toolbar}</div>
                {body}
            </div>
        );
    }
    return (
        <Card title="Episode summaries" subtitle={`${epsList.length} written`} actions={toolbar}>
            {body}
        </Card>
    );
};

export default EpisodeSummariesPanel;
