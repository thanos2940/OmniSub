import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Bot, X, Folder, Copy, Check, FileText, Sparkles, Terminal, CheckCircle2 } from 'lucide-react';

export default function AgentTaskModal({ agentModalData, onClose }) {
    const [copiedField, setCopiedField] = useState(null);
    const [isSubmitted, setIsSubmitted] = useState(false);
    const [submissionDetails, setSubmissionDetails] = useState(null);

    useEffect(() => {
        const handleSubmitted = (e) => {
            const detail = e.detail || {};
            const matchesId = detail.task_id && detail.task_id === agentModalData?.task_id;
            const matchesEp = (
                detail.project_name === agentModalData?.project_name &&
                (!agentModalData?.episode_name || detail.episode_name === agentModalData?.episode_name)
            );
            if (matchesId || matchesEp) {
                setIsSubmitted(true);
                setSubmissionDetails(detail);
            }
        };

        window.addEventListener('omnisub:agent-submitted', handleSubmitted);
        return () => window.removeEventListener('omnisub:agent-submitted', handleSubmitted);
    }, [agentModalData]);

    if (!agentModalData) return null;

    const handleCopy = (text, field) => {
        if (!text) return;
        navigator.clipboard.writeText(text);
        setCopiedField(field);
        setTimeout(() => setCopiedField(null), 2500);
    };

    const isBatch = Boolean(
        agentModalData.is_batch ||
        agentModalData.enqueued_count !== undefined ||
        (Array.isArray(agentModalData.episodes) && agentModalData.episodes.length > 0)
    );

    const alreadyDone = agentModalData.status === 'nothing_to_translate';

    const chatPromptText = isBatch
        ? `/translate-pending ${agentModalData.project_name || ''}`.trim()
        : `/translate-pending ${agentModalData.project_name || 'Project'}${agentModalData.episode_name ? ` ${agentModalData.episode_name}` : ''}`;

    const submitCommand = agentModalData.submit_command
        || `.venv/Scripts/python.exe scripts/agent_task.py submit ${agentModalData.task_id || '<task_id>'}`;
    const cliCommandText = isBatch
        ? `.venv/Scripts/python.exe scripts/agent_task.py next`
        : submitCommand;

    return (
        <AnimatePresence>
            <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-[100] flex items-center justify-center p-4 md:p-8 font-sans">
                <motion.div 
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="bg-gray-900 text-gray-100 rounded-2xl shadow-2xl border border-purple-500/30 w-full max-w-2xl overflow-hidden flex flex-col max-h-[90vh]"
                >
                    {/* Header */}
                    <div className="p-5 bg-gray-850 border-b border-gray-800 flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <div className="p-2 bg-purple-500/10 border border-purple-500/20 text-purple-400 rounded-xl">
                                <Bot size={22} />
                            </div>
                            <div>
                                <div className="flex items-center gap-2">
                                    <h3 className="text-base font-bold text-white">
                                        {isSubmitted ? 'Translation Submitted & Completed' : (isBatch ? 'Batch Agent Translation Enqueued' : 'Agent Task Workspace')}
                                    </h3>
                                    <span className={`px-2 py-0.5 text-[10px] font-bold rounded-full ${
                                        isSubmitted 
                                            ? 'bg-emerald-900/60 text-emerald-300 border border-emerald-700/50' 
                                            : (isBatch ? 'bg-purple-900/60 text-purple-300 border border-purple-700/50' : 'bg-purple-900/60 text-purple-300 border border-purple-700/50')
                                    }`}>
                                        {isSubmitted || alreadyDone ? 'Completed' : (isBatch ? `${agentModalData.enqueued_count} Episodes` : 'Claimed & Ready')}
                                    </span>
                                </div>
                                <p className="text-xs text-gray-400 mt-0.5">
                                    {agentModalData.project_name}
                                    {!isBatch && agentModalData.episode_name && ` • ${agentModalData.episode_name}`}
                                    {agentModalData.units > 0 && ` · ${agentModalData.units} lines to translate`}
                                    {agentModalData.units > 0 && agentModalData.mode === 'gaps' && ' (missing lines only)'}
                                </p>
                            </div>
                        </div>
                        <button
                            type="button"
                            onClick={onClose}
                            className="text-gray-400 hover:text-white p-1.5 hover:bg-gray-800 rounded-lg transition-colors"
                        >
                            <X size={18} />
                        </button>
                    </div>

                    {/* Body */}
                    <div className="p-6 overflow-y-auto space-y-5 text-sm">
                        {isSubmitted && (
                            <motion.div
                                initial={{ opacity: 0, y: -10 }}
                                animate={{ opacity: 1, y: 0 }}
                                className="bg-emerald-950/40 border border-emerald-500/50 rounded-xl p-4 flex items-center gap-3 text-emerald-200 shadow-lg"
                            >
                                <CheckCircle2 size={24} className="text-emerald-400 shrink-0" />
                                <div>
                                    <div className="font-bold text-emerald-100">Translation Successfully Submitted & Ingested!</div>
                                    <div className="text-xs text-emerald-300/90 mt-0.5">
                                        {submissionDetails?.cues_translated 
                                            ? `${submissionDetails.cues_translated} cues translated, Script Guard verified, and exported to media library.`
                                            : 'The agent task has been completed and project status updated.'}
                                    </div>
                                </div>
                            </motion.div>
                        )}

                        {/* Notice */}
                        <div className="bg-purple-950/30 border border-purple-800/40 rounded-xl p-3.5 text-xs text-purple-200 leading-relaxed">
                            {alreadyDone ? (
                                <>{agentModalData.message}</>
                            ) : isBatch ? (
                                <>Episodes from <strong>{agentModalData.project_name}</strong> are queued for an AI agent (Claude Code, Antigravity). The model worker leaves them alone; the agent claims them one by one, translates only what is missing, and Omnisub validates and exports each one.</>
                            ) : (
                                <>Claimed for an AI agent. The workspace holds a brief (context, story so far, characters, glossary, style rules) and a work file with only the lines that need translating. The agent replaces the English; Omnisub checks it and does everything else.</>
                            )}
                        </div>

                        {/* Workspace Directory (Single Task) */}
                        {agentModalData.workspace_dir && (
                            <div>
                                <div className="flex items-center justify-between mb-1.5">
                                    <span className="text-xs font-semibold text-gray-400 flex items-center gap-1.5">
                                        <Folder size={14} className="text-purple-400" /> Workspace Directory
                                    </span>
                                    <button
                                        type="button"
                                        onClick={() => handleCopy(agentModalData.workspace_dir, 'dir')}
                                        className="text-xs text-purple-400 hover:text-purple-300 flex items-center gap-1 font-medium"
                                    >
                                        {copiedField === 'dir' ? <Check size={12} /> : <Copy size={12} />}
                                        {copiedField === 'dir' ? 'Copied' : 'Copy Path'}
                                    </button>
                                </div>
                                <div className="bg-black/40 border border-gray-800 rounded-lg px-3 py-2 text-xs font-mono text-gray-300 truncate select-all">
                                    {agentModalData.workspace_dir}
                                </div>
                            </div>
                        )}

                        {/* Workspace Files List (Single Task) */}
                        {agentModalData.workspace_dir && (
                            <div className="space-y-2">
                                <span className="text-xs font-semibold text-gray-400">Generated Task Files</span>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs font-mono">
                                    <div className="bg-gray-800/60 border border-gray-700/60 rounded-lg p-2.5 flex items-center gap-2">
                                        <FileText size={15} className="text-indigo-400 shrink-0" />
                                        <div className="truncate">
                                            <div className="text-gray-200 font-semibold truncate">BRIEF.md</div>
                                            <div className="text-[10px] text-gray-400 font-sans">Context, glossary & style rules</div>
                                        </div>
                                    </div>
                                    <div className="bg-gray-800/60 border border-gray-700/60 rounded-lg p-2.5 flex items-center gap-2">
                                        <FileText size={15} className="text-emerald-400 shrink-0" />
                                        <div className="truncate">
                                            <div className="text-gray-200 font-semibold truncate">translation.txt</div>
                                            <div className="text-[10px] text-gray-400 font-sans">
                                                {agentModalData.units ? `${agentModalData.units} lines · ` : ''}{'replace English after "N|"'}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Episodes List (Batch) */}
                        {isBatch && agentModalData.episodes && agentModalData.episodes.length > 0 && (
                            <div className="space-y-2">
                                <span className="text-xs font-semibold text-gray-400">Enqueued Episodes ({agentModalData.episodes.length})</span>
                                <div className="flex flex-wrap gap-1.5 max-h-32 overflow-y-auto p-2 bg-black/30 rounded-xl border border-gray-800">
                                    {agentModalData.episodes.map(ep => (
                                        <span key={ep} className="px-2 py-0.5 bg-gray-800 border border-gray-700 rounded-lg text-xs font-mono text-purple-300">
                                            {ep}
                                        </span>
                                    ))}
                                </div>
                            </div>
                        )}

                        {/* How to Run / Submit */}
                        <div className="space-y-3">
                            <span className="text-xs font-semibold text-gray-400">Agent Execution Options</span>
                            
                            {/* Antigravity Assistant */}
                            <div className="bg-gray-800/40 border border-gray-800 rounded-xl p-3.5 space-y-1.5">
                                <div className="text-xs font-bold text-indigo-300 flex items-center gap-1.5">
                                    <Sparkles size={14} /> Option 1: Ask your agent (Claude Code / Antigravity)
                                </div>
                                <p className="text-xs text-gray-400">
                                    Paste this into the agent&rsquo;s chat, opened on the Omnisub folder:
                                </p>
                                <div className="bg-black/50 border border-gray-800 rounded-lg px-3 py-2 text-xs font-mono text-emerald-400 flex items-center justify-between gap-2">
                                    <span className="truncate">{chatPromptText}</span>
                                    <button
                                        type="button"
                                        onClick={() => handleCopy(chatPromptText, 'skill')}
                                        className="text-gray-400 hover:text-white shrink-0"
                                    >
                                        {copiedField === 'skill' ? <Check size={13} /> : <Copy size={13} />}
                                    </button>
                                </div>
                            </div>

                            {/* CLI Helper */}
                            <div className="bg-gray-800/40 border border-gray-800 rounded-xl p-3.5 space-y-1.5">
                                <div className="text-xs font-bold text-amber-300 flex items-center gap-1.5">
                                    <Terminal size={14} /> Option 2: Python CLI
                                </div>
                                <p className="text-xs text-gray-400">
                                    {isBatch
                                        ? "Or claim the next episode yourself from the Omnisub folder:"
                                        : "When translation.txt is done, submit it from the Omnisub folder:"}
                                </p>
                                <div className="bg-black/50 border border-gray-800 rounded-lg px-3 py-2 text-xs font-mono text-gray-300 flex items-center justify-between gap-2">
                                    <span className="truncate">{cliCommandText}</span>
                                    <button
                                        type="button"
                                        onClick={() => handleCopy(cliCommandText, 'cli')}
                                        className="text-gray-400 hover:text-white shrink-0"
                                    >
                                        {copiedField === 'cli' ? <Check size={13} /> : <Copy size={13} />}
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Footer */}
                    <div className="p-4 bg-gray-850 border-t border-gray-800 flex items-center justify-between shrink-0">
                        <span className="text-[11px] text-gray-500">
                            {agentModalData.task_id ? (
                                <>Task ID: <span className="font-mono text-gray-400">{agentModalData.task_id}</span></>
                            ) : (
                                <>Priority: <span className="font-semibold text-purple-400">High (Manual)</span></>
                            )}
                        </span>
                        <div className="flex items-center gap-2">
                            {agentModalData.task_id && (
                                <button
                                    type="button"
                                    onClick={() => handleCopy(submitCommand, 'submit_cmd')}
                                    className="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold rounded-lg flex items-center gap-1.5 transition-colors"
                                >
                                    {copiedField === 'submit_cmd' ? <Check size={13} /> : <Copy size={13} />}
                                    {copiedField === 'submit_cmd' ? 'Copied Command' : 'Copy Submit Command'}
                                </button>
                            )}
                            <button
                                type="button"
                                onClick={onClose}
                                className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-300 text-xs font-semibold rounded-lg transition-colors"
                            >
                                Close
                            </button>
                        </div>
                    </div>
                </motion.div>
            </div>
        </AnimatePresence>
    );
}
