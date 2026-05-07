import React, { useState } from 'react';
import { useJobs } from '../context/JobContext';
import { X, ChevronDown, ChevronUp, Activity, CheckCircle, AlertCircle, Terminal, ExternalLink, Sparkles } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';

const JobProgressWidget = () => {
    const { activeJobs, removeJob, cancelJob } = useJobs();
    const [isExpanded, setIsExpanded] = useState(true);
    const [showLogs, setShowLogs] = useState(null); // jobId to show logs for
    const navigate = useNavigate();

    const jobs = Object.values(activeJobs);
    // Don't show pipeline jobs here (they have their own stepper)
    const nonPipelineJobs = jobs.filter(j => j.type !== 'pipeline');
    if (nonPipelineJobs.length === 0) return null;

    const isActive = (job) => ['running', 'pending', 'awaiting_review'].includes(job.status);

    return (
        <div className="fixed bottom-4 right-4 z-50 w-96 flex flex-col gap-2">
            <AnimatePresence>
                {isExpanded && (
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: 20 }}
                        className="bg-white dark:bg-gray-800 rounded-lg shadow-2xl border border-gray-200 dark:border-gray-700 overflow-hidden max-h-[80vh] flex flex-col"
                    >
                        <div className="p-3 bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center">
                            <h3 className="font-semibold text-sm text-gray-700 dark:text-gray-300 flex items-center gap-2">
                                <Activity size={16} className="text-indigo-500" />
                                Active Tasks ({nonPipelineJobs.length})
                            </h3>
                            <button onClick={() => setIsExpanded(false)} className="text-gray-500 hover:text-gray-700">
                                <ChevronDown size={16} />
                            </button>
                        </div>

                        <div className="overflow-y-auto p-2 space-y-2">
                            {nonPipelineJobs.map(job => (
                                <div key={job.id} className="bg-gray-50 dark:bg-gray-700/50 rounded-lg p-3 border border-gray-100 dark:border-gray-600">
                                    <div className="flex justify-between items-start mb-2">
                                        <div>
                                            <p className="font-medium text-sm text-gray-800 dark:text-gray-200">
                                                {job.metadata?.episodeName ? (
                                                    <span className="flex flex-col">
                                                        <span className="text-[10px] uppercase tracking-wider text-indigo-500 font-bold">{job.metadata.projectId}</span>
                                                        <span>{job.metadata.episodeName}</span>
                                                    </span>
                                                ) : job.description}
                                            </p>
                                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{job.message}</p>
                                        </div>
                                        <div className="flex items-center gap-2">
                                            {job.status === 'completed' && <CheckCircle size={16} className="text-green-500" />}
                                            {job.status === 'failed' && <AlertCircle size={16} className="text-red-500" />}
                                            {job.status === 'cancelled' && <X size={16} className="text-gray-400" />}
                                            {job.status === 'running' && (
                                                <div className="relative">
                                                    <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                                                    {job.logs.some(l => l.includes("Reasoning:")) && (
                                                        <Sparkles size={10} className="absolute -top-1 -right-1 text-yellow-500 animate-pulse" />
                                                    )}
                                                </div>
                                            )}
                                            {isActive(job) && (
                                                <button onClick={() => cancelJob(job.id)} className="flex items-center gap-1 text-xs text-red-400 hover:text-red-600 font-medium px-2 py-1 rounded bg-red-50 dark:bg-red-900/20" title="Cancel Job">
                                                    Cancel
                                                </button>
                                            )}
                                            <button onClick={() => removeJob(job.id)} className="text-gray-400 hover:text-gray-600 ml-1" title="Dismiss">
                                                <X size={16} />
                                            </button>
                                        </div>
                                    </div>

                                    {/* Progress Bar */}
                                    <div className="w-full bg-gray-200 dark:bg-gray-600 rounded-full h-1.5 mb-2 overflow-hidden">
                                        <div
                                            className={`h-full rounded-full transition-all duration-500 ${job.status === 'failed' ? 'bg-red-500' :
                                                job.status === 'completed' ? 'bg-green-500' : 'bg-indigo-500'
                                                }`}
                                            style={{ width: `${job.progress}%` }}
                                        />
                                    </div>

                                    <div className="flex items-center justify-between mt-1">
                                        {/* Logs Toggle */}
                                        <button
                                            onClick={() => setShowLogs(showLogs === job.id ? null : job.id)}
                                            className="text-xs text-indigo-600 hover:text-indigo-800 flex items-center gap-1"
                                        >
                                            <Terminal size={12} />
                                            {showLogs === job.id ? 'Hide Logs' : 'Show Logs'}
                                        </button>

                                        {/* Navigation Link */}
                                        {job.metadata?.link && (
                                            <button
                                                onClick={() => navigate(job.metadata.link, { state: { highlightJobId: job.id } })}
                                                className="text-xs text-blue-600 hover:text-blue-800 flex items-center gap-1"
                                            >
                                                <ExternalLink size={12} />
                                                View
                                            </button>
                                        )}
                                    </div>

                                    {/* Scenes Progress */}
                                    {job.scene_status && Object.keys(job.scene_status).length > 0 && (
                                        <div className="mt-2 flex flex-wrap gap-1">
                                            {Object.entries(job.scene_status).map(([sceneName, status]) => {
                                                let bgColor = 'bg-gray-200 text-gray-600 dark:bg-gray-600 dark:text-gray-300';
                                                if (status === 'running') bgColor = 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 border border-blue-400';
                                                if (status === 'completed') bgColor = 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300';
                                                
                                                return (
                                                    <span key={sceneName} className={`text-[10px] px-1.5 py-0.5 rounded ${bgColor}`}>
                                                        {sceneName.replace('Scene ', 'S')}
                                                    </span>
                                                );
                                            })}
                                        </div>
                                    )}

                                    {/* Logs View */}
                                    <AnimatePresence>
                                        {showLogs === job.id && (
                                            <motion.div
                                                initial={{ height: 0, opacity: 0 }}
                                                animate={{ height: 'auto', opacity: 1 }}
                                                exit={{ height: 0, opacity: 0 }}
                                                className="mt-2 bg-black text-green-400 p-2 rounded text-xs font-mono overflow-hidden"
                                            >
                                                <div className="max-h-32 overflow-y-auto space-y-1">
                                                    {job.logs.slice().reverse().map((log, i) => (
                                                        <div key={i}>{log}</div>
                                                    ))}
                                                </div>
                                            </motion.div>
                                        )}
                                    </AnimatePresence>
                                </div>
                            ))}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {!isExpanded && (
                <motion.button
                    initial={{ scale: 0.9, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    onClick={() => setIsExpanded(true)}
                    className="bg-white dark:bg-gray-800 p-3 rounded-full shadow-lg border border-gray-200 dark:border-gray-700 flex items-center gap-2 self-end hover:bg-gray-50 transition-colors"
                >
                    <Activity size={20} className="text-indigo-500" />
                    <span className="font-semibold text-sm text-gray-700 dark:text-gray-300">{jobs.length}</span>
                </motion.button>
            )}
        </div>
    );
};

export default JobProgressWidget;
