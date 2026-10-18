import React, { useState, useEffect, useRef } from 'react';
import { 
    Compass, 
    Sparkles, 
    Globe, 
    Save, 
    Copy, 
    Check, 
    Eye, 
    Edit3, 
    FileText, 
    Info, 
    Loader2,
    RotateCcw
} from 'lucide-react';
import { api } from '../../../api';
import { useJobs } from '../../../context/JobContext';

const ContextTab = ({
    project,
    projectName,
    onAiAction,
    onReload,
    toast,
}) => {
    const { activeJobs, removeJob } = useJobs();
    const [content, setContent] = useState('');
    const [isEditing, setIsEditing] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [copied, setCopied] = useState(false);
    const [aiResultBanner, setAiResultBanner] = useState(false);
    const reviewedJobIdsRef = useRef(new Set());

    useEffect(() => {
        if (!aiResultBanner) {
            setContent(project?.context_guide || '');
        }
    }, [project?.context_guide, aiResultBanner]);

    // Find active AI job currently running for context
    const activeContextJob = Object.values(activeJobs || {}).find(j =>
        j && ['create_context', 'enhance_context'].includes(j.type) &&
        (j.id?.includes(projectName) || j.project_name === projectName || j.description?.includes(projectName)) &&
        j.status === 'running'
    );

    // Watch for completed context AI job to auto-populate the editor for review & save
    useEffect(() => {
        if (!activeJobs) return;
        Object.values(activeJobs).forEach(job => {
            if (
                job &&
                ['create_context', 'enhance_context'].includes(job.type) &&
                (job.id?.includes(projectName) || job.project_name === projectName || job.description?.includes(projectName)) &&
                job.status === 'completed' &&
                !reviewedJobIdsRef.current.has(job.id)
            ) {
                reviewedJobIdsRef.current.add(job.id);
                const generatedGuide = job.result?.context_guide || job.ai_response;
                if (generatedGuide) {
                    setContent(generatedGuide);
                    setIsEditing(true);
                    setAiResultBanner(true);
                    if (toast?.info) toast.info("AI Context Guide ready for review and editing!");
                }
                if (removeJob) {
                    removeJob(job.id);
                }
            }
        });
    }, [activeJobs, projectName, removeJob, toast]);


    const handleSave = async () => {
        setIsSaving(true);
        try {
            await api.updateProject(projectName, { context_guide: content });
            if (toast?.success) toast.success("Context Guide saved successfully!");
            setAiResultBanner(false);
            if (onReload) onReload();
            setIsEditing(false);
        } catch (err) {
            console.error("Failed to save context guide", err);
            if (toast?.error) toast.error("Failed to save context guide.");
        } finally {
            setIsSaving(false);
        }
    };

    const handleRevert = () => {
        setContent(project?.context_guide || '');
        setAiResultBanner(false);
        setIsEditing(false);
        if (toast?.info) toast.info("Reverted to previous context guide.");
    };


    const handleAiGenerate = async () => {
        const model = project?.settings?.context_model || null;
        await onAiAction('create_context', model);
    };

    const handleAiEnhance = async () => {
        const model = project?.settings?.context_model || null;
        await onAiAction('enhance_context', model);
    };

    const handleCopy = () => {
        if (!content) return;
        navigator.clipboard.writeText(content);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
        if (toast?.info) toast.info("Context Guide copied to clipboard.");
    };

    const wordCount = content.trim() ? content.trim().split(/\s+/).length : 0;
    const charCount = content.length;

    return (
        <div className="space-y-6">
            {/* Active AI Job Banner */}
            {activeContextJob && activeContextJob.status === 'running' && (
                <div className="bg-indigo-50/80 dark:bg-indigo-950/40 border border-indigo-200 dark:border-indigo-800/60 rounded-2xl p-4 flex items-center justify-between gap-4">
                    <div className="flex items-center gap-3">
                        <Loader2 className="w-5 h-5 text-indigo-600 dark:text-indigo-400 animate-spin flex-shrink-0" />
                        <div>
                            <p className="text-xs font-bold text-indigo-900 dark:text-indigo-200">
                                {activeContextJob.type === 'create_context' ? 'Generating Context Guide with AI...' : 'Enhancing Context Guide with AI...'}
                            </p>
                            <p className="text-[11px] text-indigo-600/80 dark:text-indigo-400/80 mt-0.5">
                                {activeContextJob.message || activeContextJob.logs?.[activeContextJob.logs.length - 1] || 'Researching series lore and drafting world rules...'}
                            </p>
                        </div>
                    </div>
                    <span className="text-xs font-bold text-indigo-600 dark:text-indigo-400">
                        {Math.round(activeContextJob.progress || 0)}%
                    </span>
                </div>
            )}

            {/* Freshly Generated AI Result Review Banner */}
            {aiResultBanner && (
                <div className="bg-emerald-50/90 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800/60 rounded-2xl p-4 flex flex-wrap items-center justify-between gap-4">
                    <div className="flex items-center gap-3">
                        <Sparkles className="w-5 h-5 text-emerald-600 dark:text-emerald-400 flex-shrink-0" />
                        <div>
                            <p className="text-xs font-bold text-emerald-900 dark:text-emerald-200">
                                AI Generated Context Guide Ready for Review
                            </p>
                            <p className="text-[11px] text-emerald-700/80 dark:text-emerald-300/80 mt-0.5">
                                The text below has been loaded into the editor. You can edit any details and click "Save Context Guide" to accept.
                            </p>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        <button
                            type="button"
                            onClick={handleRevert}
                            className="px-3 py-1.5 bg-white dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-200 rounded-xl text-xs font-semibold border border-gray-200 dark:border-gray-700 transition-colors flex items-center gap-1.5"
                        >
                            <RotateCcw size={13} /> Revert
                        </button>
                        <button
                            type="button"
                            onClick={handleSave}
                            disabled={isSaving}
                            className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 shadow-sm transition-colors disabled:opacity-50"
                        >
                            {isSaving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                            Save Context Guide
                        </button>
                    </div>
                </div>
            )}

            {/* Top Toolbar Card */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-5 border border-gray-200 dark:border-gray-700 shadow-sm flex flex-wrap items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                    <div className="p-2.5 bg-indigo-50 dark:bg-indigo-950/60 text-indigo-600 dark:text-indigo-400 rounded-xl">
                        <Compass size={22} />
                    </div>
                    <div>
                        <h3 className="text-base font-bold text-gray-900 dark:text-white">Project Context Guide</h3>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                            Lore, world-building background, tone constraints, and character relationships provided to the AI.
                        </p>
                    </div>
                </div>

                <div className="flex items-center gap-2 flex-wrap">
                    {content && (
                        <>
                            <button
                                type="button"
                                onClick={() => setIsEditing(!isEditing)}
                                className={`px-3.5 py-1.5 rounded-xl text-xs font-bold flex items-center gap-1.5 transition-colors border ${
                                    isEditing
                                        ? 'bg-indigo-50 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300 border-indigo-200 dark:border-indigo-800'
                                        : 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-200 border-transparent hover:bg-gray-200 dark:hover:bg-gray-650'
                                }`}
                            >
                                {isEditing ? <Eye size={14} /> : <Edit3 size={14} />}
                                {isEditing ? 'Preview' : 'Edit Guide'}
                            </button>

                            <button
                                type="button"
                                onClick={handleCopy}
                                className="px-3 py-1.5 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-650 text-gray-700 dark:text-gray-200 rounded-xl text-xs font-bold flex items-center gap-1.5 transition-colors"
                                title="Copy Context to Clipboard"
                            >
                                {copied ? <Check size={14} className="text-emerald-500" /> : <Copy size={14} />}
                                {copied ? 'Copied' : 'Copy'}
                            </button>
                        </>
                    )}

                    <button
                        type="button"
                        onClick={handleAiGenerate}
                        disabled={activeContextJob && activeContextJob.status === 'running'}
                        className="px-3.5 py-1.5 bg-indigo-50 dark:bg-indigo-950/60 hover:bg-indigo-100 dark:hover:bg-indigo-900/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800 rounded-xl text-xs font-bold flex items-center gap-1.5 transition-colors disabled:opacity-50"
                    >
                        <Sparkles size={14} /> AI Generate Context
                    </button>

                    <button
                        type="button"
                        onClick={handleAiEnhance}
                        disabled={activeContextJob && activeContextJob.status === 'running'}
                        className="px-3.5 py-1.5 bg-purple-50 dark:bg-purple-950/60 hover:bg-purple-100 dark:hover:bg-purple-900/60 text-purple-700 dark:text-purple-300 border border-purple-200 dark:border-purple-800 rounded-xl text-xs font-bold flex items-center gap-1.5 transition-colors disabled:opacity-50"
                    >
                        <Globe size={14} /> AI Enhance
                    </button>


                    {isEditing && (
                        <button
                            type="button"
                            onClick={handleSave}
                            disabled={isSaving}
                            className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 shadow-sm transition-colors disabled:opacity-50"
                        >
                            {isSaving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                            Save
                        </button>
                    )}
                </div>
            </div>

            {/* Content Area */}
            {!content && !isEditing ? (
                <div className="bg-white dark:bg-gray-800 rounded-3xl p-12 border border-gray-200 dark:border-gray-700 text-center shadow-sm space-y-4">
                    <div className="w-16 h-16 bg-indigo-50 dark:bg-indigo-950/60 rounded-2xl flex items-center justify-center mx-auto text-indigo-600 dark:text-indigo-400">
                        <Compass size={32} />
                    </div>
                    <div className="max-w-md mx-auto space-y-1">
                        <h4 className="text-base font-bold text-gray-900 dark:text-white">No Context Guide Generated Yet</h4>
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                            A Context Guide provides the LLM with background story knowledge, setting details, honorifics, and character speech styles for consistent, accurate subtitles.
                        </p>
                    </div>
                    <div className="flex items-center justify-center gap-3 pt-2">
                        <button
                            type="button"
                            onClick={handleAiGenerate}
                            className="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold flex items-center gap-2 shadow-sm transition-all"
                        >
                            <Sparkles size={14} /> Generate with AI
                        </button>
                        <button
                            type="button"
                            onClick={() => setIsEditing(true)}
                            className="px-4 py-2.5 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-650 text-gray-800 dark:text-gray-200 rounded-xl text-xs font-bold flex items-center gap-1.5 transition-all"
                        >
                            <Edit3 size={14} /> Write Manually
                        </button>
                    </div>
                </div>
            ) : (
                <div className="bg-white dark:bg-gray-800 rounded-3xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden flex flex-col">
                    {/* Editor / Viewer Controls Header */}
                    <div className="px-5 py-3 bg-gray-50 dark:bg-gray-850 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
                        <div className="flex items-center gap-2">
                            <FileText size={14} className="text-indigo-500" />
                            <span className="font-semibold text-gray-700 dark:text-gray-300">
                                {isEditing ? 'Editing Context Guide (Markdown)' : 'Project Context Overview'}
                            </span>
                        </div>
                        <div className="flex items-center gap-3 font-mono text-[11px]">
                            <span>{wordCount} words</span>
                            <span>&middot;</span>
                            <span>{charCount} characters</span>
                        </div>
                    </div>

                    {/* Main Content Body */}
                    <div className="p-6">
                        {isEditing ? (
                            <div className="space-y-4">
                                <textarea
                                    value={content}
                                    onChange={(e) => setContent(e.target.value)}
                                    placeholder="Enter series context, character dynamics, setting lore, tone conventions, and translation rules..."
                                    rows={18}
                                    className="w-full p-4 rounded-2xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-900 font-mono text-xs leading-relaxed text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-indigo-500 focus:bg-white dark:focus:bg-gray-900 outline-none transition-all resize-y"
                                />
                                <div className="flex items-center justify-between pt-2">
                                    <span className="text-[11px] text-gray-400">
                                        Supports standard Markdown formatting. Click Save when finished.
                                    </span>
                                    <div className="flex items-center gap-2">
                                        <button
                                            type="button"
                                            onClick={() => {
                                                setContent(project?.context_guide || '');
                                                setIsEditing(false);
                                            }}
                                            className="px-4 py-2 text-xs font-semibold text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 rounded-xl"
                                        >
                                            Cancel
                                        </button>
                                        <button
                                            type="button"
                                            onClick={handleSave}
                                            disabled={isSaving}
                                            className="px-5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 shadow-sm transition-all disabled:opacity-50"
                                        >
                                            {isSaving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                                            Save Changes
                                        </button>
                                    </div>
                                </div>
                            </div>
                        ) : (
                            <div className="prose dark:prose-invert max-w-none text-xs leading-relaxed text-gray-800 dark:text-gray-200 whitespace-pre-wrap font-sans space-y-3 bg-gray-50/40 dark:bg-gray-900/30 p-6 rounded-2xl border border-gray-100 dark:border-gray-750">
                                {content}
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
};

export default ContextTab;
