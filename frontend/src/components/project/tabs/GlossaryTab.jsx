import { useState } from 'react';
import { Loader2, Sparkles } from 'lucide-react';
import { api } from '../../../api';
import { Button, Card, Notice } from '../../ui';
import GlossaryEditor from '../../GlossaryEditor';
import GlossaryReviewModal from '../../GlossaryReviewModal';
import CharacterProfilesPanel from '../../CharacterProfilesPanel';
import BootstrapNotice, { bootstrapCaption } from '../BootstrapNotice';
import { useProjectJob } from '../useProjectJob';
import { errorText } from '../../../hooks/useTranslate';

const JOB_TYPES = ['create_glossary', 'enhance_glossary'];

const GlossaryTab = ({ project, projectName, engine, onAiAction, onReload, toast }) => {
    const [isSaving, setIsSaving] = useState(false);
    const [reviewCandidates, setReviewCandidates] = useState(null);

    const agent = engine !== 'model';
    const glossary = project?.glossary || { show_name: projectName, terms: [] };
    const parentProject = project?.parent_project || null;
    const suppressedTerms = project?.suppressed_glossary_terms || [];
    const isEmpty = (glossary.terms || []).length === 0;
    const model = agent ? null : (project?.settings?.glossary_model || null);

    const running = useProjectJob(JOB_TYPES, projectName, {
        onCompleted: (job) => {
            const candidates = job.result?.terms || job.result?.new_terms || [];
            // Agent engine: "build" merges its terms into the glossary itself; only "enhance" returns candidates.
            const needsReview = job.type === 'enhance_glossary' || !agent;
            if (needsReview && candidates.length > 0) {
                setReviewCandidates(candidates);
                toast?.info?.(`${candidates.length} suggested terms. Review them and keep the ones you want.`);
            } else {
                toast?.success?.(job.type === 'create_glossary' ? 'Glossary updated.' : 'No new terms found.');
                onReload?.(true);
            }
        },
        onFailed: (job) => toast?.error?.(job.message || 'The glossary job failed.'),
    });

    const handleSaveGlossary = async (newGlossary, globalSave = false, newSuppressed = null) => {
        setIsSaving(true);
        try {
            await api.updateGlossary(projectName, newGlossary, globalSave, newSuppressed !== null ? newSuppressed : suppressedTerms);
            toast?.success?.('Glossary saved.');
            onReload?.(true);
        } catch (err) {
            console.error("Failed to save glossary", err);
            toast?.error?.(errorText(err, 'Could not save the glossary.'));
        } finally {
            setIsSaving(false);
        }
    };

    const startBuild = () => onAiAction('create_glossary', model);
    const startEnhance = () => onAiAction('enhance_glossary', model, { data: glossary, enableResearch: !agent });

    const handleMergeCandidates = async (acceptedCandidates) => {
        if (!acceptedCandidates || acceptedCandidates.length === 0) {
            setReviewCandidates(null);
            return;
        }
        const currentTerms = glossary.terms || [];
        const existingNames = new Set(currentTerms.map(t => (t.term || '').toLowerCase()));
        const uniqueAccepted = acceptedCandidates.filter(t => !existingNames.has((t.term || '').toLowerCase()));
        await handleSaveGlossary({ ...glossary, terms: [...uniqueAccepted, ...currentTerms] }, false, suppressedTerms);
        setReviewCandidates(null);
    };

    const buildLabel = agent ? 'Build with agy' : 'Generate with AI';
    const enhanceLabel = agent ? 'Enhance with agy' : 'Enhance with AI';

    return (
        <div className="space-y-4">
            <BootstrapNotice project={project} onBuild={startBuild} building={!!running} buildLabel={buildLabel} />

            {running && (
                <Notice tone="accent" icon={Loader2} title={running.type === 'create_glossary' ? 'Building the glossary…' : 'Looking for new terms…'}
                    actions={<span className="text-xs tabular-nums">{Math.round(running.progress || 0)}%</span>}>
                    {running.message || 'This takes a minute or two. You can leave this page; it carries on.'}
                </Notice>
            )}

            <Card padded={false} title="Glossary"
                subtitle={parentProject
                    ? `Shares terms with the universe "${parentProject}".`
                    : (bootstrapCaption(project) || 'Names and terms the translator must render the same way every time.')}
                actions={(
                    <Button size="sm" variant={isEmpty ? 'primary' : 'secondary'} icon={Sparkles} disabled={!!running} onClick={isEmpty ? startBuild : startEnhance}>
                        {isEmpty ? buildLabel : enhanceLabel}
                    </Button>
                )}>
                <GlossaryEditor
                    embedded
                    glossary={glossary}
                    onSave={handleSaveGlossary}
                    isSaving={isSaving}
                    projectName={projectName}
                    parentProject={parentProject}
                    suppressedTerms={suppressedTerms}
                />
            </Card>

            <CharacterProfilesPanel projectName={projectName} projectSettings={project?.settings} />

            {reviewCandidates && (
                <GlossaryReviewModal
                    isOpen={!!reviewCandidates}
                    onClose={() => setReviewCandidates(null)}
                    newTerms={reviewCandidates}
                    existingTerms={glossary.terms || []}
                    onConfirm={handleMergeCandidates}
                />
            )}
        </div>
    );
};

export default GlossaryTab;
