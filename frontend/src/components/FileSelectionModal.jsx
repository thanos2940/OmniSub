import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, FileText, Check, AlertCircle } from 'lucide-react';

const FileSelectionModal = ({ isOpen, onClose, onConfirm, episodes, title, description, confirmLabel = "Start AI Task" }) => {
    const [selectedFiles, setSelectedFiles] = useState(new Set());

    const toggleFile = (fileName) => {
        const newSelected = new Set(selectedFiles);
        if (newSelected.has(fileName)) {
            newSelected.delete(fileName);
        } else {
            newSelected.add(fileName);
        }
        setSelectedFiles(newSelected);
    };

    const handleSelectAll = () => {
        if (selectedFiles.size === episodes.length) {
            setSelectedFiles(new Set());
        } else {
            setSelectedFiles(new Set(episodes.map(e => e.name)));
        }
    };

    const handleConfirm = () => {
        onConfirm(Array.from(selectedFiles));
    };

    return (
        <AnimatePresence>
            {isOpen && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
                    <motion.div
                        initial={{ opacity: 0, scale: 0.95 }}
                        animate={{ opacity: 1, scale: 1 }}
                        exit={{ opacity: 0, scale: 0.95 }}
                        className="bg-white dark:bg-gray-800 rounded-xl shadow-xl max-w-lg w-full overflow-hidden flex flex-col max-h-[80vh]"
                    >
                        <div className="p-6 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center">
                            <div>
                                <h3 className="text-xl font-bold text-gray-900 dark:text-white">{title}</h3>
                                <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">{description}</p>
                            </div>
                            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200">
                                <X size={24} />
                            </button>
                        </div>

                        <div className="p-4 bg-blue-50 dark:bg-blue-900/20 border-b border-blue-100 dark:border-blue-800/30 flex items-start gap-3">
                            <AlertCircle className="text-blue-600 dark:text-blue-400 shrink-0 mt-0.5" size={18} />
                            <p className="text-sm text-blue-700 dark:text-blue-300">
                                Select files to use as source material. If no files are selected, the AI will use <strong>pure research mode</strong> based on the project title.
                            </p>
                        </div>

                        <div className="p-2 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center bg-gray-50 dark:bg-gray-900/50">
                            <button
                                onClick={handleSelectAll}
                                className="text-sm font-medium text-indigo-600 hover:text-indigo-700 px-4 py-2"
                            >
                                {selectedFiles.size === episodes.length ? 'Deselect All' : 'Select All'}
                            </button>
                            <span className="text-sm text-gray-500 px-4">
                                {selectedFiles.size} selected
                            </span>
                        </div>

                        <div className="flex-1 overflow-y-auto p-2 space-y-1">
                            {episodes.map(ep => (
                                <div
                                    key={ep.name}
                                    onClick={() => toggleFile(ep.name)}
                                    className={`flex items-center justify-between p-3 rounded-lg cursor-pointer transition-colors ${selectedFiles.has(ep.name)
                                            ? 'bg-indigo-50 dark:bg-indigo-900/30 border border-indigo-200 dark:border-indigo-800'
                                            : 'hover:bg-gray-50 dark:hover:bg-gray-700 border border-transparent'
                                        }`}
                                >
                                    <div className="flex items-center gap-3">
                                        <div className={`p-2 rounded-lg ${selectedFiles.has(ep.name) ? 'bg-indigo-100 text-indigo-600' : 'bg-gray-100 text-gray-500'}`}>
                                            <FileText size={18} />
                                        </div>
                                        <div>
                                            <p className={`font-medium ${selectedFiles.has(ep.name) ? 'text-indigo-900 dark:text-indigo-200' : 'text-gray-700 dark:text-gray-300'}`}>
                                                {ep.name}
                                            </p>
                                            <p className="text-xs text-gray-500">{ep.line_count} lines</p>
                                        </div>
                                    </div>
                                    {selectedFiles.has(ep.name) && (
                                        <div className="bg-indigo-600 text-white p-1 rounded-full">
                                            <Check size={14} />
                                        </div>
                                    )}
                                </div>
                            ))}
                            {episodes.length === 0 && (
                                <div className="text-center py-8 text-gray-500">
                                    No files available. Proceeding will use research mode only.
                                </div>
                            )}
                        </div>

                        <div className="p-6 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50 flex justify-end gap-3">
                            <button
                                onClick={onClose}
                                className="px-4 py-2 text-gray-600 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200 font-medium"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleConfirm}
                                className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-2 rounded-lg transition-colors font-medium shadow-sm flex items-center gap-2"
                            >
                                <motion.span layout>{confirmLabel}</motion.span>
                                {selectedFiles.size === 0 && <span className="text-xs opacity-80">(Research Only)</span>}
                            </button>
                        </div>
                    </motion.div>
                </div>
            )}
        </AnimatePresence>
    );
};

export default FileSelectionModal;
