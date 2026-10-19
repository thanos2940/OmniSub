import React, { useEffect, useState } from 'react';
import { Check, ChevronDown, ChevronRight, Compass, Copy, Edit3, Eye, Loader2, RotateCcw, Save, Sparkles } from 'lucide-react';
import { api } from '../../../api';
import { Button, Card, EmptyState, Notice } from '../../ui';
import EpisodeSummariesPanel from '../../EpisodeSummariesPanel';
import BootstrapNotice, { bootstrapCaption } from '../BootstrapNotice';
import { useProjectJob } from '../useProjectJob';
import { errorText } from '../../../hooks/useTranslate';

const JOB_TYPES = ['create_context', 'enhance_context'];

const ContextTab = ({ project, projectName, engine, onAiAction, onReload, toast }) => {
    const [content, setContent] = useState('');
    const [isEditing, setIsEditing] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [copied, setCopied] = useState(false);
    const [reviewing, setReviewing] = useState(false);
    const [summariesOpen, setSummariesOpen] = useState(false);

    useEffect(() => {
        if (!reviewing) setContent(project?.context_guide || '');
    }, [project?.context_guide, reviewing]);

    const running = useProjectJob(JOB_TYPES, projectName, {
        onCompleted: (job) => {
            // Model engine: the draft comes back for review. Agent engine: the guide is already saved.
            const draft = job.result?.context_guide || job.ai_response;
            if (draft) {
                setContent(draft);
                setIsEditing(true);
                setReviewing(true);
                toast?.info?.('The new context guide is in the editor. Check it, then save.');
            } else {
                toast?.success?.('Context guide updated.');
                onReload?.(true);
            }
        },
        onFailed: (job) => toast?.error?.(job.message || 'Context guide generation failed.'),
    });

    const agent = engine !== 'model';
    const model = agent ? null : (project?.settings?.context_model || null);
    const hasGuide = !!(project?.context_guide || '').trim();
    const startBuild = () => onAiAction('create_context', model);
    const startEnhance = () => onAiAction('enhance_context', model);

    const handleSave = async () => {
        setIsSaving(true);
        try {
            await api.updateProject(projectName, { context_guide: content });
            toast?.success?.('Context guide saved.');
            setReviewing(false);
            setIsEditing(false);
            onReload?.(true);
        } catch (err) {
            toast?.error?.(errorText(err, 'Could not save the context guide.'));
        } finally {
            setIsSaving(false);
        }
    };

    const handleRevert = () => {
        setContent(project?.context_guide || '');
        setReviewing(false);
        setIsEditing(false);
    };

    const handleCopy = () => {
        if (!content) return;
        navigator.clipboard?.writeText(content);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const words = content.trim() ? content.trim().split(/\s+/).length : 0;
    const busy = !!running;
    const buildLabel = agent ? 'Build with agy' : 'Generate with AI';
    const enhanceLabel = agent ? 'Enhance with agy' : 'Enhance with AI';

    const actions = (
        <>
            {hasGuide && !isEditing && <Button size="sm" icon={Edit3} onClick={() => setIsEditing(true)}>Edit</Button>}
            {hasGuide && <Button size="sm" variant="ghost" icon={copied ? Check : Copy} onClick={handleCopy}>{copied ? 'Copied' : 'Copy'}</Button>}
            <Button size="sm" variant={hasGuide ? 'secondary' : 'primary'} icon={Sparkles} disabled={busy} onClick={hasGuide ? startEnhance : startBuild}>
                {hasGuide ? enhanceLabel : buildLabel}
            </Button>
        </>
    );

    return (
        <div className="space-y-4">
            <BootstrapNotice project={project} onBuild={startBuild} building={busy} buildLabel={buildLabel} />

            {running && (
                <Notice tone="accent" icon={Loader2} title={running.type === 'create_context' ? 'Building the context guide…' : 'Enhancing the context guide…'}
                    actions={<span className="text-xs tabular-nums">{Math.round(running.progress || 0)}%</span>}>
                    {running.message || 'This takes a minute or two. You can leave this page; it carries on.'}
                </Notice>
            )}

            {reviewing && (
                <Notice tone="success" icon={Sparkles} title="Review the new context guide"
                    actions={(
                        <>
                            <Button size="sm" icon={RotateCcw} onClick={handleRevert}>Discard</Button>
                            <Button size="sm" variant="primary" icon={Save} loading={isSaving} onClick={handleSave}>Save</Button>
                        </>
                    )}>
                    It is loaded in the editor below; edit anything, then save to keep it.
                </Notice>
            )}

            <Card title="Context guide"
                subtitle={bootstrapCaption(project) || 'Background the translator reads before every episode: story, setting, tone, who speaks how.'}
                actions={actions}>
                {!hasGuide && !isEditing ? (
                    <EmptyState icon={Compass} title="No context guide yet"
                        description="It is built automatically before the first episode of a new title. You can also build it now or write it yourself."
                        action={(
                            <div className="flex flex-wrap justify-center gap-2">
                                <Button variant="primary" icon={Sparkles} disabled={busy} onClick={startBuild}>{buildLabel}</Button>
                                <Button icon={Edit3} onClick={() => setIsEditing(true)}>Write it myself</Button>
                            </div>
                        )} />
                ) : isEditing ? (
                    <div className="space-y-3">
                        <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={18}
                            placeholder="Story so far, setting, tone, how characters address each other…"
                            className="block w-full resize-y rounded-lg border border-gray-250 bg-white p-3 font-mono text-xs leading-relaxed text-gray-900 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/30 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100" />
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="text-xs text-gray-500 dark:text-gray-400">{words} words · {content.length} characters</span>
                            <div className="flex gap-2">
                                <Button size="sm" icon={Eye} onClick={handleRevert}>Cancel</Button>
                                <Button size="sm" variant="primary" icon={Save} loading={isSaving} onClick={handleSave}>Save</Button>
                            </div>
                        </div>
                    </div>
                ) : (
                    <div>
                        <div className="whitespace-pre-wrap rounded-lg bg-gray-50 p-4 text-sm leading-relaxed text-gray-800 dark:bg-gray-900/40 dark:text-gray-200">{content}</div>
                        <div className="mt-2 text-xs text-gray-500 dark:text-gray-400">{words} words</div>
                    </div>
                )}
            </Card>

            <Card padded={false}>
                <button type="button" onClick={() => setSummariesOpen(o => !o)} aria-expanded={summariesOpen}
                    className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left sm:px-5">
                    <span>
                        <span className="block text-sm font-semibold text-gray-900 dark:text-white">Episode summaries</span>
                        <span className="block text-xs text-gray-500 dark:text-gray-400">Written after each episode so the next one knows what happened.</span>
                    </span>
                    {summariesOpen ? <ChevronDown size={18} className="text-gray-400" /> : <ChevronRight size={18} className="text-gray-400" />}
                </button>
                {summariesOpen && (
                    <div className="border-t border-gray-100 p-4 dark:border-gray-750 sm:p-5">
                        <EpisodeSummariesPanel projectName={projectName} projectSettings={project?.settings} embedded />
                    </div>
                )}
            </Card>
        </div>
    );
};

export default ContextTab;
