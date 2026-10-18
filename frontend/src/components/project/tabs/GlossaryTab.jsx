import React, { useState, useEffect, useRef } from 'react';
import { Sparkles, Book, Globe, Loader2, CheckCircle2 } from 'lucide-react';
import { api } from '../../../api';
import { useJobs } from '../../../context/JobContext';
import GlossaryEditor from '../../GlossaryEditor';
import GlossaryReviewModal from '../../GlossaryReviewModal';

const GlossaryTab = ({
    project,
    projectName,
    onAiAction,
    onReload,
    toast,
}) => {
    const { activeJobs, removeJob } = useJobs();
    const [isSaving, setIsSaving] = useState(false);
    const [reviewCandidates, setReviewCandidates] = useState(null);
    const reviewedJobIdsRef = useRef(new Set());

    const glossary = project?.glossary || { show_name: projectName, terms: [] };
    const parentProject = project?.parent_project || null;
    const suppressedTerms = project?.suppressed_glossary_terms || [];

    // Find active glossary AI job currently running for this project
    const activeGlossaryJob = Object.values(activeJobs || {}).find(j => 
        j && ['create_glossary', 'enhance_glossary'].includes(j.type) &&
        (j.id?.includes(projectName) || j.project_name === projectName || j.description?.includes(projectName)) &&
        j.status === 'running'
    );

    // When an AI glossary job finishes, automatically pop up the GlossaryReviewModal
    useEffect(() => {
        if (!activeJobs) return;
        Object.values(activeJobs).forEach(job => {
            if (
                job &&
                ['create_glossary', 'enhance_glossary'].includes(job.type) &&
                (job.id?.includes(projectName) || job.project_name === projectName || job.description?.includes(projectName)) &&
                job.status === 'completed' &&
                !reviewedJobIdsRef.current.has(job.id)
            ) {
                reviewedJobIdsRef.current.add(job.id);
                const candidates = job.result?.terms || job.result?.new_terms || [];
                if (candidates.length > 0) {
                    setReviewCandidates(candidates);
                    if (toast?.info) toast.info(`AI generated ${candidates.length} terms. Review and merge below.`);
                }
                if (removeJob) {
                    removeJob(job.id);
                }
            }
        });
    }, [activeJobs, projectName, removeJob, toast]);


    const handleSaveGlossary = async (newGlossary, globalSave = false, newSuppressed = null) => {
        setIsSaving(true);
        try {
            await api.updateGlossary(
                projectName,
                newGlossary,
                globalSave,
                newSuppressed !== null ? newSuppressed : suppressedTerms
            );
            if (toast?.success) toast.success("Glossary saved successfully!");
            if (onReload) onReload();
        } catch (err) {
            console.error("Failed to save glossary", err);
            if (toast?.error) toast.error("Failed to save glossary.");
        } finally {
            setIsSaving(false);
        }
    };

    const handleAiGenerate = async () => {
        const model = project?.settings?.glossary_model || null;
        await onAiAction('create_glossary', model);
    };

    const handleAiEnhance = async () => {
        const model = project?.settings?.glossary_model || null;
        await onAiAction('enhance_glossary', model, { data: glossary, enableResearch: true });
    };

    const handleMergeCandidates = async (acceptedCandidates) => {
        if (!acceptedCandidates || acceptedCandidates.length === 0) {
            setReviewCandidates(null);
            return;
        }
        const currentTerms = glossary.terms || [];
        const existingNames = new Set(currentTerms.map(t => (t.term || '').toLowerCase()));
        const uniqueAccepted = acceptedCandidates.filter(t => !existingNames.has((t.term || '').toLowerCase()));
        const merged = [...uniqueAccepted, ...currentTerms];
        await handleSaveGlossary({ ...glossary, terms: merged }, false, suppressedTerms);
        setReviewCandidates(null);
    };


    return (
        <div className="space-y-6">
            {/* Active AI Job Banner */}
            {activeGlossaryJob && activeGlossaryJob.status === 'running' && (
                <div className="bg-indigo-50/80 dark:bg-indigo-950/40 border border-indigo-200 dark:border-indigo-800/60 rounded-2xl p-4 flex items-center justify-between gap-4">
                    <div className="flex items-center gap-3">
                        <Loader2 className="w-5 h-5 text-indigo-600 dark:text-indigo-400 animate-spin flex-shrink-0" />
                        <div>
                            <p className="text-xs font-bold text-indigo-900 dark:text-indigo-200">
                                {activeGlossaryJob.type === 'create_glossary' ? 'Generating Glossary with AI...' : 'Enhancing Glossary with Web Research...'}
                            </p>
                            <p className="text-[11px] text-indigo-600/80 dark:text-indigo-400/80 mt-0.5">
                                {activeGlossaryJob.message || activeGlossaryJob.logs?.[activeGlossaryJob.logs.length - 1] || 'Analyzing subtitles and show lore...'}
                            </p>
                        </div>
                    </div>
                    <span className="text-xs font-bold text-indigo-600 dark:text-indigo-400">
                        {Math.round(activeGlossaryJob.progress || 0)}%
                    </span>
                </div>
            )}

            {/* AI Assistant Quick Bar */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-4 border border-gray-200 dark:border-gray-700 shadow-sm flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                    <Book className="text-indigo-600 dark:text-indigo-400" size={20} />
                    <div>
                        <h3 className="text-sm font-bold text-gray-900 dark:text-white">Project Terminology Glossary</h3>
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                            {parentProject ? `Inheriting terms from parent universe "${parentProject}"` : 'Standalone project glossary'}
                        </p>
                    </div>
                </div>

                <div className="flex items-center gap-2">
                    <button
                        type="button"
                        onClick={handleAiGenerate}
                        disabled={activeGlossaryJob && activeGlossaryJob.status === 'running'}
                        className="px-3.5 py-1.5 bg-indigo-50 dark:bg-indigo-950/50 hover:bg-indigo-100 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800 rounded-xl text-xs font-bold flex items-center gap-1.5 transition-colors disabled:opacity-50"
                    >
                        <Sparkles size={14} /> AI Generate Glossary
                    </button>
                    <button
                        type="button"
                        onClick={handleAiEnhance}
                        disabled={activeGlossaryJob && activeGlossaryJob.status === 'running'}
                        className="px-3.5 py-1.5 bg-purple-50 dark:bg-purple-950/50 hover:bg-purple-100 text-purple-700 dark:text-purple-300 border border-purple-200 dark:border-purple-800 rounded-xl text-xs font-bold flex items-center gap-1.5 transition-colors disabled:opacity-50"
                    >
                        <Globe size={14} /> AI Enhance with Web Research
                    </button>
                </div>
            </div>

            {/* Glossary Editor */}
            <GlossaryEditor
                glossary={glossary}
                onSave={handleSaveGlossary}
                isSaving={isSaving}
                projectName={projectName}
                parentProject={parentProject}
                suppressedTerms={suppressedTerms}
            />

            {/* Glossary Candidate Review Modal */}
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

