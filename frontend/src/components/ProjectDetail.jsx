import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { AlertCircle, EyeOff, Languages, Loader2, PauseCircle, RefreshCw, Settings as SettingsIcon } from 'lucide-react';
import { useToast } from '../context/ToastContext';
import { useJobs } from '../context/JobContext';
import { useProjectDetail } from '../hooks/useProjectDetail';
import { useEpisodeSelection } from '../hooks/useEpisodeSelection';
import { errorText, useEpisodeQueue, useTranslate, useTranslationEngine } from '../hooks/useTranslate';
import { api } from '../api';
import { Button, EmptyState, IconButton, Notice, Page, PageHeader, Tabs } from './ui';

import ProjectSettingsModal from './ProjectSettingsModal';
import ApplyFixesModal from './project/modals/ApplyFixesModal';
import CheckMissingModal from './project/modals/CheckMissingModal';
import ScriptFixModal from './project/modals/ScriptFixModal';
import EmbeddedTracksModal from './project/modals/EmbeddedTracksModal';
import SyncOptionsModal from './project/modals/SyncOptionsModal';
import ConfirmDialog from './project/ConfirmDialog';
import PriorityControl from './project/PriorityControl';

import EpisodesTab from './project/tabs/EpisodesTab';
import ContextTab from './project/tabs/ContextTab';
import GlossaryTab from './project/tabs/GlossaryTab';
import HistoryTab from './project/tabs/HistoryTab';

const TAB_IDS = ['episodes', 'glossary', 'context', 'history'];

const typeLabel = (project) => {
    if (project.type === 'parent') return 'Universe';
    if (project.arr_media_type === 'movie') return 'Movie';
    if (project.arr_media_type === 'series') return 'Series';
    return 'Project';
};

