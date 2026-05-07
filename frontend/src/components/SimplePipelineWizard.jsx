import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
    Sparkles, 
    Search, 
    Languages, 
    CheckCircle2, 
    ChevronRight, 
    Loader2, 
    Download,
    AlertCircle,
    Edit3
} from 'lucide-react';
import { api } from '../api';

const SimplePipelineWizard = ({ projectName, onComplete, onCancel }) => {
    const [job, setJob] = useState(null);
    const [isPolling, setIsPolling] = useState(false);
    const [editedContent, setEditedContent] = useState("");
    const [editedGlossary, setEditedGlossary] = useState({ terms: [] });
    const [episodes, setEpisodes] = useState([]);
    const [isConfirming, setIsConfirming] = useState(false);
    
    const pollingRef = useRef(null);

    // Initial load and job polling
    useEffect(() => {
        startPipeline();
        fetchEpisodes();
        return () => stopPolling();
    }, []);

    const fetchEpisodes = async () => {
        try {
            const res = await api.getEpisodes(projectName);
            setEpisodes(res.data);
        } catch (e) {
            console.error("Failed to fetch episodes", e);
        }
    };

    const startPipeline = async () => {
        try {
            const res = await api.startSimplePipeline(projectName);
            const jobId = res.data.job_id;
            startPolling(jobId);
        } catch (e) {
            console.error("Failed to start simple pipeline", e);
        }
    };

    const startPolling = (jobId) => {
        setIsPolling(true);
        pollingRef.current = setInterval(async () => {
            try {
                const res = await api.getJob(jobId);
                const currentJob = res.data;
                setJob(currentJob);

                if (currentJob.status === 'completed') {
                    stopPolling();
                    if (onComplete) onComplete();
                } else if (currentJob.status === 'failed') {
                    stopPolling();
                } else if (currentJob.status === 'awaiting_review') {
                    // Update editors with fresh content if they are empty
                    if (currentJob.pipeline_stage === 'analyze' && currentJob.result?.context_guide && !editedContent) {
                        setEditedContent(currentJob.result.context_guide);
                    }
                    if (currentJob.pipeline_stage === 'glossary' && currentJob.result?.glossary && !editedGlossary.terms.length) {
                        setEditedGlossary(currentJob.result.glossary);
                    }
                }
                
                // Refresh episode list during translation stage to show 'translated' status
                if (currentJob.pipeline_stage === 'translate') {
                    fetchEpisodes();
                }
            } catch (e) {
                console.error("Polling error", e);
                stopPolling();
                
                // If backend restarted or job was lost, handle UI gracefully instead of getting stuck
                setJob(prev => prev ? { 
                    ...prev, 
                    status: 'failed', 
                    message: e.response?.status === 404 
                        ? 'Backend restarted. The translation job was lost.' 
                        : 'Connection to server lost.'
                } : null);
            }
        }, 1500);
    };

    const stopPolling = () => {
        if (pollingRef.current) {
            clearInterval(pollingRef.current);
            pollingRef.current = null;
        }
        setIsPolling(false);
    };

    const handleConfirmContext = async () => {
        setIsConfirming(true);
        try {
            await api.confirmPipelineContext(projectName, job.id, editedContent);
            // Result will clear, polling will continue and move to next stage
            setEditedContent(""); 
        } catch (e) {
            console.error("Confirmation error", e);
        } finally {
            setIsConfirming(false);
        }
    };

    const handleConfirmGlossary = async () => {
        setIsConfirming(true);
        try {
            await api.confirmPipelineGlossary(projectName, job.id, editedGlossary);
            setEditedGlossary({ terms: [] });
        } catch (e) {
            console.error("Confirmation error", e);
        } finally {
            setIsConfirming(false);
        }
    };

    const handleDownload = async (episodeName) => {
        try {
            await api.downloadEpisode(projectName, episodeName);
        } catch (e) {
            console.error("Download failed", e);
        }
    };

    if (!job) return (
        <div className="flex flex-col items-center justify-center p-12 text-gray-500">
            <Loader2 className="animate-spin mb-4 text-indigo-500" size={32} />
            <p>Initializing translation pipeline...</p>
        </div>
    );

    const stages = [
        { id: 'analyze', label: 'Analysis', icon: Sparkles },
        { id: 'glossary', label: 'Glossary', icon: Search },
        { id: 'translate', label: 'Translation', icon: Languages },
    ];

    return (
        <div className="bg-white dark:bg-gray-800 rounded-3xl shadow-xl border border-gray-100 dark:border-gray-700 overflow-hidden">
            {/* Header Stepper */}
            <div className="bg-gray-50 dark:bg-gray-900/50 px-8 py-6 flex items-center justify-between border-b border-gray-100 dark:border-gray-700">
                <div className="flex items-center gap-6">
                    {stages.map((s, i) => {
                        const isActive = job.pipeline_stage === s.id;
                        const isDone = stages.findIndex(st => st.id === job.pipeline_stage) > i;
                        return (
                            <div key={s.id} className="flex items-center gap-3">
                                <div className={`w-10 h-10 rounded-full flex items-center justify-center transition-all ${
                                    isActive ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-500/30' :
                                    isDone ? 'bg-green-500 text-white' : 'bg-gray-200 dark:bg-gray-700 text-gray-400'
                                }`}>
                                    {isDone ? <CheckCircle2 size={20} /> : <s.icon size={20} />}
                                </div>
                                <span className={`text-sm font-semibold ${
                                    isActive ? 'text-indigo-600 dark:text-indigo-400' :
                                    isDone ? 'text-green-600 dark:text-green-400' : 'text-gray-400'
                                }`}>{s.label}</span>
                                {i < stages.length - 1 && <ChevronRight size={16} className="text-gray-300 mx-2" />}
                            </div>
                        );
                    })}
                </div>
                <button 
                    onClick={onCancel}
                    className="text-gray-400 hover:text-red-500 transition-colors p-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800"
                >
                    Cancel pipeline
                </button>
            </div>

            <div className="p-8">
                {/* Content based on stage */}
                <AnimatePresence mode="wait">
                    {job.status === 'awaiting_review' && job.pipeline_stage === 'analyze' && (
                        <motion.div 
                            key="analyze-review"
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y:0 }}
                            exit={{ opacity: 0, y: -10 }}
                            className="space-y-6"
                        >
                            <div className="flex items-center justify-between">
                                <div>
                                    <h3 className="text-xl font-bold flex items-center gap-2">
                                        <Edit3 size={20} className="text-indigo-500" />
                                        Review Context Guide
                                    </h3>
                                    <p className="text-gray-500 text-sm">The AI generated these instructions based on project analysis. You can edit them below.</p>
                                </div>
                                <button 
                                    onClick={handleConfirmContext}
                                    disabled={isConfirming}
                                    className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-3 rounded-2xl font-bold transition-all shadow-lg shadow-indigo-500/20 flex items-center gap-2 disabled:opacity-50"
                                >
                                    {isConfirming ? <Loader2 className="animate-spin" size={18} /> : <CheckCircle2 size={18} />}
                                    Confirm & Continue
                                </button>
                            </div>
                            <textarea
                                value={editedContent}
                                onChange={(e) => setEditedContent(e.target.value)}
                                className="w-full h-80 p-6 rounded-2xl border border-gray-200 dark:border-gray-700 dark:bg-gray-900 bg-gray-50 font-mono text-sm resize-none focus:ring-2 focus:ring-indigo-500 outline-none"
                            />
                        </motion.div>
                    )}

                    {job.status === 'awaiting_review' && job.pipeline_stage === 'glossary' && (
                        <motion.div 
                            key="glossary-review"
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y:0 }}
                            exit={{ opacity: 0, y: -10 }}
                            className="space-y-6"
                        >
                            <div className="flex items-center justify-between">
                                <div>
                                    <h3 className="text-xl font-bold flex items-center gap-2">
                                        <Search size={20} className="text-indigo-500" />
                                        Review Glossary Terms
                                    </h3>
                                    <p className="text-gray-500 text-sm">Review the terms extracted from the start, middle and end of your project.</p>
                                </div>
                                <button 
                                    onClick={handleConfirmGlossary}
                                    disabled={isConfirming}
                                    className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-3 rounded-2xl font-bold transition-all shadow-lg shadow-indigo-500/20 flex items-center gap-2 disabled:opacity-50"
                                >
                                    {isConfirming ? <Loader2 className="animate-spin" size={18} /> : <CheckCircle2 size={18} />}
                                    Confirm & Start Translation
                                </button>
                            </div>
                            
                            <div className="max-h-[500px] overflow-auto rounded-2xl border border-gray-100 dark:border-gray-700">
                                <table className="w-full text-left text-sm">
                                    <thead className="bg-gray-50 dark:bg-gray-900/50 sticky top-0">
                                        <tr>
                                            <th className="px-4 py-3 font-semibold">Term</th>
                                            <th className="px-4 py-3 font-semibold">Translation</th>
                                            <th className="px-4 py-3 font-semibold">Description</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                                        {editedGlossary.terms.map((term, i) => (
                                            <tr key={i} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                                                <td className="px-4 py-3 font-medium">{term.term}</td>
                                                <td className="px-4 py-3">
                                                    <input 
                                                        type="text"
                                                        value={term.translation}
                                                        onChange={(e) => {
                                                            const newTerms = [...editedGlossary.terms];
                                                            newTerms[i].translation = e.target.value;
                                                            setEditedGlossary({ ...editedGlossary, terms: newTerms });
                                                        }}
                                                        className="bg-transparent border-b border-dashed border-gray-300 dark:border-gray-600 focus:border-indigo-500 outline-none w-full"
                                                    />
                                                </td>
                                                <td className="px-4 py-3 text-gray-500">{term.description}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </motion.div>
                    )}

                    {job.status === 'running' && (
                        <motion.div 
                            key="running-status"
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            className="flex flex-col items-center justify-center py-20 text-center"
                        >
                            <div className="relative mb-8">
                                <div className="absolute inset-0 bg-indigo-500/20 rounded-full animate-ping blur-xl" />
                                <div className="relative bg-white dark:bg-gray-800 p-6 rounded-full shadow-2xl border-4 border-indigo-500">
                                    <Loader2 className="animate-spin text-indigo-500" size={48} />
                                </div>
                            </div>
                            <h3 className="text-2xl font-bold mb-2">{job.message}</h3>
                            <div className="w-full max-w-md bg-gray-100 dark:bg-gray-700 h-3 rounded-full overflow-hidden mt-6 mb-4">
                                <motion.div 
                                    className="h-full bg-indigo-500 shadow-[0_0_20px_rgba(99,102,241,0.5)]"
                                    initial={{ width: 0 }}
                                    animate={{ width: `${job.progress}%` }}
                                    transition={{ duration: 0.5 }}
                                />
                            </div>
                            <p className="text-gray-500">{Math.round(job.progress)}% complete</p>
                            
                            {job.pipeline_stage === 'translate' && (
                                <div className="mt-12 w-full grid grid-cols-2 gap-4 max-h-40 overflow-y-auto px-4">
                                    {episodes.map(ep => (
                                        <div key={ep.name} className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-900/50 rounded-xl border border-gray-100 dark:border-gray-700">
                                            <span className="text-xs truncate max-w-[120px]">{ep.name}</span>
                                            {ep.translated ? (
                                                <button 
                                                    onClick={() => handleDownload(ep.name)}
                                                    className="p-1.5 text-green-600 hover:bg-green-50 dark:hover:bg-green-900/20 rounded-lg transition-colors"
                                                >
                                                    <Download size={14} />
                                                </button>
                                            ) : (
                                                <div className="w-4 h-4 rounded-full border-2 border-gray-200 border-t-indigo-500 animate-spin" />
                                            )}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </motion.div>
                    )}

                    {job.status === 'failed' && (
                        <motion.div 
                            key="failed-status"
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            className="flex flex-col items-center justify-center py-20 text-center"
                        >
                            <div className="bg-red-50 dark:bg-red-900/20 p-6 rounded-full mb-6">
                                <AlertCircle size={64} className="text-red-500" />
                            </div>
                            <h3 className="text-2xl font-bold mb-2 text-red-600">Pipeline Failed</h3>
                            <p className="text-gray-500 mb-8">{job.message}</p>
                            <button 
                                onClick={startPipeline}
                                className="bg-indigo-600 hover:bg-indigo-700 text-white px-8 py-3 rounded-2xl font-bold transition-all"
                            >
                                Retry pipeline
                            </button>
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>
        </div>
    );
};

export default SimplePipelineWizard;
