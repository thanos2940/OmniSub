import React from 'react';
import { motion } from 'framer-motion';
import { FileText, Book, Languages, Check, Loader2, Circle, Pause, Play, X } from 'lucide-react';

const STAGES = [
    { id: 'context', label: 'Context Guide', icon: FileText },
    { id: 'glossary', label: 'Glossary', icon: Book },
    { id: 'translate', label: 'Translate', icon: Languages },
];

const PipelineStepper = ({ job, onPause, onResume, onCancel, onContinue }) => {
    if (!job) return null;

    const currentStage = job.pipeline_stage || 'context';
    const currentStageIdx = STAGES.findIndex(s => s.id === currentStage);
    const isAwaitingReview = job.status === 'awaiting_review';
    const isRunning = job.status === 'running';
    const isCancelled = job.status === 'cancelled';
    const isCompleted = job.status === 'completed';
    const isFailed = job.status === 'failed';
    const isDone = isCompleted || isCancelled || isFailed;

    const getStageStatus = (idx) => {
        if (idx < currentStageIdx) return 'completed';
        if (idx === currentStageIdx) {
            if (isCompleted) return 'completed';
            if (isAwaitingReview) return 'review';
            if (isRunning) return 'active';
            if (isFailed) return 'failed';
            return 'active';
        }
        return 'pending';
    };

    const statusColors = {
        completed: 'bg-green-500 text-white border-green-500',
        active: 'bg-indigo-500 text-white border-indigo-500 animate-pulse',
        review: 'bg-amber-500 text-white border-amber-500',
        failed: 'bg-red-500 text-white border-red-500',
        pending: 'bg-gray-200 dark:bg-gray-700 text-gray-400 border-gray-300 dark:border-gray-600',
    };

    const lineColors = {
        completed: 'bg-green-500',
        active: 'bg-indigo-300',
        review: 'bg-amber-300',
        failed: 'bg-red-300',
        pending: 'bg-gray-300 dark:bg-gray-600',
    };

    return (
        <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5 shadow-sm"
        >
            {/* Stepper */}
            <div className="flex items-center justify-center gap-0 mb-4">
                {STAGES.map((stage, idx) => {
                    const status = getStageStatus(idx);
                    const Icon = stage.icon;
                    const StatusIcon = status === 'completed' ? Check :
                        status === 'active' ? Loader2 :
                            status === 'review' ? Pause :
                                Circle;

                    return (
                        <React.Fragment key={stage.id}>
                            {idx > 0 && (
                                <div className={`h-0.5 w-12 mx-1 rounded-full transition-colors ${lineColors[status]}`} />
                            )}
                            <div className="flex flex-col items-center gap-1.5">
                                <div className={`w-10 h-10 rounded-full flex items-center justify-center border-2 transition-all ${statusColors[status]}`}>
                                    {status === 'completed' ? (
                                        <Check size={18} />
                                    ) : status === 'active' ? (
                                        <Loader2 size={18} className="animate-spin" />
                                    ) : (
                                        <Icon size={18} />
                                    )}
                                </div>
                                <span className={`text-xs font-medium ${status === 'pending' ? 'text-gray-400' : 'text-gray-700 dark:text-gray-200'}`}>
                                    {stage.label}
                                </span>
                            </div>
                        </React.Fragment>
                    );
                })}
            </div>

            {/* Progress Bar */}
            <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-1.5 mb-3">
                <motion.div
                    className={`h-1.5 rounded-full ${isFailed ? 'bg-red-500' : isCancelled ? 'bg-gray-400' : 'bg-indigo-500'}`}
                    initial={{ width: 0 }}
                    animate={{ width: `${job.progress || 0}%` }}
                    transition={{ duration: 0.5 }}
                />
            </div>

            {/* Status & Controls */}
            <div className="flex items-center justify-between">
                <div className="flex-1 min-w-0">
                    <p className={`text-sm font-medium truncate ${isFailed ? 'text-red-500' : 'text-gray-700 dark:text-gray-200'}`}>
                        {job.message}
                    </p>
                    {job.progress > 0 && !isDone && (
                        <p className="text-xs text-gray-400 mt-0.5">{Math.round(job.progress)}% complete</p>
                    )}
                </div>

                <div className="flex items-center gap-2 ml-3 shrink-0">
                    {isAwaitingReview && onContinue && (
                        <button
                            onClick={onContinue}
                            className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 transition-colors"
                        >
                            <Play size={14} /> Continue
                        </button>
                    )}
                    {(isRunning || isAwaitingReview) && onCancel && (
                        <button
                            onClick={onCancel}
                            className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-300 dark:hover:bg-gray-600 transition-colors"
                        >
                            <X size={14} /> Cancel
                        </button>
                    )}
                </div>
            </div>
        </motion.div>
    );
};

export default PipelineStepper;