const ProjectDetail = () => {
    const { projectName, name } = useParams();
    const effectiveProjectName = projectName || name;
    const toast = useToast();
    const { addJob, activeJobs } = useJobs();
    const [searchParams, setSearchParams] = useSearchParams();
    const requestedTab = searchParams.get('tab');
    const activeTab = TAB_IDS.includes(requestedTab) ? requestedTab : 'episodes';

    const [settingsOpen, setSettingsOpen] = useState(false);
    const [syncOpen, setSyncOpen] = useState(false);
    const [fixesModal, setFixesModal] = useState({ open: false, episodeName: null });
    const [checkMissingModal, setCheckMissingModal] = useState({ open: false, episodeName: null });
    const [scriptFixModal, setScriptFixModal] = useState({ open: false, episodeName: null });
    const [tracksModal, setTracksModal] = useState({ open: false, episodeName: null });
    const [deleteTarget, setDeleteTarget] = useState(null); // episode names
    const [deleting, setDeleting] = useState(false);
    const [changingPriority, setChangingPriority] = useState(false);
    const [runner, setRunner] = useState(null);

    const {
        project, episodes, subprojects, tokenSummary, loading, error, loadData,
        handleSaveSettings, handleUpload, handleDeleteEpisode, handleBatchExtractEmbedded, handleBatchDelete, handleAiAction,
    } = useProjectDetail({ projectName: effectiveProjectName, addJob, activeJobs, toast });

    const engine = useTranslationEngine();
    const selection = useEpisodeSelection(episodes);
    const { translate, translateMissing, busy, notice, clearNotice } = useTranslate({ projectName: effectiveProjectName, engine, toast, addJob });
    const reload = useCallback(() => loadData(true), [loadData]);
    const { active: queue, refresh: refreshQueue } = useEpisodeQueue(effectiveProjectName, reload);

    // Refresh when an agent submits a translation or a disk sync finishes.
    useEffect(() => {
        const handleRefresh = (e) => {
            const detail = e?.detail || {};
            if (!detail.project_name || detail.project_name.toLowerCase() === effectiveProjectName.toLowerCase()) loadData(true);
        };
        window.addEventListener('omnisub:agent-submitted', handleRefresh);
        window.addEventListener('omnisub:sync-completed', handleRefresh);
        return () => {
            window.removeEventListener('omnisub:agent-submitted', handleRefresh);
            window.removeEventListener('omnisub:sync-completed', handleRefresh);
        };
    }, [effectiveProjectName, loadData]);

    // A paused runner leaves queued episodes waiting; say so next to the queue.
    const queuedCount = queue.size;
    useEffect(() => {
        if (engine !== 'agent' || queuedCount === 0) { setRunner(null); return; }
        api.getAgentRunner().then(res => setRunner(res.data)).catch(() => setRunner(null));
    }, [engine, queuedCount]);

    const setTab = (id) => {
        const next = new URLSearchParams(searchParams);
        if (id === 'episodes') next.delete('tab'); else next.set('tab', id);
        setSearchParams(next, { replace: true });
    };

    const stats = useMemo(() => {
        const total = episodes.length;
        const translated = episodes.filter(e => e.translated).length;
        return { total, translated, percent: total ? Math.round((translated / total) * 100) : 0 };
    }, [episodes]);

    const currentPriority = project?.translation_priority || (project?.arr_disabled ? 'ignored' : 'normal');

    const handlePriorityChange = async (priority) => {
        if (!project || priority === currentPriority || changingPriority) return;
        setChangingPriority(true);
        try {
            await api.updateProjectPriority(effectiveProjectName, priority);
            toast.success(`Priority set to ${priority}.`);
            await loadData(true);
        } catch (err) {
            toast.error(errorText(err, 'Could not change the priority.'));
        } finally {
            setChangingPriority(false);
        }
    };

    const afterQueue = async (result) => {
        if (result) {
            await refreshQueue();
            loadData(true);
        }
    };
    const handleTranslate = async (names, options) => afterQueue(await translate(names, options));
    const handleTranslateMissing = async () => afterQueue(await translateMissing());

    const handleAction = (kind, ep) => {
        if (kind === 'extract') handleBatchExtractEmbedded([ep.name]);
        else if (kind === 'tracks') setTracksModal({ open: true, episodeName: ep.name });
        else if (kind === 'fixes') setFixesModal({ open: true, episodeName: ep.name });
        else if (kind === 'missing') setCheckMissingModal({ open: true, episodeName: ep.name });
        else if (kind === 'script') setScriptFixModal({ open: true, episodeName: ep.name });
        else if (kind === 'delete') setDeleteTarget([ep.name]);
    };

    const handleBulk = async (kind) => {
        const names = [...selection.selectedEpisodes];
        if (!names.length) return;
        if (kind === 'translate') {
            await handleTranslate(names);
            selection.clearSelection();
        } else if (kind === 'extract') {
            await handleBatchExtractEmbedded(names);
        } else if (kind === 'delete') {
            setDeleteTarget(names);
        }
    };

    const confirmDelete = async (alsoDisk) => {
        setDeleting(true);
        try {
            if (deleteTarget.length === 1) await handleDeleteEpisode(deleteTarget[0], alsoDisk);
            else if (await handleBatchDelete(deleteTarget, alsoDisk)) selection.clearSelection();
        } finally {
            setDeleting(false);
            setDeleteTarget(null);
        }
    };

    if (loading) {
        return (
            <Page>
                <div className="flex min-h-[320px] flex-col items-center justify-center gap-3 text-sm text-gray-500 dark:text-gray-400">
                    <Loader2 className="animate-spin text-indigo-600" size={28} /> Loading {effectiveProjectName}…
                </div>
            </Page>
        );
    }

    if (error || !project) {
        return (
            <Page width="narrow">
                <EmptyState icon={AlertCircle} title="Title not found" description={error || 'It may have been removed, or the server could not be reached.'}
                    action={<Link to="/" className="text-sm font-medium text-indigo-600 hover:underline dark:text-indigo-400">Back to the library</Link>} />
            </Page>
        );
    }

    const isParent = project.type === 'parent';
    const ignored = currentPriority === 'ignored';
    const runnerPaused = engine === 'agent' && queuedCount > 0 && (runner?.paused || runner?.status === 'paused');
    const subtitle = [
        typeLabel(project),
        project.target_language || 'Default language',
        isParent ? `${subprojects.length} shows` : `${stats.total} ${stats.total === 1 ? 'episode' : 'episodes'}`,
        !isParent && stats.total > 0 ? `${stats.percent}% translated` : null,
    ].filter(Boolean).join(' · ');

    const tabs = [
        { id: 'episodes', label: isParent ? 'Shows' : 'Episodes', count: isParent ? subprojects.length : stats.total },
        { id: 'glossary', label: 'Glossary', count: project.glossary?.terms?.length || undefined },
        { id: 'context', label: 'Context' },
        { id: 'history', label: 'History' },
    ];

    return (
        <Page>
            <PageHeader
                breadcrumb={[{ label: 'Library', to: '/' }, { label: project.name || effectiveProjectName }]}
                title={project.name || effectiveProjectName}
                subtitle={subtitle}
                actions={(
                    <>
                        <Button variant="primary" icon={Languages} loading={busy} disabled={isParent || stats.total === 0} onClick={handleTranslateMissing}
                            title="Queue every episode that has no translation yet">
                            Translate missing
                        </Button>
                        <Button icon={RefreshCw} onClick={() => setSyncOpen(true)} title="Check the disk and the media server for changes">Sync</Button>
                        <IconButton icon={SettingsIcon} label="Project settings" onClick={() => setSettingsOpen(true)} />
                    </>
                )}
            >
                <div className="flex flex-wrap items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
                    <span>Priority</span>
                    <PriorityControl value={currentPriority} onChange={handlePriorityChange} disabled={changingPriority} />
                </div>
            </PageHeader>

            <div className="mb-5 space-y-3 empty:hidden">
                {ignored && (
                    <Notice tone="danger" icon={EyeOff} title="This title is ignored"
                        actions={<Button size="sm" onClick={() => handlePriorityChange('normal')}>Stop ignoring</Button>}>
                        Nothing is translated automatically. Translating an episode yourself still works.
                    </Notice>
                )}
                {runnerPaused && (
                    <Notice tone="warning" icon={PauseCircle} title="The runner is paused"
                        actions={<Link to="/activity" className="text-sm font-medium underline">Open Activity</Link>}>
                        Queued episodes wait until you resume it.
                    </Notice>
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

            <Tabs tabs={tabs} value={activeTab} onChange={setTab} />

            {activeTab === 'episodes' && (
                <EpisodesTab
                    project={project} projectName={effectiveProjectName} episodes={episodes} subprojects={subprojects}
                    selection={{ ...selection, selected: selection.selectedEpisodes, toggleEpisode: selection.toggleEpisode }}
                    queue={queue} busy={busy}
                    onTranslate={handleTranslate} onAction={handleAction} onBulk={handleBulk} onUpload={handleUpload}
                />
            )}
            {activeTab === 'glossary' && (
                <GlossaryTab project={project} projectName={effectiveProjectName} engine={engine} onAiAction={handleAiAction} onReload={loadData} toast={toast} />
            )}
            {activeTab === 'context' && (
                <ContextTab project={project} projectName={effectiveProjectName} engine={engine} onAiAction={handleAiAction} onReload={loadData} toast={toast} />
            )}
            {activeTab === 'history' && <HistoryTab projectName={effectiveProjectName} />}

            <ProjectSettingsModal
                isOpen={settingsOpen} onClose={() => setSettingsOpen(false)}
                project={project} settings={project.settings} onSave={handleSaveSettings} tokenSummary={tokenSummary}
                episodes={episodes} onReload={reload} toast={toast}
            />
            <SyncOptionsModal isOpen={syncOpen} onClose={() => setSyncOpen(false)} projectName={effectiveProjectName} onSuccess={loadData} toast={toast} />
            <ApplyFixesModal
                isOpen={fixesModal.open} onClose={() => setFixesModal({ open: false, episodeName: null })}
                projectName={effectiveProjectName} episodeName={fixesModal.episodeName} isBatch={false}
                selectedEpisodes={[]} onSuccess={loadData} toast={toast}
            />
            <CheckMissingModal
                isOpen={checkMissingModal.open} onClose={() => setCheckMissingModal({ open: false, episodeName: null })}
                projectName={effectiveProjectName} episodeName={checkMissingModal.episodeName} onSuccess={loadData} toast={toast}
            />
            <ScriptFixModal
                isOpen={scriptFixModal.open} onClose={() => setScriptFixModal({ open: false, episodeName: null })}
                projectName={effectiveProjectName} episodeName={scriptFixModal.episodeName} onSuccess={loadData} toast={toast}
            />
            <EmbeddedTracksModal
                isOpen={tracksModal.open} onClose={() => setTracksModal({ open: false, episodeName: null })}
                projectName={effectiveProjectName} episodeName={tracksModal.episodeName} onSuccess={loadData} toast={toast}
            />

            <ConfirmDialog
                open={!!deleteTarget} busy={deleting} danger confirmLabel="Delete translation"
                title={deleteTarget?.length === 1 ? `Delete the translation of ${deleteTarget[0]}?` : `Delete the translation of ${deleteTarget?.length} episodes?`}
                checkbox={{ label: 'Also delete the translated subtitle file next to the video' }}
                onCancel={() => setDeleteTarget(null)} onConfirm={confirmDelete}
            >
                <p>The translation stored in OmbiSub is removed. The source subtitle stays, so the episode can be translated again.</p>
            </ConfirmDialog>
        </Page>
    );
};

export default ProjectDetail;
