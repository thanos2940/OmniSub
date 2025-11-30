import React, { useState, useEffect, useRef } from 'react';
import { Terminal, X, ChevronRight, ChevronLeft, RefreshCw, AlertCircle, CheckCircle, Clock } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { api } from '../api';

const AILogPanel = ({ activeJobId, onClose, onComplete }) => {
    const [isOpen, setIsOpen] = useState(false);
    const [job, setJob] = useState(null);
    const [isPolling, setIsPolling] = useState(false);
    const pollInterval = useRef(null);

    useEffect(() => {
        if (activeJobId) {
            setIsOpen(true);
            setIsPolling(true);
            fetchJob();
            pollInterval.current = setInterval(fetchJob, 2000);
        } else {
            setIsPolling(false);
            if (pollInterval.current) clearInterval(pollInterval.current);
        }

        return () => {
            if (pollInterval.current) clearInterval(pollInterval.current);
        };
    }, [activeJobId]);

    const fetchJob = async () => {
        if (!activeJobId) return;
        try {
            const res = await api.getJob(activeJobId);
            setJob(res.data);
            if (['completed', 'failed'].includes(res.data.status)) {
                setIsPolling(false);
                if (pollInterval.current) clearInterval(pollInterval.current);
                if (res.data.status === 'completed' && onComplete) {
                    onComplete(res.data);
                }
            }
        } catch (err) {
            console.error("Failed to fetch job", err);
        }
    };

    if (!activeJobId && !isOpen) return null;

    return (
        <motion.div
            initial={{ x: '100%' }}
            animate={{ x: isOpen ? '0%' : '100%' }}
            transition={{ type: 'spring', damping: 25, stiffness: 200 }}
            className="fixed top-16 right-0 bottom-0 w-96 bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-700 shadow-xl z-40 flex flex-col"
        >
            {/* Header */}
            <div className="p-4 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center bg-gray-50 dark:bg-gray-800">
                <div className="flex items-center gap-2">
                    <Terminal size={18} className="text-indigo-500" />
                    <h3 className="font-bold text-gray-900 dark:text-white">AI Activity Log</h3>
                </div>
                <div className="flex items-center gap-2">
                    {isPolling && <RefreshCw size={14} className="animate-spin text-gray-400" />}
                    <button onClick={() => setIsOpen(false)} className="text-gray-500 hover:text-gray-700">
                        <ChevronRight size={20} />
                    </button>
                </div>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4 font-mono text-sm">
                {!job ? (
                    <div className="text-center text-gray-500 py-8">Waiting for job data...</div>
                ) : (
                    <>
                        {/* Status Card */}
                        <div className={`p-3 rounded-lg border ${job.status === 'completed' ? 'bg-green-50 border-green-200 text-green-800' :
                            job.status === 'failed' ? 'bg-red-50 border-red-200 text-red-800' :
                                'bg-blue-50 border-blue-200 text-blue-800'
                            }`}>
                            <div className="flex items-center gap-2 mb-1">
                                {job.status === 'completed' ? <CheckCircle size={16} /> :
                                    job.status === 'failed' ? <AlertCircle size={16} /> :
                                        <Clock size={16} className="animate-pulse" />}
                                <span className="font-bold uppercase text-xs">{job.status}</span>
                            </div>
                            <p className="text-xs opacity-80">{job.message}</p>
                            {job.progress > 0 && (
                                <div className="w-full bg-gray-200 rounded-full h-1.5 mt-2">
                                    <div className="bg-current h-1.5 rounded-full transition-all duration-500" style={{ width: `${job.progress}%` }}></div>
                                </div>
                            )}
                        </div>

                        {/* Logs */}
                        <div className="space-y-1">
                            <h4 className="text-xs font-bold text-gray-500 uppercase mb-2">Execution Log</h4>
                            {job.logs.map((log, i) => (
                                <div key={i} className="text-xs text-gray-600 dark:text-gray-400 border-l-2 border-gray-200 pl-2 py-0.5">
                                    {log}
                                </div>
                            ))}
                        </div>

                        {/* Prompt */}
                        {job.prompt && (
                            <div className="mt-4">
                                <h4 className="text-xs font-bold text-gray-500 uppercase mb-2">Prompt Sent</h4>
                                <div className="bg-gray-100 dark:bg-gray-800 p-3 rounded-lg text-xs overflow-x-auto whitespace-pre-wrap max-h-60 border border-gray-200 dark:border-gray-700">
                                    {job.prompt}
                                </div>
                            </div>
                        )}

                        {/* Response */}
                        {job.ai_response && (
                            <div className="mt-4">
                                <h4 className="text-xs font-bold text-gray-500 uppercase mb-2">AI Response</h4>
                                <div className="bg-gray-100 dark:bg-gray-800 p-3 rounded-lg text-xs overflow-x-auto whitespace-pre-wrap max-h-60 border border-gray-200 dark:border-gray-700">
                                    {job.ai_response}
                                </div>
                            </div>
                        )}
                    </>
                )}
            </div>
        </motion.div>
    );
};

export default AILogPanel;
