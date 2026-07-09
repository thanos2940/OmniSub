import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Check, Trash2 } from 'lucide-react';

const ContextReviewModal = ({ isOpen, onClose, onConfirm, newContext, currentContext, onDelete }) => {
    if (!isOpen) return null;

    const hasCurrentContext = currentContext && currentContext.trim().length > 0;

    return (
        <AnimatePresence>
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
                <motion.div
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl max-w-5xl w-full max-h-[85vh] flex flex-col"
                >
                    {/* Header */}
                    <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
                        <div>
                            <h2 className="text-xl font-bold text-gray-900 dark:text-white">
                                Review Enhanced Context Guide
                            </h2>
                            <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                                Review the AI-enhanced context guide before applying
                            </p>
                        </div>
                        <button
                            onClick={onClose}
                            className="p-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                        >
                            <X size={20} />
                        </button>
                    </div>

                    {/* Content */}
                    <div className="flex-1 overflow-y-auto p-6">
                        {hasCurrentContext ? (
                            <div className="grid grid-cols-2 gap-6">
                                {/* Current */}
                                <div>
                                    <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3 flex items-center gap-2">
                                        <div className="w-2 h-2 rounded-full bg-gray-400"></div>
                                        Current Context Guide
                                    </h3>
                                    <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-4 border border-gray-200 dark:border-gray-700 max-h-[500px] overflow-y-auto">
                                        <p className="text-sm text-gray-600 dark:text-gray-400 whitespace-pre-wrap">
                                            {currentContext}
                                        </p>
                                    </div>
                                </div>

                                {/* Enhanced */}
                                <div>
                                    <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3 flex items-center gap-2">
                                        <div className="w-2 h-2 rounded-full bg-blue-500"></div>
                                        Enhanced Context Guide
                                    </h3>
                                    <div className="bg-blue-50 dark:bg-blue-900/20 rounded-lg p-4 border-2 border-blue-500 max-h-[500px] overflow-y-auto">
                                        <p className="text-sm text-gray-900 dark:text-gray-100 whitespace-pre-wrap">
                                            {newContext}
                                        </p>
                                    </div>
                                </div>
                            </div>
                        ) : (
                            <div>
                                <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3 flex items-center gap-2">
                                    <div className="w-2 h-2 rounded-full bg-blue-500"></div>
                                    New Context Guide
                                </h3>
                                <div className="bg-blue-50 dark:bg-blue-900/20 rounded-lg p-4 border-2 border-blue-500">
                                    <p className="text-sm text-gray-900 dark:text-gray-100 whitespace-pre-wrap">
                                        {newContext}
                                    </p>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Footer */}
                    <div className="px-6 py-4 border-t border-gray-200 dark:border-gray-700 flex justify-between items-center">
                        <p className="text-sm text-gray-500 dark:text-gray-400">
                            {hasCurrentContext ? 'Replace current guide with enhanced version?' : 'Apply this context guide?'}
                        </p>
                        <div className="flex gap-3">
                            {onDelete && (
                                <button
                                    onClick={() => {
                                        if (window.confirm("Delete this AI response? You'll be able to request a new one.")) {
                                            onDelete();
                                            onClose();
                                        }
                                    }}
                                    className="px-4 py-2 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors font-medium flex items-center gap-2"
                                    title="Delete this response and request a new one"
                                >
                                    <Trash2 size={16} />
                                    Delete Response
                                </button>
                            )}
                            <button
                                onClick={onClose}
                                className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors font-medium"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={() => {
                                    onConfirm(newContext);
                                    onClose();
                                }}
                                className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors font-medium flex items-center gap-2"
                            >
                                <Check size={18} />
                                {hasCurrentContext ? 'Replace' : 'Apply'}
                            </button>
                        </div>
                    </div>
                </motion.div>
            </div>
        </AnimatePresence>
    );
};

export default ContextReviewModal;
